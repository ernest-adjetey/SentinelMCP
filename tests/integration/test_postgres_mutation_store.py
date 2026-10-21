"""Real PostgreSQL confirmation and idempotency behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import ExecutionId, RequestId
from sentinelmcp.mutation_guard import (
    ChallengeId,
    ConfirmationRejectedError,
    ConfirmationRejectionReason,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyConflictError,
    IdempotencyKey,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationBinding,
    MutationChallengeRecord,
    PostgresMutationStateStore,
    confirmation_token_digest,
)
from sentinelmcp.mutation_guard.postgres import mutation_challenges, mutation_idempotency

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TOKEN = "confirmation-token-with-at-least-32-bytes"
TOOL_REF = ToolRef.parse("demo.profile.update@1.0.0")
CHALLENGE_ID = UUID("00000000-0000-4000-8000-000000000010")
RECORD_ID = UUID("00000000-0000-4000-8000-000000000020")


def binding(
    *,
    subject: str = "user-1",
    arguments: str = "b" * 64,
    tool_ref: ToolRef = TOOL_REF,
) -> MutationBinding:
    return MutationBinding(
        "https://issuer.test",
        subject,
        tool_ref,
        "a" * 64,
        arguments,
    )


def challenge(
    *,
    challenge_id: UUID = CHALLENGE_ID,
    token: str = TOKEN,
    mutation_binding: MutationBinding | None = None,
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> MutationChallengeRecord:
    return MutationChallengeRecord(
        ChallengeId(challenge_id),
        confirmation_token_digest(token),
        binding() if mutation_binding is None else mutation_binding,
        expires_at,
    )


def claim_request(
    *,
    record_id: UUID = RECORD_ID,
    token: str = TOKEN,
    mutation_binding: MutationBinding | None = None,
    key: str = "profile-update-1",
    request_digest: str = "c" * 64,
    now: datetime = NOW,
) -> IdempotencyClaimRequest:
    return IdempotencyClaimRequest(
        IdempotencyRecordId(record_id),
        confirmation_token_digest(token),
        binding() if mutation_binding is None else mutation_binding,
        IdempotencyKey(key),
        request_digest,
        RequestId(UUID("00000000-0000-4000-8000-000000000030")),
        ExecutionId(UUID("00000000-0000-4000-8000-000000000040")),
        NOW,
        now,
    )


def test_challenge_persists_digest_not_raw_token(postgres_engine: Engine) -> None:
    store = PostgresMutationStateStore(postgres_engine)

    store.issue_challenge(challenge())

    with postgres_engine.connect() as connection:
        row = connection.execute(select(mutation_challenges)).mappings().one()
    assert bytes(row["token_digest"]) == confirmation_token_digest(TOKEN)
    assert TOKEN.encode() not in bytes(row["token_digest"])
    assert "confirmation_token" not in row


def test_claim_completion_and_terminal_reuse_survive_new_store(
    postgres_engine: Engine,
) -> None:
    store = PostgresMutationStateStore(postgres_engine)
    store.issue_challenge(challenge())
    request = claim_request()

    claimed = store.claim_execution(request)
    store.complete_execution(
        claimed.record_id,
        state=IdempotencyRecordState.SUCCEEDED,
        outcome=b'{"safe":"outcome"}',
        completed_at=NOW + timedelta(seconds=1),
    )
    recovered = PostgresMutationStateStore(postgres_engine).claim_execution(request)

    assert claimed.status is IdempotencyClaimStatus.NEW
    assert recovered.status is IdempotencyClaimStatus.REUSED
    assert recovered.state is IdempotencyRecordState.SUCCEEDED
    assert recovered.outcome == b'{"safe":"outcome"}'


def test_same_scoped_key_with_different_request_digest_conflicts_without_new_effect(
    postgres_engine: Engine,
) -> None:
    store = PostgresMutationStateStore(postgres_engine)
    store.issue_challenge(challenge())
    store.claim_execution(claim_request())

    with pytest.raises(IdempotencyConflictError):
        store.claim_execution(claim_request(request_digest="d" * 64))


@pytest.mark.parametrize(
    ("record", "claim", "reason"),
    [
        (
            challenge(expires_at=NOW - timedelta(seconds=1)),
            claim_request(),
            ConfirmationRejectionReason.EXPIRED,
        ),
        (
            challenge(),
            claim_request(mutation_binding=binding(subject="user-2")),
            ConfirmationRejectionReason.BINDING_MISMATCH,
        ),
        (
            challenge(),
            claim_request(
                mutation_binding=binding(tool_ref=ToolRef.parse("demo.profile.update@2.0.0"))
            ),
            ConfirmationRejectionReason.BINDING_MISMATCH,
        ),
        (
            challenge(),
            claim_request(token="another-confirmation-token-over-32-bytes"),
            ConfirmationRejectionReason.NOT_FOUND,
        ),
    ],
)
def test_invalid_challenge_is_rejected_without_leaving_idempotency_record(
    postgres_engine: Engine,
    record: MutationChallengeRecord,
    claim: IdempotencyClaimRequest,
    reason: ConfirmationRejectionReason,
) -> None:
    store = PostgresMutationStateStore(postgres_engine)
    store.issue_challenge(record)

    with pytest.raises(ConfirmationRejectedError) as captured:
        store.claim_execution(claim)

    assert captured.value.reason is reason
    with postgres_engine.connect() as connection:
        count = connection.execute(
            select(func.count()).select_from(mutation_idempotency)
        ).scalar_one()
    assert count == 0
