"""Storage-neutral durable mutation-state protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from sentinelmcp.mutation_guard.models import (
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationChallengeRecord,
)


@runtime_checkable
class MutationStateStore(Protocol):
    """Persist challenges and arbitrate idempotent mutation execution."""

    def issue_challenge(self, record: MutationChallengeRecord) -> None:
        """Persist one challenge whose raw token exists only at the caller boundary."""

    def claim_execution(self, request: IdempotencyClaimRequest) -> IdempotencyClaim:
        """Atomically consume a challenge and claim or recover an idempotency record."""

    def complete_execution(
        self,
        record_id: IdempotencyRecordId,
        *,
        state: IdempotencyRecordState,
        outcome: bytes,
        completed_at: datetime,
    ) -> None:
        """Persist one authoritative terminal result for a pending record."""
