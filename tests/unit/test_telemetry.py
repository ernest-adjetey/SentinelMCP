"""Redaction, bounded metrics, tracing, and telemetry-failure behavior."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import generate_latest

from sentinelmcp.application import ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping, freeze_json
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    CircuitBreakerConfig,
    CircuitState,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
    InMemoryCircuitBreaker,
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
from sentinelmcp.registry import InMemoryToolRegistry
from sentinelmcp.telemetry import (
    AttemptObservation,
    AuditFailurePhase,
    ExecutionObservability,
    ExecutionObservation,
    ExecutionStage,
    GatewayObservability,
    GatewayRoute,
    IdempotencyMetricOutcome,
    OpenTelemetryExecutionObservability,
    PrometheusMetrics,
    RedactionPolicy,
    ResilientExecutionObservability,
    StructuredEventLogger,
)

TOOL_REF = ToolRef(ToolNamespace("demo.telemetry"), ToolName("probe"), ToolVersion("1.0.0"))
REQUEST_ID = RequestId(UUID("20000000-0000-4000-8000-000000000001"))
EXECUTION_ID = ExecutionId(UUID("20000000-0000-4000-8000-000000000002"))


def test_redaction_is_recursive_bounded_and_does_not_mutate_input() -> None:
    source = {
        "authorization": "Bearer private",
        "nested": {
            "confirmation-token": "confirm-private",
            "ordinary": "abcdefgh",
        },
        "items": [1, 2, 3],
    }
    redacted = RedactionPolicy(max_depth=4, max_items=2, max_string_length=4).redact(source)

    assert source["authorization"] == "Bearer private"
    rendered = repr(redacted)
    assert "Bearer private" not in rendered
    assert "confirm-private" not in rendered
    assert "[REDACTED]" in rendered
    assert "abcd[TRUNCATED]" in rendered
    assert "'_truncated': True" in rendered


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sensitive_keys": {"token"}},
        {"sensitive_keys": frozenset({""})},
        {"max_depth": True},
        {"max_depth": 65},
        {"max_items": 0},
        {"max_string_length": 16_385},
    ],
)
def test_redaction_policy_rejects_invalid_bounds(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        RedactionPolicy(**kwargs)  # type: ignore[arg-type]


def test_redaction_handles_depth_nonfinite_sequences_and_opaque_values() -> None:
    source = {
        1: "ignored non-string key",
        "finite": 1.5,
        "nonfinite": float("inf"),
        "items": [1, 2, 3],
        "opaque": b"bytes",
    }
    redacted = RedactionPolicy(max_depth=4, max_items=2).redact(source)
    depth_limited = RedactionPolicy(max_depth=1).redact({"nested": {"value": "hidden"}})

    assert redacted == {"finite": 1.5, "_truncated": True}
    assert depth_limited == {"nested": "[TRUNCATED]"}
    assert RedactionPolicy(max_items=2).redact([1, 2, 3]) == [1, 2, "[TRUNCATED]"]
    assert RedactionPolicy().redact(float("nan")) == "[TRUNCATED]"
    assert RedactionPolicy().redact(b"bytes") == "[TRUNCATED]"


def test_structured_logger_redacts_secrets_before_json_emission() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("sentinelmcp.tests.telemetry")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(stream))

    StructuredEventLogger(logger).emit(
        "security.probe",
        {"token": "private-token", "safe": "bounded"},
    )

    rendered = stream.getvalue()
    assert "private-token" not in rendered
    assert "[REDACTED]" in rendered
    assert '"safe":"bounded"' in rendered


def test_structured_logger_validates_input_and_records_normalized_results() -> None:
    with pytest.raises(TypeError, match=r"logging\.Logger"):
        StructuredEventLogger(object())  # type: ignore[arg-type]

    stream = io.StringIO()
    logger = logging.getLogger("sentinelmcp.tests.telemetry.results")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    events = StructuredEventLogger(logger)

    with pytest.raises(ValueError, match="bounded dotted identifier"):
        events.emit("INVALID EVENT", {})
    events.record_execution(_success(), OperationClass.READ, attempt_count=1)
    events.record_execution(_failure(), None, attempt_count=0)

    rendered = stream.getvalue()
    assert rendered.count('"event":"execution.completed"') == 2
    assert '"operation_class":"read"' in rendered
    assert '"operation_class":"unknown"' in rendered
    assert '"outcome":"upstream_timeout"' in rendered


def test_prometheus_exposition_uses_only_finite_low_cardinality_labels() -> None:
    metrics = PrometheusMetrics()
    result = _success()

    metrics.record_execution(result, OperationClass.READ, attempt_count=2)
    metrics.record_audit_failure(AuditFailurePhase.READ_RESULT)
    metrics.record_idempotency(IdempotencyMetricOutcome.REUSED)
    metrics.record_mutation_preview(succeeded=True)
    rendered = generate_latest(metrics.registry).decode("utf-8")

    assert 'operation="read"' in rendered
    assert 'outcome="success"' in rendered
    assert 'phase="read_result"' in rendered
    assert 'outcome="reused"' in rendered
    assert 'outcome="success"' in rendered
    for forbidden in (
        str(REQUEST_ID),
        str(EXECUTION_ID),
        "demo.telemetry",
        "probe",
        "principal",
        "request_id",
        "execution_id",
        "tool_name",
    ):
        assert forbidden not in rendered


def test_prometheus_records_failure_paths_and_enforces_finite_label_types() -> None:
    metrics = PrometheusMetrics()
    metrics.record_gateway(GatewayRoute.EXECUTE, ExecutionErrorCode.AUTHENTICATION_FAILED)
    metrics.record_execution(_failure(), None, attempt_count=0)
    metrics.record_mutation_preview(succeeded=False)
    metrics.record_circuit_created()
    metrics.record_circuit_transition(CircuitState.CLOSED, CircuitState.CLOSED)

    rendered = metrics.render().decode("utf-8")
    assert 'operation="unknown",outcome="upstream_timeout"' in rendered
    assert 'outcome="authentication_failed",route="execute"' in rendered
    assert 'outcome="failure"' in rendered

    invalid_calls: tuple[Callable[[], None], ...] = (
        lambda: metrics.record_gateway(object(), None),  # type: ignore[arg-type]
        lambda: metrics.record_gateway(GatewayRoute.EXECUTE, object()),  # type: ignore[arg-type]
        lambda: metrics.record_audit_failure(object()),  # type: ignore[arg-type]
        lambda: metrics.record_idempotency(object()),  # type: ignore[arg-type]
        lambda: metrics.record_mutation_preview(succeeded=1),  # type: ignore[arg-type]
        lambda: metrics.record_circuit_transition(
            object(),  # type: ignore[arg-type]
            CircuitState.CLOSED,
        ),
    )
    for call in invalid_calls:
        with pytest.raises(TypeError):
            call()


def test_circuit_breaker_emits_aggregate_state_transitions_without_tool_labels() -> None:
    metrics = PrometheusMetrics()
    breaker = InMemoryCircuitBreaker(
        CircuitBreakerConfig(failure_threshold=1, open_seconds=1.0),
        observer=metrics,
    )

    initial = breaker.acquire(TOOL_REF, now_monotonic=0.0)
    assert initial.permit is not None
    breaker.record_failure(TOOL_REF, initial.permit, now_monotonic=0.0)
    probe = breaker.acquire(TOOL_REF, now_monotonic=1.0)
    assert probe.permit is not None
    breaker.record_success(TOOL_REF, probe.permit, now_monotonic=1.0)
    rendered = metrics.render().decode("utf-8")

    assert 'from_state="closed",to_state="open"' in rendered
    assert 'from_state="open",to_state="half_open"' in rendered
    assert 'from_state="half_open",to_state="closed"' in rendered
    assert "demo.telemetry" not in rendered
    assert "probe" not in rendered


def test_opentelemetry_exports_only_allowlisted_stage_and_result_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observability = OpenTelemetryExecutionObservability(
        provider.get_tracer("sentinelmcp.tests"),
    )
    context = _context()
    result = _success()

    with observability.observe_execution(TOOL_REF) as observation:
        with observation.observe_stage(ExecutionStage.REGISTRY_LOOKUP):
            pass
        with observation.observe_attempt(
            context,
            attempt_number=1,
            operation_class=OperationClass.READ,
        ) as attempt:
            attempt.finish(result)
        observation.finish(result, OperationClass.READ)

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        "sentinelmcp.execution",
        "sentinelmcp.execution.attempt",
        "sentinelmcp.stage.registry_lookup",
    }
    attributes = repr(tuple(dict(span.attributes or {}) for span in spans)).lower()
    for forbidden in (
        "authorization",
        "bearer",
        "confirmation_token",
        "idempotency_key",
        "private-token",
    ):
        assert forbidden not in attributes
    root = next(span for span in spans if span.name == "sentinelmcp.execution")
    assert root.attributes is not None
    assert root.attributes["sentinelmcp.outcome"] == "success"
    assert root.attributes["sentinelmcp.attempt.count"] == 1


def test_opentelemetry_records_preview_failures_and_bounded_events() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    metrics = PrometheusMetrics()
    stream = io.StringIO()
    logger = logging.getLogger("sentinelmcp.tests.telemetry.otel")
    logger.handlers = [logging.StreamHandler(stream)]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    observability = OpenTelemetryExecutionObservability(
        provider.get_tracer("sentinelmcp.tests.signals"),
        metrics=metrics,
        logger=StructuredEventLogger(logger),
    )

    with observability.observe_execution(TOOL_REF) as preview:
        preview.finish_preview()
        preview.record_audit_failure(AuditFailurePhase.MUTATION_PREVIEW)
        preview.record_idempotency(IdempotencyMetricOutcome.NEW)
    with observability.observe_execution(TOOL_REF) as execution:
        with execution.observe_attempt(
            _context(),
            attempt_number=1,
            operation_class=OperationClass.READ,
        ) as attempt:
            attempt.finish(_failure())
        execution.finish(_failure(), None)

    rendered_metrics = metrics.render().decode("utf-8")
    assert 'phase="mutation_preview"' in rendered_metrics
    assert 'outcome="new"' in rendered_metrics
    assert 'outcome="upstream_timeout"' in rendered_metrics
    assert "upstream_timeout" in stream.getvalue()
    event_names = {event.name for span in exporter.get_finished_spans() for event in span.events}
    assert "sentinelmcp.audit.persistence_failure" in event_names
    assert "sentinelmcp.mutation.idempotency" in event_names


def test_broken_observability_cannot_change_execution_result() -> None:
    definition = _definition()
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, _Adapter()))
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, OperationClass.READ),)
    )
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=policy,
        observability=_BrokenObservability(),
    )

    result = asyncio.run(
        executor.execute(
            ExecutionRequest(definition.ref, {"value": "go"}),
            principal=Principal("actor", "https://issuer.example"),
        )
    )

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["value"] == "ok"


def test_resilient_observability_isolates_nested_delegate_failures() -> None:
    resilient = ResilientExecutionObservability(_PartiallyBrokenObservability())

    with resilient.observe_execution(TOOL_REF) as observation:
        assert observation.attempt_count == 0
        with observation.observe_stage(ExecutionStage.AUTHORIZATION):
            pass
        with observation.observe_attempt(
            _context(),
            attempt_number=1,
            operation_class=OperationClass.READ,
        ) as attempt:
            attempt.finish(_success())
        assert observation.attempt_count == 1
        observation.finish(_success(), OperationClass.READ)
        observation.finish_preview()
        observation.record_audit_failure(AuditFailurePhase.READ_RESULT)
        observation.record_idempotency(IdempotencyMetricOutcome.CONFLICT)


def test_gateway_observability_isolates_telemetry_and_propagates_body_errors() -> None:
    failing = GatewayObservability(
        tracer=_FailingTracer(),  # type: ignore[arg-type]
        metrics=_FailingMetrics(),  # type: ignore[arg-type]
    )
    with failing.observe(GatewayRoute.EXECUTE) as observation:
        observation.finish(None)
    with failing.observe_authentication():
        pass
    with (
        pytest.raises(TypeError, match="GatewayRoute"),
        failing.observe(
            object()  # type: ignore[arg-type]
        ),
    ):
        pass

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    gateway = GatewayObservability(tracer=provider.get_tracer("sentinelmcp.tests.gateway"))
    with pytest.raises(RuntimeError, match="handler failed"), gateway.observe(GatewayRoute.EXECUTE):
        raise RuntimeError("handler failed")
    with (
        pytest.raises(RuntimeError, match="authentication failed"),
        gateway.observe_authentication(),
    ):
        raise RuntimeError("authentication failed")

    gateway_span = next(
        span for span in exporter.get_finished_spans() if span.name == "sentinelmcp.gateway"
    )
    assert gateway_span.attributes is not None
    assert gateway_span.attributes["sentinelmcp.gateway.outcome"] == "internal_execution_error"


class _Adapter:
    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        del invocation
        return AdapterSuccess({"value": "ok"})


class _BrokenObservability(ExecutionObservability):
    @contextmanager
    def observe_execution(self, tool_ref: ToolRef) -> Iterator[ExecutionObservation]:
        del tool_ref
        raise RuntimeError("telemetry unavailable")
        yield  # type: ignore[unreachable]  # pragma: no cover


class _PartiallyBrokenObservability(ExecutionObservability):
    @contextmanager
    def observe_execution(self, tool_ref: ToolRef) -> Iterator[ExecutionObservation]:
        del tool_ref
        yield _PartiallyBrokenExecution()


class _PartiallyBrokenExecution(ExecutionObservation):
    @property
    def attempt_count(self) -> int:
        raise RuntimeError("attempt count failed")

    @contextmanager
    def observe_stage(self, stage: ExecutionStage) -> Iterator[None]:
        del stage
        raise RuntimeError("stage failed")
        yield  # type: ignore[unreachable]  # pragma: no cover

    @contextmanager
    def observe_attempt(
        self,
        context: ExecutionContext,
        *,
        attempt_number: int,
        operation_class: OperationClass,
    ) -> Iterator[AttemptObservation]:
        del context, attempt_number, operation_class
        raise RuntimeError("attempt failed")
        yield  # type: ignore[unreachable]  # pragma: no cover

    def finish(self, result: ExecutionResult, operation_class: OperationClass | None) -> None:
        del result, operation_class
        raise RuntimeError("finish failed")

    def finish_preview(self) -> None:
        raise RuntimeError("preview failed")

    def record_audit_failure(self, phase: AuditFailurePhase) -> None:
        del phase
        raise RuntimeError("audit metric failed")

    def record_idempotency(self, outcome: IdempotencyMetricOutcome) -> None:
        del outcome
        raise RuntimeError("idempotency metric failed")


class _FailingTracer:
    def start_as_current_span(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("tracer failed")


class _FailingMetrics:
    def record_gateway(self, route: GatewayRoute, outcome: ExecutionErrorCode | None) -> None:
        del route, outcome
        raise RuntimeError("metrics failed")


def test_resilient_observability_rejects_invalid_delegate() -> None:
    invalid = object()
    try:
        ResilientExecutionObservability(invalid)  # type: ignore[arg-type]
    except TypeError as error:
        assert str(error) == "delegate must implement ExecutionObservability"
    else:  # pragma: no cover
        raise AssertionError("invalid observability delegate was accepted")


def _context() -> ExecutionContext:
    return ExecutionContext(
        request_id=REQUEST_ID,
        execution_id=EXECUTION_ID,
        tool_ref=TOOL_REF,
        definition_fingerprint="c" * 64,
        started_at=datetime(2026, 10, 21, tzinfo=UTC),
        started_monotonic=1.0,
    )


def _success() -> ExecutionSuccess:
    return ExecutionSuccess(
        context=_context(),
        result=freeze_json({"value": "ok"}),
        duration_ms=5.0,
    )


def _failure() -> ExecutionFailure:
    return ExecutionFailure(
        context=_context(),
        error=ExecutionError(ExecutionErrorCode.UPSTREAM_TIMEOUT, "safe timeout"),
        duration_ms=8.0,
    )


def _definition() -> ToolDefinition:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    return ToolDefinition(
        namespace=TOOL_REF.namespace,
        name=TOOL_REF.name,
        version=TOOL_REF.version,
        description="Telemetry test tool.",
        input_schema=schema,
        output_schema=schema,
        operation_class=OperationClass.READ,
    )
