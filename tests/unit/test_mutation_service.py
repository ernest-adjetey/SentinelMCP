"""Mutation orchestration tests around the shared executor lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    RetryPolicy,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
)
from sentinelmcp.mutation_guard import (
    ConfirmationRejectedError,
    ConfirmationRejectionReason,
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyConflictError,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationChallengeRecord,
    MutationPreview,
    MutationStateIntegrityError,
    MutationStateStore,
    MutationStateUnavailableError,
    serialize_execution_result,
)
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
PRINCIPAL = Principal("mutation-user", "https://issuer.test", scopes=frozenset({"tools:write"}))


class RecordingAdapter:
    def __init__(self, result: AdapterResult | None = None) -> None:
        self.calls: list[AdapterInvocation] = []
        self.result = AdapterSuccess({"updated": True}) if result is None else result

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.calls.append(invocation)
        return self.result


class RecordingStore(MutationStateStore):
    def __init__(self) -> None:
        self.challenges: list[MutationChallengeRecord] = []
        self.claim_requests: list[IdempotencyClaimRequest] = []
        self.completions: list[tuple[IdempotencyRecordId, IdempotencyRecordState, bytes]] = []
        self.claim: IdempotencyClaim | Exception | None = None
        self.issue_error: Exception | None = None
        self.complete_error: Exception | None = None

    def issue_challenge(self, record: MutationChallengeRecord) -> None:
        if self.issue_error is not None:
            raise self.issue_error
        self.challenges.append(record)

    def claim_execution(self, request: IdempotencyClaimRequest) -> IdempotencyClaim:
        self.claim_requests.append(request)
        if isinstance(self.claim, Exception):
            raise self.claim
        if self.claim is not None:
            return self.claim
        return IdempotencyClaim(
            IdempotencyClaimStatus.NEW,
            request.record_id,
            IdempotencyRecordState.PENDING,
        )

    def complete_execution(
        self,
        record_id: IdempotencyRecordId,
        *,
        state: IdempotencyRecordState,
        outcome: bytes,
        completed_at: datetime,
    ) -> None:
        del completed_at
        if self.complete_error is not None:
            raise self.complete_error
        self.completions.append((record_id, state, outcome))


def definition(
    operation: OperationClass = OperationClass.MUTATION,
    *,
    retry_policy: RetryPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.profile"),
        name=ToolName("update"),
        version=ToolVersion("1.0.0"),
        description="Update one synthetic profile.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"updated": {"type": "boolean"}},
            "required": ["updated"],
            "additionalProperties": False,
        },
        operation_class=operation,
        retry_policy=RetryPolicy() if retry_policy is None else retry_policy,
        idempotency_supported=True,
    )


def services(
    *,
    with_binding: bool = True,
    operation: OperationClass = OperationClass.MUTATION,
    adapter_result: AdapterResult | None = None,
    retry_policy: RetryPolicy | None = None,
) -> tuple[MutationService, RecordingStore, RecordingAdapter, ToolDefinition]:
    tool = definition(operation, retry_policy=retry_policy)
    registry = InMemoryToolRegistry()
    registry.register(tool)
    adapter = RecordingAdapter(adapter_result)
    bindings = InMemoryRuntimeBindingRegistry()
    if with_binding:
        bindings.bind(RuntimeToolBinding(tool.ref, adapter))
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                tool.identity,
                operation,
                required_scopes=frozenset({"tools:write"}),
            ),
        )
    )
    executor = ToolExecutor(registry, bindings, authorization_policy=policy, wall_clock=lambda: NOW)
    store = RecordingStore()
    service = MutationService(
        executor,
        store,
        wall_clock=lambda: NOW,
        token_factory=lambda: "t" * 43,
    )
    return service, store, adapter, tool


def test_preview_persists_only_digest_and_never_resolves_or_dispatches() -> None:
    service, store, adapter, tool = services(with_binding=False)
    request = ExecutionRequest(tool.ref, {"name": "Ada"})

    result = service.preview(request, principal=PRINCIPAL)

    assert isinstance(result, MutationPreview)
    assert result.confirmation_token == "t" * 43
    assert result.arguments["name"] == "Ada"
    assert len(store.challenges) == 1
    assert store.challenges[0].token_digest != result.confirmation_token.encode()
    assert adapter.calls == []


def test_confirm_claims_dispatches_once_and_persists_success() -> None:
    service, store, adapter, tool = services()
    request = ExecutionRequest(tool.ref, {"name": "Ada"})

    result = asyncio.run(
        service.confirm(
            request,
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionSuccess)
    assert len(store.claim_requests) == 1
    assert len(adapter.calls) == 1
    assert len(store.completions) == 1
    assert store.completions[0][1] is IdempotencyRecordState.SUCCEEDED


def test_confirmation_rejection_and_missing_binding_never_claim_or_dispatch() -> None:
    service, store, adapter, tool = services()
    store.claim = ConfirmationRejectedError(ConfirmationRejectionReason.EXPIRED)
    rejected = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )
    unbound_service, unbound_store, unbound_adapter, unbound_tool = services(with_binding=False)
    unbound = asyncio.run(
        unbound_service.confirm(
            ExecutionRequest(unbound_tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(rejected, ExecutionFailure)
    assert isinstance(unbound, ExecutionFailure)
    assert rejected.error.code is ExecutionErrorCode.MUTATION_CONFIRMATION_INVALID
    assert unbound.error.code is ExecutionErrorCode.BINDING_NOT_FOUND
    assert unbound_store.claim_requests == []
    assert adapter.calls == []
    assert unbound_adapter.calls == []


def test_reused_terminal_outcome_is_returned_without_dispatch() -> None:
    service, store, adapter, tool = services()
    first = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )
    assert isinstance(first, ExecutionSuccess)
    store.claim = IdempotencyClaim(
        IdempotencyClaimStatus.REUSED,
        store.completions[0][0],
        IdempotencyRecordState.SUCCEEDED,
        serialize_execution_result(first),
    )

    second = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="x" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert second == first
    assert len(adapter.calls) == 1


def test_in_progress_duplicate_does_not_dispatch() -> None:
    service, store, adapter, tool = services()
    store.claim = IdempotencyClaim(
        IdempotencyClaimStatus.IN_PROGRESS,
        IdempotencyRecordId.generate(),
        IdempotencyRecordState.PENDING,
    )

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS
    assert adapter.calls == []


def test_invalid_ttl_and_read_operations_fail_before_state_or_dispatch() -> None:
    service, store, adapter, tool = services(operation=OperationClass.READ)
    request = ExecutionRequest(tool.ref, {"name": "Ada"})

    preview = service.preview(request, principal=PRINCIPAL)
    confirmed = asyncio.run(
        service.confirm(
            request,
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(preview, ExecutionFailure)
    assert isinstance(confirmed, ExecutionFailure)
    assert preview.error.code is ExecutionErrorCode.INVALID_REQUEST
    assert confirmed.error.code is ExecutionErrorCode.INVALID_REQUEST
    assert store.challenges == []
    assert store.claim_requests == []
    assert adapter.calls == []
    with pytest.raises(ValueError, match="TTL"):
        MutationService(service._executor, store, challenge_ttl=timedelta(seconds=29))


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            MutationStateUnavailableError(operation="challenge issuance"),
            ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE,
        ),
        (RuntimeError("unexpected"), ExecutionErrorCode.INTERNAL_EXECUTION_ERROR),
    ],
)
def test_preview_translates_state_failures_without_dispatch(
    error: Exception,
    expected: ExecutionErrorCode,
) -> None:
    service, store, adapter, tool = services()
    store.issue_error = error

    result = service.preview(ExecutionRequest(tool.ref, {"name": "Ada"}), principal=PRINCIPAL)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is expected
    assert adapter.calls == []


def test_preview_rejects_invalid_generated_token_without_persisting() -> None:
    service, store, adapter, tool = services()
    service._token_factory = lambda: "short"

    result = service.preview(ExecutionRequest(tool.ref, {"name": "Ada"}), principal=PRINCIPAL)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INVALID_REQUEST
    assert store.challenges == []
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("claim_error", "expected"),
    [
        (IdempotencyConflictError(), ExecutionErrorCode.IDEMPOTENCY_CONFLICT),
        (
            MutationStateUnavailableError(operation="execution claim"),
            ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE,
        ),
        (RuntimeError("unexpected"), ExecutionErrorCode.INTERNAL_EXECUTION_ERROR),
    ],
)
def test_confirm_translates_claim_failures_without_dispatch(
    claim_error: Exception,
    expected: ExecutionErrorCode,
) -> None:
    service, store, adapter, tool = services()
    store.claim = claim_error

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is expected
    assert adapter.calls == []


def test_confirm_rejects_invalid_key_before_claim_or_dispatch() -> None:
    service, store, adapter, tool = services()

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="bad/key",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INVALID_REQUEST
    assert store.claim_requests == []
    assert adapter.calls == []


@pytest.mark.parametrize(
    "completion_error",
    [
        MutationStateUnavailableError(operation="outcome persistence"),
        MutationStateIntegrityError(reason="record disappeared"),
    ],
)
def test_completion_failure_reports_unknown_outcome_after_one_dispatch(
    completion_error: Exception,
) -> None:
    service, store, adapter, tool = services()
    store.complete_error = completion_error

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.MUTATION_OUTCOME_UNKNOWN
    assert len(adapter.calls) == 1


def test_adapter_failure_is_persisted_as_unknown_and_reused_without_redispatch() -> None:
    service, store, adapter, tool = services(
        adapter_result=AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)
    )
    request = ExecutionRequest(tool.ref, {"name": "Ada"})

    first = asyncio.run(
        service.confirm(
            request,
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )
    assert isinstance(first, ExecutionFailure)
    assert store.completions[0][1] is IdempotencyRecordState.OUTCOME_UNKNOWN
    store.claim = IdempotencyClaim(
        IdempotencyClaimStatus.REUSED,
        store.completions[0][0],
        IdempotencyRecordState.OUTCOME_UNKNOWN,
        store.completions[0][2],
    )

    second = asyncio.run(
        service.confirm(
            request,
            principal=PRINCIPAL,
            confirmation_token="x" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert second == first
    assert len(adapter.calls) == 1


def test_mutation_dispatch_never_retries_even_when_contract_declares_idempotency() -> None:
    service, store, adapter, tool = services(
        adapter_result=AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
        retry_policy=RetryPolicy(max_attempts=3),
    )

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-no-retry",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.UPSTREAM_UNAVAILABLE
    assert len(adapter.calls) == 1
    assert store.completions[0][1] is IdempotencyRecordState.OUTCOME_UNKNOWN


@pytest.mark.parametrize("outcome", [b"not-json", b""])
def test_corrupt_reused_outcome_fails_closed(outcome: bytes) -> None:
    service, store, adapter, tool = services()
    store.claim = IdempotencyClaim(
        IdempotencyClaimStatus.REUSED,
        IdempotencyRecordId.generate(),
        IdempotencyRecordState.SUCCEEDED,
        outcome,
    )

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert adapter.calls == []


def test_reused_outcome_for_another_tool_fails_closed() -> None:
    service, store, adapter, tool = services()
    first = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="t" * 43,
            idempotency_key="profile-update-1",
        )
    )
    assert isinstance(first, ExecutionSuccess)
    mismatched = replace(
        first,
        context=replace(first.context, definition_fingerprint="f" * 64),
    )
    store.claim = IdempotencyClaim(
        IdempotencyClaimStatus.REUSED,
        store.completions[0][0],
        IdempotencyRecordState.SUCCEEDED,
        serialize_execution_result(mismatched),
    )

    result = asyncio.run(
        service.confirm(
            ExecutionRequest(tool.ref, {"name": "Ada"}),
            principal=PRINCIPAL,
            confirmation_token="x" * 43,
            idempotency_key="profile-update-1",
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert len(adapter.calls) == 1
