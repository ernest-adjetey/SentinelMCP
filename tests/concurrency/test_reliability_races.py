"""Process-local reliability arbitration under thread contention."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import (
    CircuitBreakerConfig,
    InMemoryCircuitBreaker,
    InMemoryRateLimiter,
    InMemoryRetryBudget,
    RateLimitKey,
    TokenBucketConfig,
)
from sentinelmcp.policy import Principal

WORKERS = 32
TOOL_REF = ToolRef.parse("demo.reliability.probe@1.0.0")


def test_rate_limit_capacity_is_atomic_under_32_way_contention() -> None:
    limiter = InMemoryRateLimiter(TokenBucketConfig(capacity=8, refill_per_second=1))
    key = RateLimitKey.from_request(Principal("race-user", "https://issuer.test"), TOOL_REF)
    barrier = Barrier(WORKERS)

    def acquire(_index: int) -> bool:
        barrier.wait()
        return limiter.acquire(key, now_monotonic=0).allowed

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        decisions = tuple(executor.map(acquire, range(WORKERS)))

    assert sum(decisions) == 8


def test_retry_budget_capacity_is_atomic_under_32_way_contention() -> None:
    budget = InMemoryRetryBudget(TokenBucketConfig(capacity=8, refill_per_second=1))
    barrier = Barrier(WORKERS)

    def acquire(_index: int) -> bool:
        barrier.wait()
        return budget.acquire(TOOL_REF, now_monotonic=0)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        decisions = tuple(executor.map(acquire, range(WORKERS)))

    assert sum(decisions) == 8


def test_half_open_probe_limit_is_atomic_under_32_way_contention() -> None:
    breaker = InMemoryCircuitBreaker(
        CircuitBreakerConfig(failure_threshold=1, open_seconds=1, half_open_max_probes=1)
    )
    initial = breaker.acquire(TOOL_REF, now_monotonic=0)
    assert initial.permit is not None
    breaker.record_failure(TOOL_REF, initial.permit, now_monotonic=0)
    barrier = Barrier(WORKERS)

    def acquire(_index: int) -> bool:
        barrier.wait()
        return breaker.acquire(TOOL_REF, now_monotonic=1).allowed

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        decisions = tuple(executor.map(acquire, range(WORKERS)))

    assert sum(decisions) == 1
