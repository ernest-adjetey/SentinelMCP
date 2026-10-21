"""Deterministic reliability controls for bounded tool execution."""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from typing import Protocol, final, runtime_checkable

from sentinelmcp.domain import RetryPolicy, ToolRef
from sentinelmcp.execution.models import ExecutionErrorCode
from sentinelmcp.policy import Principal

type AsyncSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Deadline:
    """One absolute monotonic deadline shared by every execution attempt."""

    expires_monotonic: float

    def __post_init__(self) -> None:
        _require_finite_non_negative(self.expires_monotonic, field="deadline")

    @classmethod
    def from_timeout_ms(cls, *, started_monotonic: float, timeout_ms: int) -> Deadline:
        _require_finite_non_negative(started_monotonic, field="started_monotonic")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        return cls(started_monotonic + timeout_ms / 1_000)

    def remaining_seconds(self, now_monotonic: float) -> float:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        return max(0.0, self.expires_monotonic - now_monotonic)

    def expired(self, now_monotonic: float) -> bool:
        return self.remaining_seconds(now_monotonic) <= 0


@dataclass(frozen=True, slots=True)
class RateLimitKey:
    """Trusted bounded admission scope derived from identity and exact tool reference."""

    principal_issuer: str
    principal_subject: str
    tool_ref: ToolRef

    @classmethod
    def from_request(cls, principal: Principal, tool_ref: ToolRef) -> RateLimitKey:
        return cls(principal.issuer, principal.subject, tool_ref)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Result of a local rate, retry-budget, or circuit admission check."""

    allowed: bool
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a boolean")
        if self.allowed is (self.retry_after_seconds is not None):
            raise ValueError("only denied decisions carry retry guidance")
        if self.retry_after_seconds is not None:
            _require_finite_non_negative(
                self.retry_after_seconds,
                field="retry_after_seconds",
            )


@runtime_checkable
class RateLimiter(Protocol):
    """Admit one caller request against trusted identity and tool scope."""

    def acquire(self, key: RateLimitKey, *, now_monotonic: float) -> AdmissionDecision:
        """Consume one admission token or return bounded retry guidance."""


@runtime_checkable
class RetryBudget(Protocol):
    """Bound aggregate retry amplification independently of per-request attempts."""

    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> bool:
        """Consume one retry token for an exact tool version."""


@dataclass(frozen=True, slots=True)
class TokenBucketConfig:
    """Capacity and continuous refill rate for a process-local token bucket."""

    capacity: int
    refill_per_second: float

    def __post_init__(self) -> None:
        if isinstance(self.capacity, bool) or not isinstance(self.capacity, int):
            raise TypeError("token bucket capacity must be an integer")
        if not 1 <= self.capacity <= 1_000_000:
            raise ValueError("token bucket capacity must be from 1 through 1,000,000")
        if (
            isinstance(self.refill_per_second, bool)
            or not isinstance(self.refill_per_second, int | float)
            or not math.isfinite(self.refill_per_second)
            or self.refill_per_second <= 0
            or self.refill_per_second > 1_000_000
        ):
            raise ValueError("token bucket refill rate must be finite and positive")


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_monotonic: float


@final
class InMemoryRateLimiter(RateLimiter):
    """Thread-safe token buckets scoped by verified principal and exact tool."""

    __slots__ = ("_buckets", "_config", "_lock")

    def __init__(self, config: TokenBucketConfig) -> None:
        self._config = config
        self._buckets: dict[RateLimitKey, _Bucket] = {}
        self._lock = Lock()

    def acquire(self, key: RateLimitKey, *, now_monotonic: float) -> AdmissionDecision:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        if not isinstance(key, RateLimitKey):
            raise TypeError("rate-limit key must be a RateLimitKey")
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(float(self._config.capacity), now_monotonic)
                self._buckets[key] = bucket
            _refill(bucket, self._config, now_monotonic)
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return AdmissionDecision(True)
            retry_after = (1 - bucket.tokens) / self._config.refill_per_second
            return AdmissionDecision(False, retry_after)


@final
class InMemoryRetryBudget(RetryBudget):
    """Thread-safe retry tokens scoped by exact tool version."""

    __slots__ = ("_buckets", "_config", "_lock")

    def __init__(self, config: TokenBucketConfig) -> None:
        self._config = config
        self._buckets: dict[ToolRef, _Bucket] = {}
        self._lock = Lock()

    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> bool:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        if not isinstance(tool_ref, ToolRef):
            raise TypeError("retry-budget key must be a ToolRef")
        with self._lock:
            bucket = self._buckets.get(tool_ref)
            if bucket is None:
                bucket = _Bucket(float(self._config.capacity), now_monotonic)
                self._buckets[tool_ref] = bucket
            _refill(bucket, self._config, now_monotonic)
            if bucket.tokens < 1:
                return False
            bucket.tokens -= 1
            return True


@final
class UnlimitedRateLimiter(RateLimiter):
    def acquire(self, key: RateLimitKey, *, now_monotonic: float) -> AdmissionDecision:
        del key, now_monotonic
        return AdmissionDecision(True)


@final
class UnlimitedRetryBudget(RetryBudget):
    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> bool:
        del tool_ref, now_monotonic
        return True


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    open_seconds: float = 30.0
    half_open_max_probes: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.failure_threshold, bool)
            or not isinstance(self.failure_threshold, int)
            or not 1 <= self.failure_threshold <= 10_000
        ):
            raise ValueError("failure threshold must be from 1 through 10,000")
        _require_finite_positive(self.open_seconds, field="open_seconds")
        if (
            isinstance(self.half_open_max_probes, bool)
            or not isinstance(self.half_open_max_probes, int)
            or not 1 <= self.half_open_max_probes <= 1_000
        ):
            raise ValueError("half-open probe count must be from 1 through 1,000")


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    half_open_probe: bool
    generation: int

    def __post_init__(self) -> None:
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("circuit generation must be an integer")
        if self.generation < 0:
            raise ValueError("circuit generation must be non-negative")


@dataclass(frozen=True, slots=True)
class CircuitDecision:
    permit: CircuitPermit | None
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if (self.permit is None) is (self.retry_after_seconds is None):
            raise ValueError("circuit decisions contain either a permit or retry guidance")
        if self.retry_after_seconds is not None:
            _require_finite_non_negative(
                self.retry_after_seconds,
                field="retry_after_seconds",
            )

    @property
    def allowed(self) -> bool:
        return self.permit is not None


@runtime_checkable
class CircuitBreaker(Protocol):
    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> CircuitDecision:
        """Admit one call or reject while a dependency circuit is open."""

    def record_success(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        """Record one dependency-health success."""

    def record_failure(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        """Record one dependency-health failure."""

    def record_neutral(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        """Release a probe for an outcome unrelated to dependency health."""


@dataclass(slots=True)
class _CircuitRecord:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_monotonic: float | None = None
    half_open_in_flight: int = 0
    generation: int = 0


@final
class InMemoryCircuitBreaker(CircuitBreaker):
    """Thread-safe process-local circuit state keyed by exact tool version."""

    __slots__ = ("_config", "_lock", "_records")

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = CircuitBreakerConfig() if config is None else config
        self._records: dict[ToolRef, _CircuitRecord] = {}
        self._lock = Lock()

    def acquire(self, tool_ref: ToolRef, *, now_monotonic: float) -> CircuitDecision:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        with self._lock:
            record = self._records.setdefault(tool_ref, _CircuitRecord())
            if record.state is CircuitState.OPEN:
                assert record.opened_monotonic is not None
                elapsed = max(0.0, now_monotonic - record.opened_monotonic)
                remaining = max(0.0, self._config.open_seconds - elapsed)
                if remaining > 0:
                    return CircuitDecision(None, remaining)
                record.state = CircuitState.HALF_OPEN
                record.half_open_in_flight = 0
            if record.state is CircuitState.HALF_OPEN:
                if record.half_open_in_flight >= self._config.half_open_max_probes:
                    return CircuitDecision(None, 0.0)
                record.half_open_in_flight += 1
                return CircuitDecision(CircuitPermit(True, record.generation))
            return CircuitDecision(CircuitPermit(False, record.generation))

    def record_success(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        with self._lock:
            record = self._records.setdefault(tool_ref, _CircuitRecord())
            if permit.generation != record.generation:
                return
            if permit.half_open_probe:
                self._records[tool_ref] = _CircuitRecord(generation=record.generation + 1)
                return
            if record.state is CircuitState.CLOSED:
                record.consecutive_failures = 0

    def record_failure(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        with self._lock:
            record = self._records.setdefault(tool_ref, _CircuitRecord())
            if permit.generation != record.generation:
                return
            if permit.half_open_probe:
                self._open(record, now_monotonic)
                return
            record.consecutive_failures += 1
            if record.consecutive_failures >= self._config.failure_threshold:
                self._open(record, now_monotonic)

    def record_neutral(
        self,
        tool_ref: ToolRef,
        permit: CircuitPermit,
        *,
        now_monotonic: float,
    ) -> None:
        _require_finite_non_negative(now_monotonic, field="now_monotonic")
        if not permit.half_open_probe:
            return
        with self._lock:
            record = self._records.setdefault(tool_ref, _CircuitRecord())
            if permit.generation != record.generation:
                return
            record.half_open_in_flight = max(0, record.half_open_in_flight - 1)

    @staticmethod
    def _open(record: _CircuitRecord, now_monotonic: float) -> None:
        record.state = CircuitState.OPEN
        record.opened_monotonic = now_monotonic
        record.half_open_in_flight = 0
        record.generation += 1


@final
class NoOpCircuitBreaker(CircuitBreaker):
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


@dataclass(frozen=True, slots=True)
class ReliabilityControls:
    """Injected reliability adapters and deterministic timing/randomness hooks."""

    rate_limiter: RateLimiter = field(default_factory=UnlimitedRateLimiter)
    retry_budget: RetryBudget = field(default_factory=UnlimitedRetryBudget)
    circuit_breaker: CircuitBreaker = field(default_factory=NoOpCircuitBreaker)
    random_unit: Callable[[], float] = random.random
    sleep: AsyncSleep = asyncio.sleep

    def __post_init__(self) -> None:
        if not isinstance(self.rate_limiter, RateLimiter):
            raise TypeError("rate_limiter must implement RateLimiter")
        if not isinstance(self.retry_budget, RetryBudget):
            raise TypeError("retry_budget must implement RetryBudget")
        if not isinstance(self.circuit_breaker, CircuitBreaker):
            raise TypeError("circuit_breaker must implement CircuitBreaker")


def full_jitter_delay_seconds(
    policy: RetryPolicy,
    *,
    retry_number: int,
    random_unit: float,
) -> float:
    """Return capped exponential full jitter for retry number one or greater."""

    if isinstance(retry_number, bool) or not isinstance(retry_number, int) or retry_number < 1:
        raise ValueError("retry_number must be a positive integer")
    if (
        isinstance(random_unit, bool)
        or not isinstance(random_unit, int | float)
        or not math.isfinite(random_unit)
        or not 0 <= random_unit <= 1
    ):
        raise ValueError("random_unit must be finite and between zero and one")
    maximum_ms = min(
        policy.maximum_delay_ms,
        policy.initial_delay_ms * (1 << (retry_number - 1)),
    )
    return maximum_ms * float(random_unit) / 1_000


def is_retryable_failure(code: ExecutionErrorCode) -> bool:
    return code in {
        ExecutionErrorCode.UPSTREAM_RATE_LIMITED,
        ExecutionErrorCode.UPSTREAM_SERVER_ERROR,
        ExecutionErrorCode.UPSTREAM_TIMEOUT,
        ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
    }


def is_circuit_failure(code: ExecutionErrorCode) -> bool:
    return code in {
        ExecutionErrorCode.UPSTREAM_SERVER_ERROR,
        ExecutionErrorCode.UPSTREAM_TIMEOUT,
        ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
    }


def retry_after_ms(seconds: float | None) -> int | None:
    if seconds is None:
        return None
    return min(300_000, math.ceil(seconds * 1_000))


def _refill(bucket: _Bucket, config: TokenBucketConfig, now_monotonic: float) -> None:
    elapsed = max(0.0, now_monotonic - bucket.updated_monotonic)
    bucket.tokens = min(
        float(config.capacity),
        bucket.tokens + elapsed * config.refill_per_second,
    )
    bucket.updated_monotonic = max(bucket.updated_monotonic, now_monotonic)


def _require_finite_non_negative(value: object, *, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be finite and non-negative")


def _require_finite_positive(value: object, *, field: str) -> None:
    _require_finite_non_negative(value, field=field)
    if value == 0:
        raise ValueError(f"{field} must be positive")
