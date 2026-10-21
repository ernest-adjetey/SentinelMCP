"""Real PostgreSQL races for confirmation and idempotency arbitration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import ExecutionId, RequestId
from sentinelmcp.mutation_guard import (
    ChallengeId,
    ConfirmationRejectedError,
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyKey,
    IdempotencyRecordId,
    MutationBinding,
    MutationChallengeRecord,
    PostgresMutationStateStore,
    confirmation_token_digest,
)
from sentinelmcp.mutation_guard.postgres import mutation_idempotency

pytestmark = pytest.mark.postgres

WORKERS = 32
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
BINDING = MutationBinding(
    "https://issuer.test",
    "race-user",
    ToolRef.parse("demo.profile.update@1.0.0"),
    "a" * 64,
    "b" * 64,
)


def challenge(index: int, token: str) -> MutationChallengeRecord:
    return MutationChallengeRecord(
        ChallengeId(UUID(int=index + 1)),
        confirmation_token_digest(token),
        BINDING,
        NOW + timedelta(minutes=5),
    )


def request(index: int, token: str, key: str) -> IdempotencyClaimRequest:
    return IdempotencyClaimRequest(
        IdempotencyRecordId(UUID(int=10_000 + index)),
        confirmation_token_digest(token),
        BINDING,
        IdempotencyKey(key),
        "c" * 64,
        RequestId(UUID(int=20_000 + index)),
        ExecutionId(UUID(int=30_000 + index)),
        NOW,
        NOW,
    )


def test_only_one_of_32_consumers_can_claim_one_confirmation(postgres_engine: Engine) -> None:
    store = PostgresMutationStateStore(postgres_engine)
    token = "shared-confirmation-token-over-thirty-two-bytes"
    store.issue_challenge(challenge(0, token))
    barrier = Barrier(WORKERS)

    def consume(index: int) -> IdempotencyClaim | ConfirmationRejectedError:
        barrier.wait()
        try:
            return store.claim_execution(request(index, token, f"key-{index}"))
        except ConfirmationRejectedError as error:
            return error

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        results = tuple(executor.map(consume, range(WORKERS)))

    claims = tuple(result for result in results if isinstance(result, IdempotencyClaim))
    rejections = tuple(
        result for result in results if isinstance(result, ConfirmationRejectedError)
    )
    assert len(claims) == 1
    assert claims[0].status is IdempotencyClaimStatus.NEW
    assert len(rejections) == WORKERS - 1
    with postgres_engine.connect() as connection:
        count = connection.execute(
            select(func.count()).select_from(mutation_idempotency)
        ).scalar_one()
    assert count == 1


def test_32_identical_idempotent_claims_admit_one_execution(postgres_engine: Engine) -> None:
    store = PostgresMutationStateStore(postgres_engine)
    tokens = tuple(f"confirmation-token-{index:02d}-with-enough-bytes" for index in range(WORKERS))
    for index, token in enumerate(tokens):
        store.issue_challenge(challenge(index, token))
    barrier = Barrier(WORKERS)

    def claim(index: int) -> IdempotencyClaim:
        barrier.wait()
        return store.claim_execution(request(index, tokens[index], "shared-key"))

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        results = tuple(executor.map(claim, range(WORKERS)))

    assert sum(result.status is IdempotencyClaimStatus.NEW for result in results) == 1
    assert sum(result.status is IdempotencyClaimStatus.IN_PROGRESS for result in results) == 31
    assert len({result.record_id for result in results}) == 1
    with postgres_engine.connect() as connection:
        count = connection.execute(
            select(func.count()).select_from(mutation_idempotency)
        ).scalar_one()
    assert count == 1
