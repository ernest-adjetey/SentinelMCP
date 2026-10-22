"""Transport-independent orchestration for one exact tool execution."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast, final

from sentinelmcp.audit import AuditEventKind, AuditRecorder, NoOpAuditRecorder
from sentinelmcp.domain import (
    ArgumentValidationError,
    OperationClass,
    OutputValidationError,
    ToolDefinition,
    ToolNotFoundError,
    ToolRef,
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
    RuntimeToolBinding,
)
from sentinelmcp.execution.reliability import (
    CircuitPermit,
    Deadline,
    RateLimitKey,
    ReliabilityControls,
    full_jitter_delay_seconds,
    is_circuit_failure,
    is_retryable_failure,
    retry_after_ms,
)
from sentinelmcp.policy import AuthorizationDecision, AuthorizationPolicy, Principal
from sentinelmcp.registry import (
    ToolContractValidator,
    ToolRegistry,
    fingerprint_tool_definition,
    validate_tool_definition_schemas,
)
from sentinelmcp.telemetry import (
    AuditFailurePhase,
    ExecutionObservability,
    ExecutionObservation,
    ExecutionStage,
    NoOpExecutionObservability,
    ResilientExecutionObservability,
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
        "Mutations require the durable preview and confirmation workflow."
    ),
    ExecutionErrorCode.MUTATION_CONFIRMATION_INVALID: (
        "Mutation confirmation is invalid, expired, consumed, or bound to another request."
    ),
    ExecutionErrorCode.IDEMPOTENCY_CONFLICT: (
        "The idempotency key is already bound to a different mutation request."
    ),
    ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS: (
        "The accepted mutation is still pending; retry the same semantic request later."
    ),
    ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE: (
        "Durable mutation safety state is unavailable; no mutation was dispatched."
    ),
    ExecutionErrorCode.MUTATION_OUTCOME_UNKNOWN: (
        "The mutation may have reached the tool, but its durable outcome is unavailable."
    ),
    ExecutionErrorCode.RATE_LIMITED: "The caller has exhausted the local request rate limit.",
    ExecutionErrorCode.CIRCUIT_OPEN: "The tool dependency circuit is open.",
    ExecutionErrorCode.DEADLINE_EXCEEDED: "The request-wide execution deadline was exhausted.",
    ExecutionErrorCode.AUDIT_UNAVAILABLE: "Required durable audit state is unavailable.",
    ExecutionErrorCode.BINDING_NOT_FOUND: "The exact tool version has no runtime binding.",
    ExecutionErrorCode.ADAPTER_FAILURE: "The tool adapter could not complete the invocation.",
    ExecutionErrorCode.OUTBOUND_TARGET_REJECTED: (
        "The configured outbound tool target was rejected by network policy."
    ),
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


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    """Validated and authorized exact execution before operational binding."""

    request: ExecutionRequest
    context: ExecutionContext
    definition: ToolDefinition
    validator: ToolContractValidator
    deadline: Deadline


@final
class ToolExecutor:
    """Execute one authorized tool request within its reliability policy."""

    __slots__ = (
        "_audit",
        "_authorization_policy",
        "_bindings",
        "_execution_id_factory",
        "_monotonic",
        "_observability",
        "_registry",
        "_reliability",
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
        reliability: ReliabilityControls | None = None,
        audit: AuditRecorder | None = None,
        observability: ExecutionObservability | None = None,
    ) -> None:
        self._registry = registry
        self._bindings = bindings
        self._request_id_factory = request_id_factory
        self._execution_id_factory = execution_id_factory
        self._wall_clock = wall_clock
        self._monotonic = monotonic
        self._authorization_policy = authorization_policy
        self._reliability = ReliabilityControls() if reliability is None else reliability
        self._audit = NoOpAuditRecorder() if audit is None else audit
        configured_observability = (
            NoOpExecutionObservability() if observability is None else observability
        )
        self._observability = ResilientExecutionObservability(configured_observability)

    async def execute(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal | None = None,
        binding_guard: Callable[[ToolDefinition, RuntimeToolBinding], bool] | None = None,
    ) -> ExecutionResult:
        """Run one authorized read; mutations require the durable mutation service."""

        with self._observability.observe_execution(request.tool_ref) as observation:
            prepared = self.prepare(request, principal=principal, observation=observation)
            if isinstance(prepared, ExecutionFailure):
                return self._complete_execution(observation, prepared, principal, None)
            operation_class = prepared.definition.operation_class
            if operation_class is OperationClass.MUTATION:
                mutation_result = self._failure(
                    prepared.context,
                    ExecutionErrorCode.MUTATION_NOT_SUPPORTED,
                )
                return self._complete_execution(
                    observation,
                    mutation_result,
                    principal,
                    operation_class,
                )
            binding = self.resolve_binding(prepared, observation=observation)
            if isinstance(binding, ExecutionFailure):
                return self._complete_execution(
                    observation,
                    binding,
                    principal,
                    operation_class,
                )
            if binding_guard is not None:
                try:
                    binding_allowed = binding_guard(prepared.definition, binding)
                    if not isinstance(binding_allowed, bool):
                        raise TypeError("binding guard returned an invalid decision")
                except Exception:
                    guard_failure = self._failure(
                        prepared.context,
                        ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                    )
                    return self._complete_execution(
                        observation,
                        guard_failure,
                        principal,
                        operation_class,
                    )
                if not binding_allowed:
                    guard_rejection = self._failure(
                        prepared.context,
                        ExecutionErrorCode.BINDING_NOT_FOUND,
                    )
                    return self._complete_execution(
                        observation,
                        guard_rejection,
                        principal,
                        operation_class,
                    )
            read_result = await self.dispatch(prepared, binding, observation=observation)
            return self._complete_execution(
                observation,
                read_result,
                principal,
                operation_class,
            )

    def prepare(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal | None,
        observation: ExecutionObservation | None = None,
    ) -> PreparedExecution | ExecutionFailure:
        """Resolve, validate, and authorize before any operational binding lookup."""

        started_monotonic = self._monotonic()
        context = ExecutionContext(
            request_id=self._request_id_factory(),
            execution_id=self._execution_id_factory(),
            tool_ref=request.tool_ref,
            definition_fingerprint=None,
            started_at=self._wall_clock(),
            started_monotonic=started_monotonic,
        )

        with _observe_stage(observation, ExecutionStage.REGISTRY_LOOKUP):
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

        with _observe_stage(observation, ExecutionStage.INPUT_VALIDATION):
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

        with _observe_stage(observation, ExecutionStage.AUTHORIZATION):
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

        with _observe_stage(observation, ExecutionStage.RATE_LIMIT):
            try:
                admission = self._reliability.rate_limiter.acquire(
                    RateLimitKey.from_request(principal, definition.ref),
                    now_monotonic=self._monotonic(),
                )
            except Exception:
                return self._failure(context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)
            if not admission.allowed:
                return self._failure(
                    context,
                    ExecutionErrorCode.RATE_LIMITED,
                    retry_after_ms=retry_after_ms(admission.retry_after_seconds),
                )

        return PreparedExecution(
            request=request,
            context=context,
            definition=definition,
            validator=validator,
            deadline=Deadline.from_timeout_ms(
                started_monotonic=started_monotonic,
                timeout_ms=definition.timeout_policy.timeout_ms,
            ),
        )

    def resolve_binding(
        self,
        prepared: PreparedExecution,
        *,
        observation: ExecutionObservation | None = None,
    ) -> RuntimeToolBinding | ExecutionFailure:
        """Resolve operational routing after all semantic admission checks."""

        with _observe_stage(observation, ExecutionStage.RUNTIME_BINDING):
            try:
                return self._bindings.resolve(prepared.request.tool_ref)
            except BindingNotFoundError:
                return self._failure(prepared.context, ExecutionErrorCode.BINDING_NOT_FOUND)
            except Exception:
                return self._failure(prepared.context, ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)

    async def dispatch(
        self,
        prepared: PreparedExecution,
        binding: RuntimeToolBinding,
        *,
        observation: ExecutionObservation | None = None,
    ) -> ExecutionResult:
        """Invoke within one deadline, retrying only eligible read failures."""

        maximum_attempts = (
            prepared.definition.retry_policy.max_attempts
            if prepared.definition.operation_class is OperationClass.READ
            else 1
        )
        attempt_number = 1
        while True:
            now = self._monotonic()
            remaining = prepared.deadline.remaining_seconds(now)
            if remaining <= 0:
                return self._failure(
                    prepared.context,
                    ExecutionErrorCode.DEADLINE_EXCEEDED,
                )
            try:
                circuit = self._reliability.circuit_breaker.acquire(
                    prepared.request.tool_ref,
                    now_monotonic=now,
                )
            except Exception:
                return self._failure(
                    prepared.context,
                    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                )
            if not circuit.allowed:
                return self._failure(
                    prepared.context,
                    ExecutionErrorCode.CIRCUIT_OPEN,
                    retry_after_ms=retry_after_ms(circuit.retry_after_seconds),
                )
            assert circuit.permit is not None
            attempt_context = (
                prepared.context
                if attempt_number == 1
                else self._next_attempt_context(prepared.context)
            )
            if observation is None:
                result = await self._dispatch_attempt(
                    prepared,
                    binding,
                    context=attempt_context,
                    timeout_seconds=remaining,
                )
            else:
                with observation.observe_attempt(
                    attempt_context,
                    attempt_number=attempt_number,
                    operation_class=prepared.definition.operation_class,
                ) as attempt_observation:
                    result = await self._dispatch_attempt(
                        prepared,
                        binding,
                        context=attempt_context,
                        timeout_seconds=remaining,
                        observation=observation,
                    )
                    with suppress(Exception):
                        attempt_observation.finish(result)
            self._record_circuit_outcome(
                prepared.request.tool_ref,
                circuit.permit,
                result,
            )
            if isinstance(result, ExecutionSuccess):
                return result
            if attempt_number >= maximum_attempts or not is_retryable_failure(result.error.code):
                return result
            now = self._monotonic()
            try:
                has_retry_budget = self._reliability.retry_budget.acquire(
                    prepared.request.tool_ref,
                    now_monotonic=now,
                )
            except Exception:
                return result
            if not has_retry_budget:
                return result
            try:
                delay = full_jitter_delay_seconds(
                    prepared.definition.retry_policy,
                    retry_number=attempt_number,
                    random_unit=self._reliability.random_unit(),
                )
            except (TypeError, ValueError):
                return self._failure(
                    result.context,
                    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                )
            if delay >= prepared.deadline.remaining_seconds(self._monotonic()):
                return self._failure(
                    result.context,
                    ExecutionErrorCode.DEADLINE_EXCEEDED,
                )
            try:
                await self._reliability.sleep(delay)
            except Exception:
                return self._failure(
                    result.context,
                    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                )
            attempt_number += 1

    async def _dispatch_attempt(
        self,
        prepared: PreparedExecution,
        binding: RuntimeToolBinding,
        *,
        context: ExecutionContext,
        timeout_seconds: float,
        observation: ExecutionObservation | None = None,
    ) -> ExecutionResult:
        """Run one adapter attempt and normalize its contract outcome."""

        invocation = AdapterInvocation(
            context=context,
            arguments=cast(FrozenJSONMapping, prepared.request.arguments),
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                adapter_result: object = await binding.adapter.execute(invocation)
        except TimeoutError:
            return self._failure(context, ExecutionErrorCode.UPSTREAM_TIMEOUT)
        except Exception:
            return self._failure(
                context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )

        if isinstance(adapter_result, AdapterFailure):
            return self._failure(
                context,
                adapter_result.code,
                upstream_status=adapter_result.upstream_status,
            )
        if not isinstance(adapter_result, AdapterSuccess):
            return self._failure(
                context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )
        with _observe_stage(observation, ExecutionStage.OUTPUT_VALIDATION):
            try:
                prepared.validator.validate_output(adapter_result.output)
            except OutputValidationError as error:
                return self._failure(
                    context,
                    ExecutionErrorCode.OUTPUT_VALIDATION_FAILED,
                    issues=error.issues,
                )
            except Exception:
                return self._failure(
                    context,
                    ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                )

        return ExecutionSuccess(
            context=context,
            result=adapter_result.output,
            duration_ms=self._duration_ms(prepared.context.started_monotonic),
        )

    def _record_circuit_outcome(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        result: ExecutionResult,
    ) -> None:
        try:
            if isinstance(result, ExecutionSuccess):
                self._reliability.circuit_breaker.record_success(
                    tool_ref,
                    permit,
                    now_monotonic=self._monotonic(),
                )
            elif is_circuit_failure(result.error.code):
                self._reliability.circuit_breaker.record_failure(
                    tool_ref,
                    permit,
                    now_monotonic=self._monotonic(),
                )
            else:
                self._reliability.circuit_breaker.record_neutral(
                    tool_ref,
                    permit,
                    now_monotonic=self._monotonic(),
                )
        except Exception:
            return

    def _next_attempt_context(self, context: ExecutionContext) -> ExecutionContext:
        return ExecutionContext(
            request_id=context.request_id,
            execution_id=self._execution_id_factory(),
            tool_ref=context.tool_ref,
            definition_fingerprint=context.definition_fingerprint,
            started_at=context.started_at,
            started_monotonic=context.started_monotonic,
        )

    def _complete_execution(
        self,
        observation: ExecutionObservation,
        result: ExecutionResult,
        principal: Principal | None,
        operation_class: OperationClass | None,
    ) -> ExecutionResult:
        with observation.observe_stage(ExecutionStage.AUDIT_RECORDING):
            if principal is not None:
                try:
                    self._audit.record_result(
                        kind=AuditEventKind.EXECUTION_COMPLETED,
                        principal=principal,
                        result=result,
                        operation_class=operation_class,
                        attempt_count=observation.attempt_count,
                    )
                except Exception:
                    observation.record_audit_failure(AuditFailurePhase.READ_RESULT)
        with suppress(Exception):
            observation.finish(result, operation_class)
        return result

    def _failure(
        self,
        context: ExecutionContext,
        code: ExecutionErrorCode,
        *,
        upstream_status: int | None = None,
        retry_after_ms: int | None = None,
        issues: tuple[ValidationIssue, ...] = (),
    ) -> ExecutionFailure:
        return ExecutionFailure(
            context=context,
            error=ExecutionError(
                code=code,
                message=_SAFE_MESSAGES[code],
                upstream_status=upstream_status,
                retry_after_ms=retry_after_ms,
                issues=issues,
            ),
            duration_ms=self._duration_ms(context.started_monotonic),
        )

    def _duration_ms(self, started_monotonic: float) -> float:
        return max(0.0, (self._monotonic() - started_monotonic) * 1_000)


def _observe_stage(
    observation: ExecutionObservation | None,
    stage: ExecutionStage,
) -> AbstractContextManager[None]:
    return nullcontext() if observation is None else observation.observe_stage(stage)
