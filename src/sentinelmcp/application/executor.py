"""Transport-independent orchestration for one exact tool execution."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast, final

from sentinelmcp.domain import (
    ArgumentValidationError,
    OperationClass,
    OutputValidationError,
    ToolNotFoundError,
    ValidationIssue,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterSuccess,
    BindingNotFoundError,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
    RequestId,
    RuntimeBindingResolver,
)
from sentinelmcp.policy import AuthorizationDecision, AuthorizationPolicy, Principal
from sentinelmcp.registry import (
    ToolRegistry,
    fingerprint_tool_definition,
    validate_tool_definition_schemas,
)

_SAFE_MESSAGES: dict[ExecutionErrorCode, str] = {
    ExecutionErrorCode.INVALID_REQUEST: "The execution request is invalid.",
    ExecutionErrorCode.AUTHENTICATION_FAILED: "Caller authentication is required.",
    ExecutionErrorCode.TOOL_NOT_FOUND: "The requested exact tool version is not registered.",
    ExecutionErrorCode.INPUT_VALIDATION_FAILED: (
        "Arguments do not satisfy the registered input schema."
    ),
    ExecutionErrorCode.AUTHORIZATION_FAILED: (
        "The authenticated caller is not authorized for this tool operation."
    ),
    ExecutionErrorCode.MUTATION_NOT_SUPPORTED: (
        "Mutation execution is unavailable until confirmation safeguards are implemented."
    ),
    ExecutionErrorCode.BINDING_NOT_FOUND: "The exact tool version has no runtime binding.",
    ExecutionErrorCode.ADAPTER_FAILURE: "The tool adapter could not complete the invocation.",
    ExecutionErrorCode.UPSTREAM_BAD_REQUEST: "The upstream tool rejected the request.",
    ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED: (
        "The upstream tool rejected SentinelMCP authentication."
    ),
    ExecutionErrorCode.UPSTREAM_AUTHORIZATION_FAILED: ("The upstream tool denied the operation."),
    ExecutionErrorCode.UPSTREAM_NOT_FOUND: "The requested upstream resource was not found.",
    ExecutionErrorCode.UPSTREAM_CONFLICT: "The upstream tool reported a conflict.",
    ExecutionErrorCode.UPSTREAM_RATE_LIMITED: "The upstream tool rate-limited the request.",
    ExecutionErrorCode.UPSTREAM_STATUS_ERROR: "The upstream tool returned an unsuccessful status.",
    ExecutionErrorCode.UPSTREAM_SERVER_ERROR: "The upstream tool reported an internal error.",
    ExecutionErrorCode.UPSTREAM_UNAVAILABLE: "The upstream tool is unavailable.",
    ExecutionErrorCode.UPSTREAM_TIMEOUT: "The upstream tool timed out.",
    ExecutionErrorCode.UPSTREAM_INVALID_CONTENT_TYPE: (
        "The upstream tool returned an unsupported content type."
    ),
    ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE: (
        "The upstream tool returned a malformed response."
    ),
    ExecutionErrorCode.UPSTREAM_RESPONSE_TOO_LARGE: (
        "The upstream tool response exceeded the configured size limit."
    ),
    ExecutionErrorCode.OUTPUT_VALIDATION_FAILED: (
        "The tool output does not satisfy the registered output schema."
    ),
    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR: ("SentinelMCP could not complete the execution."),
}


@final
class ToolExecutor:
    """Execute exactly one validated tool invocation without retries."""

    __slots__ = (
        "_authorization_policy",
        "_bindings",
        "_execution_id_factory",
        "_monotonic",
        "_registry",
        "_request_id_factory",
        "_wall_clock",
    )

    def __init__(
        self,
        registry: ToolRegistry,
        bindings: RuntimeBindingResolver,
        *,
        authorization_policy: AuthorizationPolicy,
        request_id_factory: Callable[[], RequestId] = RequestId.generate,
        execution_id_factory: Callable[[], ExecutionId] = ExecutionId.generate,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._bindings = bindings
        self._request_id_factory = request_id_factory
        self._execution_id_factory = execution_id_factory
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._authorization_policy = authorization_policy

    async def execute(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal | None = None,
    ) -> ExecutionResult:
        """Run the Phase 3 lifecycle for one exact immutable tool version."""

        started_monotonic = self._monotonic()
        context = ExecutionContext(
            request_id=self._request_id_factory(),
            execution_id=self._execution_id_factory(),
            tool_ref=request.tool_ref,
            definition_fingerprint=None,
            started_at=self._wall_clock(),
            started_monotonic=started_monotonic,
        )

        try:
            definition = self._registry.get(request.tool_ref)
            fingerprint = fingerprint_tool_definition(definition)
            validator = validate_tool_definition_schemas(definition)
        except ToolNotFoundError:
            return self._failure(context, ExecutionErrorCode.TOOL_NOT_FOUND)
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

        context = ExecutionContext(
            request_id=context.request_id,
            execution_id=context.execution_id,
            tool_ref=context.tool_ref,
            definition_fingerprint=fingerprint,
            started_at=context.started_at,
            started_monotonic=context.started_monotonic,
        )

        try:
            validator.validate_arguments(request.arguments)
        except ArgumentValidationError as error:
            return self._failure(
                context,
                ExecutionErrorCode.INPUT_VALIDATION_FAILED,
                issues=error.issues,
            )
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

        if principal is None:
            return self._failure(context, ExecutionErrorCode.AUTHENTICATION_FAILED)
        try:
            decision = self._authorization_policy.authorize(principal, definition)
            if not isinstance(decision, AuthorizationDecision):
                raise TypeError("authorization policy returned an invalid decision")
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)
        if not decision.allowed:
            return self._failure(context, ExecutionErrorCode.AUTHORIZATION_FAILED)

        if definition.operation_class is OperationClass.MUTATION:
            return self._failure(context, ExecutionErrorCode.MUTATION_NOT_SUPPORTED)

        try:
            binding = self._bindings.resolve(request.tool_ref)
        except BindingNotFoundError:
            return self._failure(context, ExecutionErrorCode.BINDING_NOT_FOUND)
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

        invocation = AdapterInvocation(
            context=context,
            arguments=cast(FrozenJSONMapping, request.arguments),
        )
        try:
            adapter_result: object = await binding.adapter.execute(invocation)
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

        if isinstance(adapter_result, AdapterFailure):
            return self._failure(
                context,
                adapter_result.code,
                upstream_status=adapter_result.upstream_status,
            )
        if not isinstance(adapter_result, AdapterSuccess):
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)
        try:
            validator.validate_output(adapter_result.output)
        except OutputValidationError as error:
            return self._failure(
                context,
                ExecutionErrorCode.OUTPUT_VALIDATION_FAILED,
                issues=error.issues,
            )
        except Exception:
            return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

        return ExecutionSuccess(
            context=context,
            result=adapter_result.output,
            duration_ms=self._duration_ms(started_monotonic),
        )

    def _failure(
        self,
        context: ExecutionContext,
        code: ExecutionErrorCode,
        *,
        upstream_status: int | None = None,
        issues: tuple[ValidationIssue, ...] = (),
    ) -> ExecutionFailure:
        return ExecutionFailure(
            context=context,
            error=ExecutionError(
                code=code,
                message=_SAFE_MESSAGES[code],
                upstream_status=upstream_status,
                issues=issues,
            ),
            duration_ms=self._duration_ms(context.started_monotonic),
        )

    def _duration_ms(self, started_monotonic: float) -> float:
        return max(0.0, (self._monotonic() - started_monotonic) * 1_000)
