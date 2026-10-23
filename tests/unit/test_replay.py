"""Replay records, stores, drift checks, and synthetic-only dispatch safety."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import pytest

from sentinelmcp.application import ToolExecutor, create_demo_runtime
from sentinelmcp.audit import (
    AuditEventKind,
    AuditRecorder,
    DurableAuditRecorder,
    InMemoryAuditStore,
)
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
    HTTPToolAdapter,
    HTTPToolTarget,
    InMemoryRuntimeBindingRegistry,
    RequestId,
    RuntimeToolBinding,
    SyntheticToolAdapter,
)
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry, fingerprint_tool_definition
from sentinelmcp.replay import (
    FileReplayStore,
    InMemoryReplayStore,
    ReplayAuditUnavailableError,
    ReplayContractDriftError,
    ReplayIntegrityError,
    ReplayNotFoundError,
    ReplayPlan,
    ReplayRecord,
    ReplayRecordId,
    ReplayService,
    ReplayStoreUnavailableError,
    ReplayUnsafeError,
    replay_record_from_document,
    replay_record_to_document,
)

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
RECORD_ID = ReplayRecordId(UUID("40000000-0000-4000-8000-000000000001"))
REQUEST_ID = RequestId(UUID("40000000-0000-4000-8000-000000000002"))
EXECUTION_ID = ExecutionId(UUID("40000000-0000-4000-8000-000000000003"))
TOOL_REF = ToolRef.parse("demo.math.add@1.0.0")
PRINCIPAL = Principal("operator", "urn:sentinelmcp:test")


def test_replay_record_round_trip_is_versioned_and_immutable() -> None:
    record = _record()
    document = replay_record_to_document(record)

    assert replay_record_from_document(document) == record
    source = {"a": 2, "b": 3}
    detached = replace(record, arguments=source)
    source["a"] = 100
    assert detached.arguments["a"] == 2


@pytest.mark.parametrize(
    "arguments",
    [
        {"token": "secret"},
        {"nested": {"authorization": "Bearer value"}},
        {"items": [{"private-key": "value"}]},
    ],
)
def test_replay_record_rejects_sensitive_field_names(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="sensitive field"):
        replace(_record(), arguments=arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("record_id", "bad"),
        ("captured_at", datetime(2026, 1, 1)),
        ("principal_issuer", ""),
        ("principal_subject", "x" * 256),
        ("tool_ref", "bad"),
        ("definition_fingerprint", "bad"),
        ("request_id", "bad"),
        ("execution_id", "bad"),
        ("operation_class", "read"),
    ],
)
def test_replay_record_rejects_invalid_contract_fields(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        replace(_record(), **cast(Any, {field: value}))


def test_replay_identifiers_and_arguments_enforce_runtime_bounds() -> None:
    with pytest.raises(TypeError, match="must be a UUID"):
        ReplayRecordId(cast(Any, "not-a-uuid"))
    with pytest.raises(TypeError, match="must be a string"):
        ReplayRecordId.parse(cast(Any, 7))
    with pytest.raises(ValueError, match="bounded JSON"):
        replace(_record(), arguments={"value": object()})
    with pytest.raises(ValueError, match="size limit"):
        replace(_record(), arguments={"value": "x" * 65_536})


def test_replay_serialization_rejects_extra_and_malformed_fields() -> None:
    document = replay_record_to_document(_record())
    document["result"] = {"not": "allowed"}
    with pytest.raises(ReplayIntegrityError, match="violates schema"):
        replay_record_from_document(document)

    document = replay_record_to_document(_record())
    document["arguments"] = [1, 2]
    with pytest.raises(ReplayIntegrityError, match="violates schema"):
        replay_record_from_document(document)

    with pytest.raises(TypeError, match="ReplayRecord"):
        replay_record_to_document(object())  # type: ignore[arg-type]

    document = replay_record_to_document(_record())
    document["record_id"] = 1
    with pytest.raises(ReplayIntegrityError, match="violates schema"):
        replay_record_from_document(document)

    document = replay_record_to_document(_record())
    document["schema_version"] = True
    with pytest.raises(ReplayIntegrityError, match="violates schema"):
        replay_record_from_document(document)


def test_in_memory_replay_store_is_ordered_idempotent_and_strict() -> None:
    store = InMemoryReplayStore()
    later = replace(_record(), captured_at=NOW + timedelta(seconds=1))
    earlier = replace(
        _record(),
        record_id=ReplayRecordId(UUID("40000000-0000-4000-8000-000000000004")),
    )
    store.append(later)
    store.append(earlier)
    store.append(earlier)

    assert store.get(earlier.record_id) == earlier
    assert store.list_records() == (earlier, later)
    with pytest.raises(ReplayIntegrityError, match="conflicting"):
        store.append(replace(earlier, principal_subject="other"))
    with pytest.raises(ReplayNotFoundError):
        store.get(ReplayRecordId.generate())
    with pytest.raises(TypeError, match="ReplayRecord"):
        store.append(cast(Any, object()))
    with pytest.raises(TypeError, match="ReplayRecordId"):
        store.get(cast(Any, object()))


def test_file_replay_store_persists_and_detects_corruption(tmp_path: Path) -> None:
    root = tmp_path / "replays"
    store = FileReplayStore(root)
    record = _record()
    store.append(record)
    store.append(record)

    restarted = FileReplayStore(tmp_path / "replays")
    assert restarted.get(record.record_id) == record
    assert restarted.list_records() == (record,)
    with pytest.raises(ReplayIntegrityError, match="conflicting"):
        restarted.append(replace(record, principal_subject="different"))

    path = tmp_path / "replays" / f"{record.record_id}.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ReplayIntegrityError, match="strict UTF-8 JSON"):
        restarted.get(record.record_id)


def test_file_replay_store_handles_absence_invalid_names_and_unavailable_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "missing"
    store = FileReplayStore(root)
    assert store.list_records() == ()
    with pytest.raises(ReplayNotFoundError):
        store.get(RECORD_ID)

    root.mkdir()
    (root / "not-a-uuid.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReplayIntegrityError, match="invalid filename"):
        store.list_records()

    unavailable = tmp_path / "file-root"
    unavailable.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ReplayStoreUnavailableError, match="append"):
        FileReplayStore(unavailable).append(_record())


def test_file_replay_store_translates_defensive_file_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TypeError, match=r"pathlib.Path"):
        FileReplayStore(cast(Any, str(tmp_path)))

    root = tmp_path / "replays"
    store = FileReplayStore(root)
    with pytest.raises(TypeError, match="ReplayRecord"):
        store.append(cast(Any, object()))
    with pytest.raises(TypeError, match="ReplayRecordId"):
        store.get(cast(Any, object()))

    root.mkdir()
    record_path = root / f"{RECORD_ID}.json"
    record_path.mkdir()
    with pytest.raises(ReplayStoreUnavailableError, match="lookup"):
        store.get(RECORD_ID)
    with pytest.raises(ReplayStoreUnavailableError, match="append"):
        store.append(_record())

    def fail_open_with_existing_path(_path: Path, _flags: int, _mode: int) -> int:
        raise FileExistsError

    with monkeypatch.context() as context:
        context.setattr(os, "open", fail_open_with_existing_path)
        with pytest.raises(ReplayStoreUnavailableError, match="append"):
            store.append(_record())

    record_path.rmdir()
    record_path.write_bytes(b"")
    with pytest.raises(ReplayIntegrityError, match="size"):
        store.get(RECORD_ID)

    def fail_exists(_path: Path) -> bool:
        raise OSError("unavailable")

    monkeypatch.setattr(Path, "exists", fail_exists)
    with pytest.raises(ReplayStoreUnavailableError, match="list"):
        store.list_records()


def test_file_replay_store_translates_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_sync(_descriptor: int) -> None:
        raise OSError("sync failed")

    monkeypatch.setattr("sentinelmcp.replay.file.os.fsync", fail_sync)
    with pytest.raises(ReplayStoreUnavailableError, match="append"):
        FileReplayStore(tmp_path / "replays").append(_record())


def test_replay_service_validates_dependencies_and_capture_inputs() -> None:
    runtime = create_demo_runtime()
    store = InMemoryReplayStore()
    recorder = DurableAuditRecorder(InMemoryAuditStore())
    invalid = cast(Any, object())

    with pytest.raises(TypeError, match="registry"):
        ReplayService(invalid, runtime.bindings, runtime.executor, store, recorder)
    with pytest.raises(TypeError, match="bindings"):
        ReplayService(runtime.registry, invalid, runtime.executor, store, recorder)
    with pytest.raises(TypeError, match="executor"):
        ReplayService(runtime.registry, runtime.bindings, invalid, store, recorder)
    with pytest.raises(TypeError, match="store"):
        ReplayService(runtime.registry, runtime.bindings, runtime.executor, invalid, recorder)
    with pytest.raises(TypeError, match="audit"):
        ReplayService(runtime.registry, runtime.bindings, runtime.executor, store, invalid)
    with pytest.raises(TypeError, match="target_policy"):
        ReplayService(
            runtime.registry,
            runtime.bindings,
            runtime.executor,
            store,
            recorder,
            target_policy=invalid,
        )

    service = ReplayService(runtime.registry, runtime.bindings, runtime.executor, store, recorder)
    request = ExecutionRequest(TOOL_REF, {"a": 2, "b": 3})
    initial = asyncio.run(runtime.executor.execute(request, principal=PRINCIPAL))
    assert isinstance(initial, ExecutionSuccess)
    assert service.list_records() == ()
    with pytest.raises(TypeError, match="ExecutionRequest"):
        service.capture(invalid, principal=PRINCIPAL, result=initial)
    with pytest.raises(TypeError, match="Principal"):
        service.capture(request, principal=invalid, result=initial)
    with pytest.raises(ReplayUnsafeError, match="successful read"):
        service.capture(request, principal=PRINCIPAL, result=invalid)
    with pytest.raises(ReplayContractDriftError):
        service.capture(
            request,
            principal=PRINCIPAL,
            result=replace(
                initial,
                context=replace(initial.context, tool_ref=ToolRef.parse("demo.profile.get@1.0.0")),
            ),
        )
    with pytest.raises(ReplayContractDriftError):
        service.capture(
            request,
            principal=PRINCIPAL,
            result=replace(
                initial,
                context=replace(initial.context, definition_fingerprint="f" * 64),
            ),
        )


def test_replay_service_rejects_invalid_callers_and_unavailable_targets() -> None:
    runtime = create_demo_runtime()
    store = InMemoryReplayStore()
    store.append(_record())
    service = ReplayService(
        runtime.registry,
        runtime.bindings,
        runtime.executor,
        store,
        DurableAuditRecorder(InMemoryAuditStore()),
    )
    invalid = cast(Any, object())

    with pytest.raises(TypeError, match="Principal"):
        asyncio.run(service.replay(RECORD_ID, principal=invalid))
    with pytest.raises(TypeError, match="boolean"):
        asyncio.run(service.replay(RECORD_ID, principal=PRINCIPAL, dry_run=invalid))

    missing_store = InMemoryReplayStore()
    missing_record = replace(_record(), tool_ref=ToolRef.parse("demo.missing.tool@1.0.0"))
    missing_store.append(missing_record)
    missing_service = ReplayService(
        runtime.registry,
        runtime.bindings,
        runtime.executor,
        missing_store,
        DurableAuditRecorder(InMemoryAuditStore()),
    )
    with pytest.raises(ReplayUnsafeError, match="unavailable"):
        asyncio.run(missing_service.replay(missing_record.record_id, principal=PRINCIPAL))


def test_replay_dry_run_applies_current_authorization() -> None:
    runtime = create_demo_runtime()
    denied_executor = ToolExecutor(
        runtime.registry,
        runtime.bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(()),
    )
    store = InMemoryReplayStore()
    store.append(_record())
    service = ReplayService(
        runtime.registry,
        runtime.bindings,
        denied_executor,
        store,
        DurableAuditRecorder(InMemoryAuditStore()),
    )

    plan = asyncio.run(service.replay(RECORD_ID, principal=PRINCIPAL, dry_run=True))

    assert isinstance(plan, ReplayPlan)
    assert plan.can_execute is False
    assert plan.error_code is ExecutionErrorCode.AUTHORIZATION_FAILED


def test_replay_rejects_unmarked_synthetic_binding() -> None:
    definition = replace(_definition(), capabilities=frozenset({"demo"}))
    registry = InMemoryToolRegistry()
    registry.register(definition)

    async def synthetic(arguments: object) -> object:
        return arguments

    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, SyntheticToolAdapter(synthetic)))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, OperationClass.READ),)
        ),
    )
    store = InMemoryReplayStore()
    record = replace(
        _record(),
        tool_ref=definition.ref,
        definition_fingerprint=fingerprint_tool_definition(definition),
        arguments={"value": "go"},
    )
    store.append(record)
    service = ReplayService(
        registry,
        bindings,
        executor,
        store,
        DurableAuditRecorder(InMemoryAuditStore()),
    )

    with pytest.raises(ReplayUnsafeError, match="not approved"):
        asyncio.run(service.replay(record.record_id, principal=PRINCIPAL))


@pytest.mark.parametrize("guard", [lambda *_args: "yes", lambda *_args: 1])
def test_executor_rejects_non_boolean_binding_guard(guard: Any) -> None:
    runtime = create_demo_runtime()
    result = asyncio.run(
        runtime.executor.execute(
            ExecutionRequest(TOOL_REF, {"a": 2, "b": 3}),
            principal=PRINCIPAL,
            binding_guard=guard,
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR


def test_replay_service_captures_dry_runs_and_executes_through_same_executor() -> None:
    runtime = create_demo_runtime()
    audit_store = InMemoryAuditStore()
    recorder = DurableAuditRecorder(audit_store, wall_clock=lambda: NOW)
    store = InMemoryReplayStore()
    service = ReplayService(
        runtime.registry,
        runtime.bindings,
        runtime.executor,
        store,
        recorder,
        record_id_factory=lambda: RECORD_ID,
        wall_clock=lambda: NOW,
    )
    request = ExecutionRequest(TOOL_REF, {"a": 2, "b": 3})
    initial = asyncio.run(runtime.executor.execute(request, principal=PRINCIPAL))
    record = service.capture(request, principal=PRINCIPAL, result=initial)

    plan = asyncio.run(service.replay(record.record_id, principal=PRINCIPAL, dry_run=True))
    replayed = asyncio.run(service.replay(record.record_id, principal=PRINCIPAL))

    assert isinstance(plan, ReplayPlan)
    assert plan.can_execute is True
    assert isinstance(replayed, ExecutionSuccess)
    assert isinstance(replayed.result, FrozenJSONMapping)
    assert replayed.result["sum"] == 5
    events = audit_store.list_for_request(record.request_id)
    assert [event.kind for event in events] == [
        AuditEventKind.REPLAY_ATTEMPTED,
        AuditEventKind.REPLAY_ATTEMPTED,
    ]


def test_replay_service_detects_contract_drift_and_audit_outage() -> None:
    runtime = create_demo_runtime()
    store = InMemoryReplayStore()
    drifted = replace(_record(), definition_fingerprint="b" * 64)
    store.append(drifted)
    service = ReplayService(
        runtime.registry,
        runtime.bindings,
        runtime.executor,
        store,
        _FailingAuditRecorder(),
    )

    with pytest.raises(ReplayContractDriftError):
        asyncio.run(service.replay(drifted.record_id, principal=PRINCIPAL))

    valid = _record()
    another_store = InMemoryReplayStore()
    another_store.append(valid)
    service = ReplayService(
        runtime.registry,
        runtime.bindings,
        runtime.executor,
        another_store,
        _FailingAuditRecorder(),
    )
    with pytest.raises(ReplayAuditUnavailableError):
        asyncio.run(service.replay(valid.record_id, principal=PRINCIPAL))


def test_replay_binding_guard_blocks_http_swap_before_dispatch() -> None:
    definition = _definition()
    registry = InMemoryToolRegistry()
    registry.register(definition)
    calls: list[str] = []

    async def synthetic(_arguments: object) -> object:
        return {"value": "synthetic"}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append("http")
        return httpx.Response(200, json={"value": "live"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    synthetic_binding = RuntimeToolBinding(definition.ref, SyntheticToolAdapter(synthetic))
    http_binding = RuntimeToolBinding(
        definition.ref,
        HTTPToolAdapter(client, HTTPToolTarget("https://live.example.test/tool")),
    )
    resolver = _SwappingResolver(synthetic_binding, http_binding)
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, OperationClass.READ),)
    )
    executor = ToolExecutor(registry, resolver, authorization_policy=policy)
    store = InMemoryReplayStore()
    record = replace(
        _record(),
        tool_ref=definition.ref,
        definition_fingerprint=fingerprint_tool_definition(definition),
        arguments={"value": "go"},
    )
    store.append(record)
    service = ReplayService(
        registry,
        resolver,
        executor,
        store,
        DurableAuditRecorder(InMemoryAuditStore()),
    )

    result = asyncio.run(service.replay(record.record_id, principal=PRINCIPAL))
    asyncio.run(client.aclose())

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.BINDING_NOT_FOUND
    assert calls == []


class _SwappingResolver:
    def __init__(self, first: RuntimeToolBinding, second: RuntimeToolBinding) -> None:
        self._bindings = iter((first, second))

    def resolve(self, tool_ref: ToolRef) -> RuntimeToolBinding:
        binding = next(self._bindings)
        assert binding.tool_ref == tool_ref
        return binding


class _FailingAuditRecorder(AuditRecorder):
    def record_preview(self, *, principal: Principal, context: ExecutionContext) -> None:
        del principal, context

    def record_intent(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        context: ExecutionContext,
        operation_class: OperationClass,
    ) -> None:
        del kind, principal, context, operation_class
        raise RuntimeError("audit unavailable")

    def record_result(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        attempt_count: int,
    ) -> None:
        del kind, principal, result, operation_class, attempt_count


def _record() -> ReplayRecord:
    return ReplayRecord(
        record_id=RECORD_ID,
        captured_at=NOW,
        principal_issuer=PRINCIPAL.issuer,
        principal_subject=PRINCIPAL.subject,
        tool_ref=TOOL_REF,
        definition_fingerprint=fingerprint_tool_definition(
            create_demo_runtime().registry.get(TOOL_REF)
        ),
        request_id=REQUEST_ID,
        execution_id=EXECUTION_ID,
        operation_class=OperationClass.READ,
        arguments={"a": 2, "b": 3},
    )


def _definition() -> ToolDefinition:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    return ToolDefinition(
        namespace=ToolNamespace("demo.swap"),
        name=ToolName("probe"),
        version=ToolVersion("1.0.0"),
        description="Prove replay checks the final binding.",
        input_schema=schema,
        output_schema=schema,
        operation_class=OperationClass.READ,
        capabilities=frozenset({"demo", "replay-safe"}),
    )
