"""API-only OpenTelemetry instrumentation around core execution boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from enum import StrEnum
from types import TracebackType
from typing import Protocol, final, runtime_checkable

from opentelemetry.trace import Span, Status, StatusCode, Tracer

from sentinelmcp.domain import OperationClass, ToolRef
from sentinelmcp.execution import ExecutionContext, ExecutionFailure, ExecutionResult
from sentinelmcp.telemetry.logging import StructuredEventLogger
from sentinelmcp.telemetry.metrics import (
    AuditFailurePhase,
    IdempotencyMetricOutcome,
    PrometheusMetrics,
)


class ExecutionStage(StrEnum):
    """Finite lifecycle stages safe for span names and attributes."""

    REGISTRY_LOOKUP = "registry_lookup"
    INPUT_VALIDATION = "input_validation"
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    MUTATION_PREVIEW = "mutation_preview"
    MUTATION_CONFIRMATION = "mutation_confirmation"
    RUNTIME_BINDING = "runtime_binding"
    OUTPUT_VALIDATION = "output_validation"
    AUDIT_RECORDING = "audit_recording"


@runtime_checkable
class AttemptObservation(Protocol):
    def finish(self, result: ExecutionResult) -> None:
        """Record one normalized adapter-attempt result."""


@runtime_checkable
class ExecutionObservation(Protocol):
    @property
    def attempt_count(self) -> int:
        """Return the number of adapter attempts observed."""

    def observe_stage(self, stage: ExecutionStage) -> AbstractContextManager[None]:
        """Create one child span for a finite lifecycle stage."""

    def observe_attempt(
        self,
        context: ExecutionContext,
        *,
        attempt_number: int,
        operation_class: OperationClass,
    ) -> AbstractContextManager[AttemptObservation]:
        """Create one child observation for an adapter attempt."""

    def finish(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
    ) -> None:
        """Record the final normalized execution result."""

    def finish_preview(self) -> None:
        """Record successful creation of a mutation preview."""

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        """Record an audit-persistence failure without changing the execution outcome."""

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        """Record one finite mutation idempotency disposition."""


@runtime_checkable
class ExecutionObservability(Protocol):
    def observe_execution(
        self,
        tool_ref: ToolRef,
    ) -> AbstractContextManager[ExecutionObservation]:
        """Create one request-lifecycle observation."""


@final
class _NoOpAttempt(AttemptObservation):
    def finish(self, result: ExecutionResult) -> None:
        del result


@final
class _NoOpExecution(ExecutionObservation):
    __slots__ = ("_attempt_count",)

    def __init__(self) -> None:
        self._attempt_count = 0

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    @contextmanager
    def observe_stage(self, stage: ExecutionStage) -> Iterator[None]:
        del stage
        yield

    @contextmanager
    def observe_attempt(
        self,
        context: ExecutionContext,
        *,
        attempt_number: int,
        operation_class: OperationClass,
    ) -> Iterator[AttemptObservation]:
        del context, attempt_number, operation_class
        self._attempt_count += 1
        yield _NoOpAttempt()

    def finish(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
    ) -> None:
        del result, operation_class

    def finish_preview(self) -> None:
        return

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        del phase

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        del outcome


@final
class NoOpExecutionObservability(ExecutionObservability):
    @contextmanager
    def observe_execution(self, tool_ref: ToolRef) -> Iterator[ExecutionObservation]:
        del tool_ref
        yield _NoOpExecution()


@final
class ResilientExecutionObservability(ExecutionObservability):
    """Prevent telemetry implementations from changing application behavior."""

    __slots__ = ("_delegate",)

    def __init__(self, delegate: ExecutionObservability) -> None:
        if not isinstance(delegate, ExecutionObservability):
            raise TypeError("delegate must implement ExecutionObservability")
        self._delegate = delegate

    @contextmanager
    def observe_execution(self, tool_ref: ToolRef) -> Iterator[ExecutionObservation]:
        fallback: ExecutionObservation = _NoOpExecution()
        with _resilient_context(
            lambda: self._delegate.observe_execution(tool_ref),
            fallback,
        ) as observation:
            yield _ResilientExecutionObservation(observation)


@final
class _ResilientExecutionObservation(ExecutionObservation):
    __slots__ = ("_delegate", "_fallback_attempt_count")

    def __init__(self, delegate: ExecutionObservation) -> None:
        self._delegate = delegate
        self._fallback_attempt_count = 0

    @property
    def attempt_count(self) -> int:
        with suppress(Exception):
            return self._delegate.attempt_count
        return self._fallback_attempt_count

    @contextmanager
    def observe_stage(self, stage: ExecutionStage) -> Iterator[None]:
        with _resilient_context(lambda: self._delegate.observe_stage(stage), None):
            yield

    @contextmanager
    def observe_attempt(
        self,
        context: ExecutionContext,
        *,
        attempt_number: int,
        operation_class: OperationClass,
    ) -> Iterator[AttemptObservation]:
        self._fallback_attempt_count += 1
        fallback: AttemptObservation = _NoOpAttempt()
        with _resilient_context(
            lambda: self._delegate.observe_attempt(
                context,
                attempt_number=attempt_number,
                operation_class=operation_class,
            ),
            fallback,
        ) as observation:
            yield _ResilientAttemptObservation(observation)

    def finish(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
    ) -> None:
        with suppress(Exception):
            self._delegate.finish(result, operation_class)

    def finish_preview(self) -> None:
        with suppress(Exception):
            self._delegate.finish_preview()

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        with suppress(Exception):
            self._delegate.record_audit_failure(phase)

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        with suppress(Exception):
            self._delegate.record_idempotency(outcome)


@final
class _ResilientAttemptObservation(AttemptObservation):
    __slots__ = ("_delegate",)

    def __init__(self, delegate: AttemptObservation) -> None:
        self._delegate = delegate

    def finish(self, result: ExecutionResult) -> None:
        with suppress(Exception):
            self._delegate.finish(result)


@contextmanager
def _resilient_context[T](
    factory: Callable[[], AbstractContextManager[T]],
    fallback: T,
) -> Iterator[T]:
    try:
        manager = factory()
        value = manager.__enter__()
    except Exception:
        yield fallback
        return
    try:
        yield value
    except BaseException as error:
        _safe_exit(manager, type(error), error, error.__traceback__)
        raise
    else:
        _safe_exit(manager, None, None, None)


def _safe_exit(
    manager: AbstractContextManager[object],
    exception_type: type[BaseException] | None,
    exception: BaseException | None,
    traceback: TracebackType | None,
) -> None:
    with suppress(Exception):
        manager.__exit__(exception_type, exception, traceback)


@final
class OpenTelemetryExecutionObservability(ExecutionObservability):
    """Create allowlisted spans and optional metrics/logs from normalized outcomes."""

    __slots__ = ("_logger", "_metrics", "_tracer")

    def __init__(
        self,
        tracer: Tracer,
        *,
        metrics: PrometheusMetrics | None = None,
        logger: StructuredEventLogger | None = None,
    ) -> None:
        self._tracer = tracer
        self._metrics = metrics
        self._logger = logger

    @contextmanager
    def observe_execution(self, tool_ref: ToolRef) -> Iterator[ExecutionObservation]:
        attributes: dict[str, bool | int | float | str] = {
            "sentinelmcp.tool.namespace": str(tool_ref.namespace),
            "sentinelmcp.tool.name": str(tool_ref.name),
            "sentinelmcp.tool.version": str(tool_ref.version),
        }
        with self._tracer.start_as_current_span(
            "sentinelmcp.execution",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield _OpenTelemetryExecution(
                tracer=self._tracer,
                span=span,
                metrics=self._metrics,
                logger=self._logger,
            )


@final
class _OpenTelemetryExecution(ExecutionObservation):
    __slots__ = ("_attempt_count", "_logger", "_metrics", "_span", "_tracer")

    def __init__(
        self,
        *,
        tracer: Tracer,
        span: Span,
        metrics: PrometheusMetrics | None,
        logger: StructuredEventLogger | None,
    ) -> None:
        self._tracer = tracer
        self._span = span
        self._metrics = metrics
        self._logger = logger
        self._attempt_count = 0

    @property
    def attempt_count(self) -> int:
        return self._attempt_count

    @contextmanager
    def observe_stage(self, stage: ExecutionStage) -> Iterator[None]:
        if not isinstance(stage, ExecutionStage):
            raise TypeError("stage must be an ExecutionStage")
        with self._tracer.start_as_current_span(
            f"sentinelmcp.stage.{stage.value}",
            attributes={"sentinelmcp.stage": stage.value},
            record_exception=False,
            set_status_on_exception=False,
        ):
            yield

    @contextmanager
    def observe_attempt(
        self,
        context: ExecutionContext,
        *,
        attempt_number: int,
        operation_class: OperationClass,
    ) -> Iterator[AttemptObservation]:
        self._attempt_count += 1
        attributes: dict[str, bool | int | float | str] = {
            "sentinelmcp.attempt.number": attempt_number,
            "sentinelmcp.execution.id": str(context.execution_id),
            "sentinelmcp.operation": operation_class.value,
            "sentinelmcp.request.id": str(context.request_id),
        }
        with self._tracer.start_as_current_span(
            "sentinelmcp.execution.attempt",
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield _OpenTelemetryAttempt(span)

    def finish(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
    ) -> None:
        outcome = result.error.code.value if isinstance(result, ExecutionFailure) else "success"
        self._span.set_attribute("sentinelmcp.attempt.count", self._attempt_count)
        self._span.set_attribute("sentinelmcp.execution.id", str(result.context.execution_id))
        self._span.set_attribute(
            "sentinelmcp.operation",
            "unknown" if operation_class is None else operation_class.value,
        )
        self._span.set_attribute("sentinelmcp.outcome", outcome)
        self._span.set_attribute("sentinelmcp.request.id", str(result.context.request_id))
        self._span.set_status(
            Status(StatusCode.ERROR if isinstance(result, ExecutionFailure) else StatusCode.OK)
        )
        if self._metrics is not None:
            self._metrics.record_execution(
                result,
                operation_class,
                attempt_count=self._attempt_count,
            )
        if self._logger is not None:
            self._logger.record_execution(
                result,
                operation_class,
                attempt_count=self._attempt_count,
            )

    def finish_preview(self) -> None:
        self._span.set_attribute("sentinelmcp.operation", OperationClass.MUTATION.value)
        self._span.set_attribute("sentinelmcp.outcome", "preview_ready")
        self._span.set_status(Status(StatusCode.OK))
        if self._metrics is not None:
            self._metrics.record_mutation_preview(succeeded=True)

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        self._span.add_event(
            "sentinelmcp.audit.persistence_failure",
            {"sentinelmcp.audit.phase": phase.value},
        )
        if self._metrics is not None:
            self._metrics.record_audit_failure(phase)

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        self._span.add_event(
            "sentinelmcp.mutation.idempotency",
            {"sentinelmcp.idempotency.outcome": outcome.value},
        )
        if self._metrics is not None:
            self._metrics.record_idempotency(outcome)


@final
class _OpenTelemetryAttempt(AttemptObservation):
    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def finish(self, result: ExecutionResult) -> None:
        outcome = result.error.code.value if isinstance(result, ExecutionFailure) else "success"
        self._span.set_attribute("sentinelmcp.attempt.outcome", outcome)
        self._span.set_status(
            Status(StatusCode.ERROR if isinstance(result, ExecutionFailure) else StatusCode.OK)
        )
