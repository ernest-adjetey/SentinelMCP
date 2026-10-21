"""Deterministic reliability state machines and executor integration."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from sentinelmcp.application import PreparedExecution, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
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
    AdmissionDecision,
    CircuitBreakerConfig,
    CircuitDecision,
    CircuitPermit,
    Deadline,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    InMemoryCircuitBreaker,
    InMemoryRateLimiter,
    InMemoryRetryBudget,
    InMemoryRuntimeBindingRegistry,
    RateLimitKey,
    ReliabilityControls,
    RuntimeToolBinding,
    TokenBucketConfig,
    ToolAdapter,
    full_jitter_delay_seconds,
)
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

PRINCIPAL = Principal("reliability-user", "https://issuer.test")


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.delays: list[float] = []

    def __call__(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.value += seconds


class SequenceAdapter:
    def __init__(self, results: list[AdapterResult]) -> None:
        self._results = iter(results)
        self.invocations: list[AdapterInvocation] = []

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.invocations.append(invocation)
        return next(self._results)


class SlowAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _invocation: AdapterInvocation) -> AdapterResult:
        self.calls += 1
        await asyncio.sleep(1)
        return AdapterSuccess({"value": "late"})


class RaisingRateLimiter:
    def acquire(self, key: RateLimitKey, *, now_monotonic: float) -> AdmissionDecision:
        del key, now_monotonic
        raise RuntimeError("rate state failed")


class RaisingRetryBudget:
    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> bool:
        del tool_ref, now_monotonic
        raise RuntimeError("retry state failed")


class RaisingCircuitBreaker:
    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> CircuitDecision:
        del tool_ref, now_monotonic
        raise RuntimeError("circuit state failed")

    def record_success(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        del tool_ref, permit, now_monotonic

    def record_failure(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        del tool_ref, permit, now_monotonic

    def record_neutral(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        del tool_ref, permit, now_monotonic


class RaisingRecorderCircuitBreaker(RaisingCircuitBreaker):
    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> CircuitDecision:
        del tool_ref, now_monotonic
        return CircuitDecision(CircuitPermit(False, 0))

    def record_success(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        del tool_ref, permit, now_monotonic
        raise RuntimeError("circuit record failed")


async def raising_sleep(_seconds: float) -> None:
    raise RuntimeError("sleep failed")


def definition(
    *,
    operation: OperationClass = OperationClass.READ,
    retry_policy: RetryPolicy | None = None,
    timeout_policy: TimeoutPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.reliability"),
        name=ToolName("probe"),
        version=ToolVersion("1.0.0"),
        description="Exercise deterministic reliability controls.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        operation_class=operation,
        retry_policy=RetryPolicy() if retry_policy is None else retry_policy,
        timeout_policy=TimeoutPolicy() if timeout_policy is None else timeout_policy,
        idempotency_supported=operation is OperationClass.MUTATION,
    )


def executor_for(
    adapter: ToolAdapter,
    *,
    tool: ToolDefinition | None = None,
    clock: FakeClock | None = None,
    controls: ReliabilityControls | None = None,
) -> tuple[ToolExecutor, ToolDefinition]:
    selected = definition() if tool is None else tool
    registry = InMemoryToolRegistry()
    registry.register(selected)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(selected.ref, adapter))
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, selected.identity, selected.operation_class),)
    )
    monotonic = FakeClock() if clock is None else clock
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=policy,
        monotonic=monotonic,
        reliability=controls,
    )
    return executor, selected


def test_deadline_and_full_jitter_use_bounded_request_policy() -> None:
    deadline = Deadline.from_timeout_ms(started_monotonic=10.0, timeout_ms=1_500)
    policy = RetryPolicy(max_attempts=5, initial_delay_ms=100, maximum_delay_ms=250)

    assert deadline.expires_monotonic == 11.5
    assert deadline.remaining_seconds(10.25) == 1.25
    assert deadline.expired(11.5)
    assert full_jitter_delay_seconds(policy, retry_number=1, random_unit=0.5) == 0.05
    assert full_jitter_delay_seconds(policy, retry_number=2, random_unit=1) == 0.2
    assert full_jitter_delay_seconds(policy, retry_number=3, random_unit=1) == 0.25
    with pytest.raises(ValueError, match="retry_number"):
        full_jitter_delay_seconds(policy, retry_number=0, random_unit=0.5)
    with pytest.raises(ValueError, match="random_unit"):
        full_jitter_delay_seconds(policy, retry_number=1, random_unit=1.1)


def test_reliability_values_reject_invalid_configuration_and_decisions() -> None:
    ref = ToolRef.parse("demo.reliability.probe@1.0.0")
    limiter = InMemoryRateLimiter(TokenBucketConfig(1, 1))
    budget = InMemoryRetryBudget(TokenBucketConfig(1, 1))

    with pytest.raises(ValueError, match="deadline"):
        Deadline(-1)
    with pytest.raises(ValueError, match="timeout_ms"):
        Deadline.from_timeout_ms(started_monotonic=0, timeout_ms=0)
    with pytest.raises(TypeError, match="allowed"):
        AdmissionDecision(cast(Any, 1))
    with pytest.raises(ValueError, match="denied"):
        AdmissionDecision(True, 1)
    with pytest.raises(ValueError, match="denied"):
        AdmissionDecision(False)
    with pytest.raises(TypeError, match="capacity"):
        TokenBucketConfig(cast(Any, True), 1)
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketConfig(0, 1)
    with pytest.raises(ValueError, match="refill"):
        TokenBucketConfig(1, 0)
    with pytest.raises(TypeError, match="rate-limit key"):
        limiter.acquire(cast(Any, ref), now_monotonic=0)
    with pytest.raises(TypeError, match="retry-budget key"):
        budget.acquire(cast(Any, "bad"), now_monotonic=0)
    with pytest.raises(ValueError, match="threshold"):
        CircuitBreakerConfig(failure_threshold=0)
    with pytest.raises(ValueError, match="open_seconds"):
        CircuitBreakerConfig(open_seconds=0)
    with pytest.raises(ValueError, match="probe"):
        CircuitBreakerConfig(half_open_max_probes=0)
    with pytest.raises(TypeError, match="generation"):
        CircuitPermit(False, cast(Any, "bad"))
    with pytest.raises(ValueError, match="generation"):
        CircuitPermit(False, -1)
    with pytest.raises(ValueError, match="either"):
        CircuitDecision(None)
    with pytest.raises(ValueError, match="either"):
        CircuitDecision(CircuitPermit(False, 0), 1)
    with pytest.raises(TypeError, match="rate_limiter"):
        ReliabilityControls(rate_limiter=cast(Any, object()))


def test_rate_limiter_refills_and_scopes_by_verified_principal() -> None:
    limiter = InMemoryRateLimiter(TokenBucketConfig(capacity=2, refill_per_second=1))
    ref = ToolRef.parse("demo.reliability.probe@1.0.0")
    first_key = RateLimitKey.from_request(PRINCIPAL, ref)
    second_key = RateLimitKey.from_request(Principal("other", PRINCIPAL.issuer), ref)

    assert limiter.acquire(first_key, now_monotonic=0).allowed
    assert limiter.acquire(first_key, now_monotonic=0).allowed
    denied = limiter.acquire(first_key, now_monotonic=0)
    half_refilled = limiter.acquire(first_key, now_monotonic=0.5)

    assert not denied.allowed
    assert denied.retry_after_seconds == 1
    assert not half_refilled.allowed
    assert half_refilled.retry_after_seconds == 0.5
    assert limiter.acquire(first_key, now_monotonic=1).allowed
    assert limiter.acquire(second_key, now_monotonic=0).allowed


def test_retry_budget_bounds_aggregate_retries_per_exact_tool() -> None:
    budget = InMemoryRetryBudget(TokenBucketConfig(capacity=1, refill_per_second=0.5))
    first = ToolRef.parse("demo.reliability.probe@1.0.0")
    second = ToolRef.parse("demo.reliability.probe@2.0.0")

    assert budget.acquire(first, now_monotonic=0)
    assert not budget.acquire(first, now_monotonic=0)
    assert not budget.acquire(first, now_monotonic=1)
    assert budget.acquire(first, now_monotonic=2)
    assert budget.acquire(second, now_monotonic=0)


def test_circuit_breaker_opens_limits_probe_and_closes_on_success() -> None:
    breaker = InMemoryCircuitBreaker(
        CircuitBreakerConfig(failure_threshold=2, open_seconds=5, half_open_max_probes=1)
    )
    ref = ToolRef.parse("demo.reliability.probe@1.0.0")

    first = breaker.acquire(ref, now_monotonic=0)
    assert first.permit is not None
    breaker.record_failure(ref, first.permit, now_monotonic=0)
    second = breaker.acquire(ref, now_monotonic=0)
    assert second.permit is not None
    breaker.record_failure(ref, second.permit, now_monotonic=0)
    breaker.record_success(ref, first.permit, now_monotonic=0)

    denied = breaker.acquire(ref, now_monotonic=0)
    assert not denied.allowed
    assert denied.retry_after_seconds == 5

    probe = breaker.acquire(ref, now_monotonic=5)
    assert probe.permit is not None and probe.permit.half_open_probe
    assert not breaker.acquire(ref, now_monotonic=5).allowed
    breaker.record_success(ref, probe.permit, now_monotonic=5)
    assert breaker.acquire(ref, now_monotonic=5).allowed


def test_transient_read_failures_retry_with_new_execution_ids_and_full_jitter() -> None:
    clock = FakeClock()
    adapter = SequenceAdapter(
        [
            AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
            AdapterFailure(ExecutionErrorCode.UPSTREAM_SERVER_ERROR, 503),
            AdapterSuccess({"value": "ready"}),
        ]
    )
    tool = definition(
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_ms=100, maximum_delay_ms=500)
    )
    controls = ReliabilityControls(random_unit=lambda: 0.5, sleep=clock.sleep)
    executor, _ = executor_for(adapter, tool=tool, clock=clock, controls=controls)

    result = asyncio.run(executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL))

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["value"] == "ready"
    assert clock.delays == [0.05, 0.1]
    assert len(adapter.invocations) == 3
    assert len({call.context.execution_id for call in adapter.invocations}) == 3
    assert len({call.context.request_id for call in adapter.invocations}) == 1


def test_non_retryable_failure_and_exhausted_budget_do_not_amplify_calls() -> None:
    non_retryable = SequenceAdapter(
        [
            AdapterFailure(ExecutionErrorCode.UPSTREAM_BAD_REQUEST, 400),
            AdapterSuccess({"value": "unexpected"}),
        ]
    )
    tool = definition(retry_policy=RetryPolicy(max_attempts=3))
    first_executor, _ = executor_for(non_retryable, tool=tool)
    first = asyncio.run(first_executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL))

    budgeted = SequenceAdapter(
        [
            AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
            AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
            AdapterSuccess({"value": "unexpected"}),
        ]
    )
    clock = FakeClock()
    budget = InMemoryRetryBudget(TokenBucketConfig(capacity=1, refill_per_second=0.001))
    controls = ReliabilityControls(
        retry_budget=budget,
        random_unit=lambda: 0,
        sleep=clock.sleep,
    )
    second_executor, _ = executor_for(budgeted, tool=tool, clock=clock, controls=controls)
    second = asyncio.run(
        second_executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL)
    )

    assert isinstance(first, ExecutionFailure)
    assert first.error.code is ExecutionErrorCode.UPSTREAM_BAD_REQUEST
    assert len(non_retryable.invocations) == 1
    assert isinstance(second, ExecutionFailure)
    assert second.error.code is ExecutionErrorCode.UPSTREAM_UNAVAILABLE
    assert len(budgeted.invocations) == 2


def test_expired_deadline_prevents_dispatch_and_slow_attempt_is_cancelled() -> None:
    clock = FakeClock()
    adapter = SequenceAdapter([AdapterSuccess({"value": "unexpected"})])
    tool = definition(timeout_policy=TimeoutPolicy(timeout_ms=1))
    executor, _ = executor_for(adapter, tool=tool, clock=clock)
    prepared = executor.prepare(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL)
    assert isinstance(prepared, PreparedExecution)
    binding = executor.resolve_binding(prepared)
    assert isinstance(binding, RuntimeToolBinding)
    clock.value = 1

    expired = asyncio.run(executor.dispatch(prepared, binding))

    assert isinstance(expired, ExecutionFailure)
    assert expired.error.code is ExecutionErrorCode.DEADLINE_EXCEEDED
    assert adapter.invocations == []

    slow = SlowAdapter()
    slow_tool = definition(timeout_policy=TimeoutPolicy(timeout_ms=10))
    slow_executor, _ = executor_for(slow, tool=slow_tool)
    timed_out = asyncio.run(
        slow_executor.execute(ExecutionRequest(slow_tool.ref, {}), principal=PRINCIPAL)
    )

    assert isinstance(timed_out, ExecutionFailure)
    assert timed_out.error.code is ExecutionErrorCode.UPSTREAM_TIMEOUT
    assert slow.calls == 1


def test_rate_limit_and_open_circuit_fail_before_adapter_dispatch() -> None:
    adapter = SequenceAdapter(
        [
            AdapterSuccess({"value": "first"}),
            AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
        ]
    )
    limiter = InMemoryRateLimiter(TokenBucketConfig(capacity=1, refill_per_second=0.001))
    rate_controls = ReliabilityControls(rate_limiter=limiter)
    executor, tool = executor_for(adapter, controls=rate_controls)
    first = asyncio.run(executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL))
    limited = asyncio.run(executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL))

    assert isinstance(first, ExecutionSuccess)
    assert isinstance(limited, ExecutionFailure)
    assert limited.error.code is ExecutionErrorCode.RATE_LIMITED
    assert limited.error.retry_after_ms == 300_000
    assert len(adapter.invocations) == 1

    failing = SequenceAdapter([AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)])
    breaker = InMemoryCircuitBreaker(
        CircuitBreakerConfig(failure_threshold=1, open_seconds=10, half_open_max_probes=1)
    )
    circuit_controls = ReliabilityControls(circuit_breaker=breaker)
    circuit_executor, circuit_tool = executor_for(failing, controls=circuit_controls)
    failure = asyncio.run(
        circuit_executor.execute(ExecutionRequest(circuit_tool.ref, {}), principal=PRINCIPAL)
    )
    open_result = asyncio.run(
        circuit_executor.execute(ExecutionRequest(circuit_tool.ref, {}), principal=PRINCIPAL)
    )

    assert isinstance(failure, ExecutionFailure)
    assert isinstance(open_result, ExecutionFailure)
    assert open_result.error.code is ExecutionErrorCode.CIRCUIT_OPEN
    assert open_result.error.retry_after_ms == 10_000
    assert len(failing.invocations) == 1


def test_reliability_control_failures_are_bounded_and_never_blindly_retry() -> None:
    tool = definition(retry_policy=RetryPolicy(max_attempts=3))

    rate_adapter = SequenceAdapter([AdapterSuccess({"value": "unexpected"})])
    rate_executor, _ = executor_for(
        rate_adapter,
        tool=tool,
        controls=ReliabilityControls(rate_limiter=RaisingRateLimiter()),
    )
    rate_result = asyncio.run(
        rate_executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL)
    )

    circuit_adapter = SequenceAdapter([AdapterSuccess({"value": "unexpected"})])
    circuit_executor, _ = executor_for(
        circuit_adapter,
        tool=tool,
        controls=ReliabilityControls(circuit_breaker=RaisingCircuitBreaker()),
    )
    circuit_result = asyncio.run(
        circuit_executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL)
    )

    budget_adapter = SequenceAdapter([AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)])
    budget_executor, _ = executor_for(
        budget_adapter,
        tool=tool,
        controls=ReliabilityControls(retry_budget=RaisingRetryBudget()),
    )
    budget_result = asyncio.run(
        budget_executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL)
    )

    assert isinstance(rate_result, ExecutionFailure)
    assert rate_result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert rate_adapter.invocations == []
    assert isinstance(circuit_result, ExecutionFailure)
    assert circuit_result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert circuit_adapter.invocations == []
    assert isinstance(budget_result, ExecutionFailure)
    assert budget_result.error.code is ExecutionErrorCode.UPSTREAM_UNAVAILABLE
    assert len(budget_adapter.invocations) == 1


@pytest.mark.parametrize(
    "controls",
    [
        ReliabilityControls(random_unit=lambda: float("nan")),
        ReliabilityControls(random_unit=lambda: 0, sleep=raising_sleep),
    ],
)
def test_invalid_retry_hooks_fail_safely(controls: ReliabilityControls) -> None:
    adapter = SequenceAdapter([AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)])
    tool = definition(retry_policy=RetryPolicy(max_attempts=2))
    executor, _ = executor_for(adapter, tool=tool, controls=controls)

    result = asyncio.run(executor.execute(ExecutionRequest(tool.ref, {}), principal=PRINCIPAL))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR
    assert len(adapter.invocations) == 1


def test_retry_delay_cannot_overrun_deadline_and_recorder_failure_is_non_fatal() -> None:
    clock = FakeClock()
    delayed = SequenceAdapter([AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)])
    deadline_tool = definition(
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_ms=1_000),
        timeout_policy=TimeoutPolicy(timeout_ms=500),
    )
    deadline_executor, _ = executor_for(
        delayed,
        tool=deadline_tool,
        clock=clock,
        controls=ReliabilityControls(random_unit=lambda: 1, sleep=clock.sleep),
    )

    deadline_result = asyncio.run(
        deadline_executor.execute(ExecutionRequest(deadline_tool.ref, {}), principal=PRINCIPAL)
    )

    assert isinstance(deadline_result, ExecutionFailure)
    assert deadline_result.error.code is ExecutionErrorCode.DEADLINE_EXCEEDED
    assert clock.delays == []

    successful = SequenceAdapter([AdapterSuccess({"value": "ready"})])
    recorder_executor, recorder_tool = executor_for(
        successful,
        controls=ReliabilityControls(circuit_breaker=RaisingRecorderCircuitBreaker()),
    )
    recorder_result = asyncio.run(
        recorder_executor.execute(
            ExecutionRequest(recorder_tool.ref, {}),
            principal=PRINCIPAL,
        )
    )

    assert isinstance(recorder_result, ExecutionSuccess)
    assert len(successful.invocations) == 1
