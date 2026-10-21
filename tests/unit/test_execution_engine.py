"""Tests for the transport-independent Phase 3 executor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sentinelmcp.application import ToolExecutor
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
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionSuccess,
    InMemoryRuntimeBindingRegistry,
    RequestId,
    RuntimeToolBinding,
)
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry, ToolRegistry

TEST_PRINCIPAL = Principal("execution-test", "https://issuer.test")


class RecordingAdapter:
    def __init__(
        self,
        output: object = None,
        *,
        failure: AdapterFailure | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.output = {"value": "ok"} if output is None else output
        self.failure = failure
        self.exception = exception
        self.calls: list[AdapterInvocation] = []

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.calls.append(invocation)
        if self.exception is not None:
            raise self.exception
        if self.failure is not None:
            return self.failure
        return AdapterSuccess(self.output)


class InvalidResultAdapter:
    async def execute(self, _invocation: AdapterInvocation) -> AdapterResult:
        return cast(AdapterResult, object())


class BrokenRegistry:
    def get(self, _tool_ref: ToolRef) -> ToolDefinition:
        raise RuntimeError("private registry detail")


def make_definition(
    *,
    version: str = "1.0.0",
    operation_class: OperationClass = OperationClass.READ,
    input_schema: dict[str, object] | None = None,
    output_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.test"),
        name=ToolName("run"),
        version=ToolVersion(version),
        description="Execute a deterministic test tool.",
        input_schema=input_schema
        or {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        operation_class=operation_class,
    )


def run_execution(executor: ToolExecutor, request: ExecutionRequest) -> object:
    return asyncio.run(executor.execute(request, principal=TEST_PRINCIPAL))


def allow_policy(definition: ToolDefinition) -> RuleBasedAuthorizationPolicy:
    return RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                definition.identity,
                definition.operation_class,
            ),
        )
    )


def setup_executor(
    definition: ToolDefinition,
    adapter: RecordingAdapter | InvalidResultAdapter | None,
) -> tuple[ToolExecutor, InMemoryToolRegistry, InMemoryRuntimeBindingRegistry]:
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    if adapter is not None:
        bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    return (
        ToolExecutor(registry, bindings, authorization_policy=allow_policy(definition)),
        registry,
        bindings,
    )


def test_valid_execution_has_trusted_ids_fingerprint_and_monotonic_duration() -> None:
    definition = make_definition()
    adapter = RecordingAdapter({"value": "done"})
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    ticks = iter((10.0, 10.0, 10.0, 10.125, 10.125))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=allow_policy(definition),
        request_id_factory=lambda: RequestId(UUID("00000000-0000-4000-8000-000000000001")),
        execution_id_factory=lambda: ExecutionId(UUID("00000000-0000-4000-8000-000000000002")),
        wall_clock=lambda: datetime(2026, 1, 2, tzinfo=UTC),
        monotonic=lambda: next(ticks),
    )

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["value"] == "done"
    assert result.duration_ms == 125.0
    assert str(result.context.request_id) == "00000000-0000-4000-8000-000000000001"
    assert str(result.context.execution_id) == "00000000-0000-4000-8000-000000000002"
    assert result.context.definition_fingerprint is not None
    assert result.context.started_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert len(adapter.calls) == 1
    assert adapter.calls[0].arguments["value"] == "go"


def test_invalid_input_never_resolves_to_adapter_execution() -> None:
    definition = make_definition()
    adapter = RecordingAdapter()
    executor, _, _ = setup_executor(definition, adapter)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": 7}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INPUT_VALIDATION_FAILED
    assert result.error.issues[0].instance_path == ("value",)
    assert adapter.calls == []


def test_unknown_exact_version_never_executes_a_newer_binding() -> None:
    registered = make_definition(version="1.1.0")
    adapter = RecordingAdapter()
    executor, _, bindings = setup_executor(registered, adapter)
    requested = ToolRef(
        registered.namespace,
        registered.name,
        ToolVersion("1.0.0"),
    )
    bindings.bind(RuntimeToolBinding(requested, adapter))

    result = run_execution(executor, ExecutionRequest(requested, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.TOOL_NOT_FOUND
    assert result.context.definition_fingerprint is None
    assert adapter.calls == []


def test_valid_input_without_runtime_binding_fails_before_dispatch() -> None:
    definition = make_definition()
    executor, _, _ = setup_executor(definition, None)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.BINDING_NOT_FOUND


def test_mutation_definition_fails_closed_before_adapter_dispatch() -> None:
    definition = make_definition(operation_class=OperationClass.MUTATION)
    adapter = RecordingAdapter()
    executor, _, _ = setup_executor(definition, adapter)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.MUTATION_NOT_SUPPORTED
    assert adapter.calls == []


def test_contract_invalid_output_never_returns_success() -> None:
    definition = make_definition()
    adapter = RecordingAdapter({"value": 7})
    executor, _, _ = setup_executor(definition, adapter)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.OUTPUT_VALIDATION_FAILED
    assert result.error.issues[0].instance_path == ("value",)
    assert len(adapter.calls) == 1


def test_normalized_adapter_failure_preserves_only_safe_status_metadata() -> None:
    definition = make_definition()
    adapter = RecordingAdapter(
        failure=AdapterFailure(
            ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED,
            upstream_status=401,
        )
    )
    executor, _, _ = setup_executor(definition, adapter)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED
    assert result.error.upstream_status == 401
    assert "authentication" in result.error.message


def test_unexpected_adapter_exception_or_result_is_hidden() -> None:
    definition = make_definition()
    exploding = RecordingAdapter(exception=RuntimeError("secret stack detail"))
    executor, _, bindings = setup_executor(definition, exploding)

    first = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))
    bindings.bind(RuntimeToolBinding(definition.ref, InvalidResultAdapter()))
    second = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(first, ExecutionFailure)
    assert isinstance(second, ExecutionFailure)
    assert first.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert second.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert "secret" not in first.error.message


def test_registry_internal_exception_is_normalized() -> None:
    bindings = InMemoryRuntimeBindingRegistry()
    executor = ToolExecutor(
        cast(ToolRegistry, BrokenRegistry()),
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(()),
    )
    request = ExecutionRequest(ToolRef.parse("demo.test.run@1.0.0"), {"value": "go"})

    result = run_execution(executor, request)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert "private" not in result.error.message


def test_adapter_is_invoked_once_without_retry_on_transient_failure() -> None:
    definition = make_definition()
    adapter = RecordingAdapter(failure=AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE))
    executor, _, _ = setup_executor(definition, adapter)

    result = run_execution(executor, ExecutionRequest(definition.ref, {"value": "go"}))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.UPSTREAM_UNAVAILABLE
    assert len(adapter.calls) == 1
