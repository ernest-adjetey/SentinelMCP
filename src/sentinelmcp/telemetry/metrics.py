"""Prometheus metrics with finite, explicitly allowlisted labels."""

from __future__ import annotations

from enum import StrEnum
from typing import final

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from sentinelmcp.domain import OperationClass
from sentinelmcp.execution import ExecutionErrorCode, ExecutionFailure, ExecutionResult
from sentinelmcp.execution.reliability import CircuitState

_LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class AuditFailurePhase(StrEnum):
    READ_RESULT = "read_result"
    MUTATION_PREVIEW = "mutation_preview"
    MUTATION_INTENT = "mutation_intent"
    MUTATION_RESULT = "mutation_result"


class GatewayRoute(StrEnum):
    EXECUTE = "execute"
    MUTATION_PREVIEW = "mutation_preview"
    MUTATION_CONFIRM = "mutation_confirm"


class IdempotencyMetricOutcome(StrEnum):
    NEW = "new"
    REUSED = "reused"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"


@final
class PrometheusMetrics:
    """Record execution signals without caller-, request-, URL-, or tool-derived labels."""

    __slots__ = (
        "_attempts",
        "_audit_failures",
        "_circuit_states",
        "_circuit_transitions",
        "_duration",
        "_gateway_requests",
        "_idempotency_claims",
        "_mutation_previews",
        "_outcomes",
        "_registry",
        "_requests",
    )

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = CollectorRegistry() if registry is None else registry
        self._requests = Counter(
            "sentinelmcp_requests_total",
            "Accepted core execution requests.",
            ("operation",),
            registry=self._registry,
        )
        self._outcomes = Counter(
            "sentinelmcp_execution_outcomes_total",
            "Normalized execution outcomes.",
            ("operation", "outcome"),
            registry=self._registry,
        )
        self._attempts = Counter(
            "sentinelmcp_execution_attempts_total",
            "Adapter attempts by normalized outcome.",
            ("operation", "outcome"),
            registry=self._registry,
        )
        self._duration = Histogram(
            "sentinelmcp_execution_duration_seconds",
            "Core execution duration in seconds.",
            ("operation", "outcome"),
            buckets=_LATENCY_BUCKETS,
            registry=self._registry,
        )
        self._gateway_requests = Counter(
            "sentinelmcp_gateway_requests_total",
            "Gateway requests by finite route and normalized outcome.",
            ("route", "outcome"),
            registry=self._registry,
        )
        self._idempotency_claims = Counter(
            "sentinelmcp_idempotency_claims_total",
            "Mutation idempotency claims by finite disposition.",
            ("outcome",),
            registry=self._registry,
        )
        self._audit_failures = Counter(
            "sentinelmcp_audit_persistence_failures_total",
            "Audit persistence failures by finite lifecycle phase.",
            ("phase",),
            registry=self._registry,
        )
        self._mutation_previews = Counter(
            "sentinelmcp_mutation_previews_total",
            "Mutation preview outcomes.",
            ("outcome",),
            registry=self._registry,
        )
        self._circuit_transitions = Counter(
            "sentinelmcp_circuit_transitions_total",
            "Local circuit state transitions.",
            ("from_state", "to_state"),
            registry=self._registry,
        )
        self._circuit_states = Gauge(
            "sentinelmcp_circuits",
            "Known local circuits by state.",
            ("state",),
            registry=self._registry,
        )
        for state in CircuitState:
            self._circuit_states.labels(state=state.value).set(0)

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def render(self) -> bytes:
        """Render this isolated registry in Prometheus text exposition format."""

        return generate_latest(self._registry)

    def record_gateway(
        self,
        route: GatewayRoute,
        outcome: ExecutionErrorCode | None,
    ) -> None:
        if not isinstance(route, GatewayRoute):
            raise TypeError("route must be a GatewayRoute")
        if outcome is not None and not isinstance(outcome, ExecutionErrorCode):
            raise TypeError("outcome must be an ExecutionErrorCode or None")
        self._gateway_requests.labels(
            route=route.value,
            outcome="success" if outcome is None else outcome.value,
        ).inc()

    def record_execution(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        *,
        attempt_count: int,
    ) -> None:
        operation = _operation_label(operation_class)
        outcome = _outcome_label(result)
        self._requests.labels(operation=operation).inc()
        self._outcomes.labels(operation=operation, outcome=outcome).inc()
        self._duration.labels(operation=operation, outcome=outcome).observe(
            result.duration_ms / 1_000
        )
        if attempt_count:
            self._attempts.labels(operation=operation, outcome=outcome).inc(attempt_count)

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        if not isinstance(phase, AuditFailurePhase):
            raise TypeError("phase must be an AuditFailurePhase")
        self._audit_failures.labels(phase=phase.value).inc()

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        if not isinstance(outcome, IdempotencyMetricOutcome):
            raise TypeError("outcome must be an IdempotencyMetricOutcome")
        self._idempotency_claims.labels(outcome=outcome.value).inc()

    def record_mutation_preview(self, *, succeeded: bool) -> None:
        if not isinstance(succeeded, bool):
            raise TypeError("succeeded must be a boolean")
        self._mutation_previews.labels(outcome="success" if succeeded else "failure").inc()

    def record_circuit_created(self) -> None:
        self._circuit_states.labels(state=CircuitState.CLOSED.value).inc()

    def record_circuit_transition(
        self,
        previous: CircuitState,
        current: CircuitState,
    ) -> None:
        if not isinstance(previous, CircuitState) or not isinstance(current, CircuitState):
            raise TypeError("circuit states must be CircuitState values")
        if previous is current:
            return
        self._circuit_transitions.labels(
            from_state=previous.value,
            to_state=current.value,
        ).inc()
        self._circuit_states.labels(state=previous.value).dec()
        self._circuit_states.labels(state=current.value).inc()


def _operation_label(operation_class: OperationClass | None) -> str:
    return "unknown" if operation_class is None else operation_class.value


def _outcome_label(result: ExecutionResult) -> str:
    return result.error.code.value if isinstance(result, ExecutionFailure) else "success"
