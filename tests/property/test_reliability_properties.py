"""Generated invariants for deadlines and exponential full jitter."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from sentinelmcp.domain import RetryPolicy
from sentinelmcp.execution import Deadline, full_jitter_delay_seconds


@given(
    started=st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    timeout_ms=st.integers(min_value=1, max_value=300_000),
    elapsed_ms=st.integers(min_value=0, max_value=600_000),
)
def test_deadline_remaining_is_bounded_and_never_negative(
    started: float,
    timeout_ms: int,
    elapsed_ms: int,
) -> None:
    deadline = Deadline.from_timeout_ms(started_monotonic=started, timeout_ms=timeout_ms)
    remaining = deadline.remaining_seconds(started + elapsed_ms / 1_000)

    assert 0 <= remaining <= timeout_ms / 1_000 + 1e-9
    assert deadline.expired(started + timeout_ms / 1_000) or remaining > 0


@given(
    initial_ms=st.integers(min_value=0, max_value=60_000),
    maximum_ms=st.integers(min_value=0, max_value=300_000),
    retry_number=st.integers(min_value=1, max_value=20),
    random_unit=st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
)
def test_full_jitter_never_exceeds_exponential_cap(
    initial_ms: int,
    maximum_ms: int,
    retry_number: int,
    random_unit: float,
) -> None:
    if initial_ms > maximum_ms:
        initial_ms = maximum_ms
    policy = RetryPolicy(
        max_attempts=2,
        initial_delay_ms=initial_ms,
        maximum_delay_ms=maximum_ms,
    )
    delay = full_jitter_delay_seconds(
        policy,
        retry_number=retry_number,
        random_unit=random_unit,
    )
    cap = min(maximum_ms, initial_ms * (1 << (retry_number - 1))) / 1_000

    assert 0 <= delay <= cap
