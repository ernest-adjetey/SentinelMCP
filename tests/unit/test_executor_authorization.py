"""Authorization ordering at the transport-independent executor boundary."""

from __future__ import annotations

import asyncio
from typing import cast

from sentinelmcp.application import ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.execution import (
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
from sentinelmcp.policy import (
    AuthorizationDecision,
    AuthorizationReason,
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

PRINCIPAL = Principal(
    "authorization-test",
    "https://issuer.test",
    roles=frozenset({"developer"}),
    scopes=frozenset({"tools:execute"}),
)


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[AdapterInvocation] = []

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.calls.append(invocation)
        return AdapterSuccess({"value": "ok"})


class TrackingPolicy:
    def __init__(
        self,
        decision: AuthorizationDecision,
        *,
        exception: Exception | None = None,
    ) -> None:
        self.decision = decision
        self.exception = exception
        self.calls: list[tuple[Principal, ToolDefinition]] = []

    def authorize(
        self,
        principal: Principal,
        definition: ToolDefinition,
    ) -> AuthorizationDecision:
        self.calls.append((principal, definition))
        if self.exception is not None:
            raise self.exception
        return self.decision


class InvalidDecisionPolicy:
    def authorize(
        self,
        _principal: Principal,
        _definition: ToolDefinition,
    ) -> AuthorizationDecision:
        return cast(AuthorizationDecision, object())


def definition(operation: OperationClass = OperationClass.READ) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.security"),
        name=ToolName("check"),
        version=ToolVersion("1.0.0"),
        description="Exercise authorization ordering.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        operation_class=operation,
    )


def executor_for(
    tool: ToolDefinition,
    policy: TrackingPolicy | RuleBasedAuthorizationPolicy,
) -> tuple[ToolExecutor, RecordingAdapter]:
    registry = InMemoryToolRegistry()
    registry.register(tool)
    adapter = RecordingAdapter()
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(tool.ref, adapter))
    return ToolExecutor(registry, bindings, authorization_policy=policy), adapter


def execute(
    executor: ToolExecutor,
    tool: ToolDefinition,
    *,
    principal: Principal | None,
    value: object = "valid",
) -> ExecutionSuccess | ExecutionFailure:
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(tool.ref, {"value": value}),
            principal=principal,
        )
    )
    assert isinstance(result, ExecutionSuccess | ExecutionFailure)
    return result


def test_missing_principal_fails_before_policy_or_adapter() -> None:
    tool = definition()
    policy = TrackingPolicy(AuthorizationDecision(True, AuthorizationReason.ALLOWED))
    executor, adapter = executor_for(tool, policy)

    result = execute(executor, tool, principal=None)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.AUTHENTICATION_FAILED
    assert policy.calls == []
    assert adapter.calls == []


def test_argument_validation_precedes_authorization_and_dispatch() -> None:
    tool = definition()
    policy = TrackingPolicy(AuthorizationDecision(True, AuthorizationReason.ALLOWED))
    executor, adapter = executor_for(tool, policy)

    result = execute(executor, tool, principal=PRINCIPAL, value=42)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INPUT_VALIDATION_FAILED
    assert policy.calls == []
    assert adapter.calls == []


def test_denial_and_policy_failure_never_dispatch() -> None:
    tool = definition()
    denied_policy = TrackingPolicy(
        AuthorizationDecision(False, AuthorizationReason.NO_MATCHING_ALLOW)
    )
    denied_executor, denied_adapter = executor_for(tool, denied_policy)
    broken_policy = TrackingPolicy(
        AuthorizationDecision(True, AuthorizationReason.ALLOWED),
        exception=RuntimeError("private policy detail"),
    )
    broken_executor, broken_adapter = executor_for(tool, broken_policy)

    denied = execute(denied_executor, tool, principal=PRINCIPAL)
    broken = execute(broken_executor, tool, principal=PRINCIPAL)

    assert isinstance(denied, ExecutionFailure)
    assert isinstance(broken, ExecutionFailure)
    assert denied.error.code is ExecutionErrorCode.AUTHORIZATION_FAILED
    assert broken.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert "private" not in broken.error.message
    assert len(denied_policy.calls) == 1
    assert len(broken_policy.calls) == 1
    assert denied_adapter.calls == []
    assert broken_adapter.calls == []


def test_invalid_policy_result_is_normalized_before_dispatch() -> None:
    tool = definition()
    executor, adapter = executor_for(
        tool,
        cast(RuleBasedAuthorizationPolicy, InvalidDecisionPolicy()),
    )

    result = execute(executor, tool, principal=PRINCIPAL)

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert adapter.calls == []


def test_matching_allow_dispatches_once() -> None:
    tool = definition()
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                tool.identity,
                OperationClass.READ,
                required_roles=frozenset({"developer"}),
                required_scopes=frozenset({"tools:execute"}),
            ),
        )
    )
    executor, adapter = executor_for(tool, policy)

    result = execute(executor, tool, principal=PRINCIPAL)

    assert isinstance(result, ExecutionSuccess)
    assert len(adapter.calls) == 1


def test_read_permission_does_not_grant_mutation_permission() -> None:
    tool = definition(OperationClass.MUTATION)
    read_only = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, tool.identity, OperationClass.READ),)
    )
    read_executor, read_adapter = executor_for(tool, read_only)
    mutation_allowed = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, tool.identity, OperationClass.MUTATION),)
    )
    mutation_executor, mutation_adapter = executor_for(tool, mutation_allowed)

    denied = execute(read_executor, tool, principal=PRINCIPAL)
    guarded = execute(mutation_executor, tool, principal=PRINCIPAL)

    assert isinstance(denied, ExecutionFailure)
    assert isinstance(guarded, ExecutionFailure)
    assert denied.error.code is ExecutionErrorCode.AUTHORIZATION_FAILED
    assert guarded.error.code is ExecutionErrorCode.MUTATION_NOT_SUPPORTED
    assert read_adapter.calls == []
    assert mutation_adapter.calls == []
