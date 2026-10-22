"""Execute deterministic scenarios through SentinelMCP's production orchestration paths."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import final

import httpx

from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    RetryPolicy,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.evaluation.catalog import ScenarioCatalog, load_phase11_catalog
from sentinelmcp.evaluation.models import (
    EvaluationReport,
    EvaluationScenario,
    OutcomeAttribution,
    ScenarioKind,
    ScenarioResult,
)
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    HTTPToolAdapter,
    HTTPToolTarget,
    InMemoryRuntimeBindingRegistry,
    OutboundHTTPPolicy,
    ReliabilityControls,
    RuntimeToolBinding,
)
from sentinelmcp.fault_injection import (
    DeterministicHTTPTransport,
    DeterministicMutationStateStore,
    HTTPFaultKind,
    HTTPFaultStep,
)
from sentinelmcp.mutation_guard import MutationPreview
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

_HTTP_URL = "https://faults.example.test/invoke"
_HTTP_MAX_RESPONSE_BYTES = 64


async def _resolve_evaluation_host(host: str, port: int) -> tuple[str, ...]:
    if host != "faults.example.test" or port != 443:
        raise OSError("unexpected deterministic evaluation target")
    return ("93.184.216.34",)


@dataclass(frozen=True, slots=True)
class _Observed:
    error: ExecutionErrorCode | None
    dispatches: int
    labels: tuple[str, ...] = ()


@final
class _CountingMutationAdapter:
    __slots__ = ("calls",)

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        del invocation
        self.calls += 1
        return AdapterSuccess({"ok": True})


async def run_phase11_evaluation(
    *,
    principal: Principal | None = None,
    catalog: ScenarioCatalog | None = None,
) -> EvaluationReport:
    """Run the packaged suite and return observed values without synthetic pass inflation."""

    selected_catalog = load_phase11_catalog() if catalog is None else catalog
    actor = Principal("evaluation-runner", "urn:sentinelmcp:evaluation")
    if principal is not None:
        actor = principal
    started = time.perf_counter_ns()
    results = []
    for scenario in selected_catalog.scenarios:
        results.append(await _run_scenario(scenario, actor))
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return EvaluationReport(
        suite_id=selected_catalog.suite_id,
        suite_version=selected_catalog.suite_version,
        generated_at=datetime.now(UTC),
        elapsed_ms=elapsed_ms,
        results=tuple(results),
    )


async def _run_scenario(
    scenario: EvaluationScenario,
    principal: Principal,
) -> ScenarioResult:
    started = time.perf_counter_ns()
    if scenario.kind in {ScenarioKind.HTTP, ScenarioKind.POLICY_REJECTION}:
        observed = await _run_http_scenario(scenario, principal)
    else:
        observed = await _run_mutation_scenario(scenario, principal)
    duration_ms = (time.perf_counter_ns() - started) / 1_000_000
    attribution = attribute_error(observed.error)
    passed = (
        observed.error is scenario.expected_error
        and attribution is scenario.expected_attribution
        and observed.dispatches == scenario.expected_dispatches
    )
    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        passed=passed,
        expected_attribution=scenario.expected_attribution,
        observed_attribution=attribution,
        expected_error=scenario.expected_error,
        observed_error=observed.error,
        expected_dispatches=scenario.expected_dispatches,
        observed_dispatches=observed.dispatches,
        duration_ms=duration_ms,
        labels=observed.labels,
    )


async def _run_http_scenario(
    scenario: EvaluationScenario,
    principal: Principal,
) -> _Observed:
    definition = _http_definition(
        attempts=len(scenario.fault_steps) if scenario.retry_recovered else 1
    )
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    steps = scenario.fault_steps or (HTTPFaultStep(HTTPFaultKind.SUCCESS),)
    transport = DeterministicHTTPTransport(steps)
    async with httpx.AsyncClient(transport=transport) as client:
        bindings.bind(
            RuntimeToolBinding(
                definition.ref,
                HTTPToolAdapter(
                    client,
                    HTTPToolTarget(
                        _HTTP_URL,
                        max_response_bytes=_HTTP_MAX_RESPONSE_BYTES,
                    ),
                    policy=OutboundHTTPPolicy(resolver=_resolve_evaluation_host),
                ),
            )
        )
        rules: tuple[AuthorizationRule, ...] = ()
        if scenario.kind is ScenarioKind.HTTP:
            rules = (
                AuthorizationRule(
                    PolicyEffect.ALLOW,
                    definition.identity,
                    OperationClass.READ,
                ),
            )
        executor = ToolExecutor(
            registry,
            bindings,
            authorization_policy=RuleBasedAuthorizationPolicy(rules),
            reliability=ReliabilityControls(random_unit=lambda: 0.0, sleep=_no_sleep),
        )
        result = await executor.execute(
            ExecutionRequest(definition.ref, {"value": "evaluation"}),
            principal=principal,
        )
    error = result.error.code if isinstance(result, ExecutionFailure) else None
    labels: tuple[str, ...] = (
        ("retry_recovered",) if scenario.retry_recovered and error is None else ()
    )
    if scenario.scenario_id == "slow-response" and error is None:
        labels += ("slow_response",)
    return _Observed(error, transport.request_count, labels)


async def _run_mutation_scenario(
    scenario: EvaluationScenario,
    principal: Principal,
) -> _Observed:
    definition = _mutation_definition()
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    adapter = _CountingMutationAdapter()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (
                AuthorizationRule(
                    PolicyEffect.ALLOW,
                    definition.identity,
                    OperationClass.MUTATION,
                ),
            )
        ),
    )
    store = DeterministicMutationStateStore(
        fail_completion=scenario.kind is ScenarioKind.MUTATION_AMBIGUITY
    )
    service = MutationService(executor, store, token_factory=lambda: "e" * 43)
    request = ExecutionRequest(definition.ref, {"value": "evaluation"})
    preview = service.preview(request, principal=principal)
    if not isinstance(preview, MutationPreview):
        return _Observed(preview.error.code, adapter.calls)
    first = await service.confirm(
        request,
        principal=principal,
        confirmation_token=preview.confirmation_token,
        idempotency_key=f"phase11-{scenario.scenario_id}",
    )
    if scenario.kind is ScenarioKind.DUPLICATE_REQUEST:
        second = await service.confirm(
            request,
            principal=principal,
            confirmation_token="u" * 43,
            idempotency_key=f"phase11-{scenario.scenario_id}",
        )
        if isinstance(first, ExecutionSuccess) and isinstance(second, ExecutionSuccess):
            return _Observed(None, adapter.calls, ("duplicate_reused",))
        failed = first if isinstance(first, ExecutionFailure) else second
        assert isinstance(failed, ExecutionFailure)
        return _Observed(failed.error.code, adapter.calls)
    error = first.error.code if isinstance(first, ExecutionFailure) else None
    labels = ("outcome_ambiguous",) if error is ExecutionErrorCode.MUTATION_OUTCOME_UNKNOWN else ()
    return _Observed(error, adapter.calls, labels)


def attribute_error(error: ExecutionErrorCode | None) -> OutcomeAttribution:
    """Map a normalized execution result to one primary attribution."""

    if error is None:
        return OutcomeAttribution.SUCCESS
    if error in {
        ExecutionErrorCode.AUTHENTICATION_FAILED,
        ExecutionErrorCode.AUTHORIZATION_FAILED,
        ExecutionErrorCode.INPUT_VALIDATION_FAILED,
        ExecutionErrorCode.MUTATION_NOT_SUPPORTED,
        ExecutionErrorCode.MUTATION_CONFIRMATION_INVALID,
        ExecutionErrorCode.IDEMPOTENCY_CONFLICT,
        ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS,
        ExecutionErrorCode.RATE_LIMITED,
        ExecutionErrorCode.CIRCUIT_OPEN,
    }:
        return OutcomeAttribution.POLICY_REJECTION
    if error in {
        ExecutionErrorCode.UPSTREAM_BAD_REQUEST,
        ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED,
        ExecutionErrorCode.UPSTREAM_AUTHORIZATION_FAILED,
        ExecutionErrorCode.UPSTREAM_NOT_FOUND,
        ExecutionErrorCode.UPSTREAM_CONFLICT,
        ExecutionErrorCode.UPSTREAM_RATE_LIMITED,
        ExecutionErrorCode.UPSTREAM_STATUS_ERROR,
        ExecutionErrorCode.UPSTREAM_SERVER_ERROR,
        ExecutionErrorCode.OUTPUT_VALIDATION_FAILED,
    }:
        return OutcomeAttribution.TOOL_ERROR
    return OutcomeAttribution.INFRASTRUCTURE_ERROR


async def _no_sleep(delay: float) -> None:
    del delay


def _http_definition(*, attempts: int) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("evaluation.http"),
        name=ToolName("invoke"),
        version=ToolVersion("1.0.0"),
        description="Invoke a deterministic dependency for fault evaluation.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
        retry_policy=RetryPolicy(
            max_attempts=attempts,
            initial_delay_ms=0,
            maximum_delay_ms=0,
        ),
        capabilities=frozenset({"evaluation", "synthetic"}),
    )


def _mutation_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("evaluation.mutation"),
        name=ToolName("update"),
        version=ToolVersion("1.0.0"),
        description="Apply a deterministic mutation for safety evaluation.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.MUTATION,
        idempotency_supported=True,
        capabilities=frozenset({"evaluation", "synthetic"}),
    )
