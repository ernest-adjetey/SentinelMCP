"""Deterministic mutation-state implementation used only by the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import final

from sentinelmcp.mutation_guard import (
    ConfirmationRejectedError,
    ConfirmationRejectionReason,
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyConflictError,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationChallengeRecord,
    MutationStateIntegrityError,
    MutationStateUnavailableError,
)


@dataclass(slots=True)
class _EvaluationRecord:
    request_digest: str
    state: IdempotencyRecordState
    outcome: bytes | None = None


@final
class DeterministicMutationStateStore:
    """Small state store for repeatable safety evaluations, never production composition."""

    __slots__ = ("_challenges", "_fail_completion", "_lock", "_records", "_scopes")

    def __init__(self, *, fail_completion: bool = False) -> None:
        self._fail_completion = fail_completion
        self._challenges: list[MutationChallengeRecord] = []
        self._records: dict[IdempotencyRecordId, _EvaluationRecord] = {}
        self._scopes: dict[tuple[str, str, str, str], IdempotencyRecordId] = {}
        self._lock = RLock()

    def issue_challenge(self, record: MutationChallengeRecord) -> None:
        with self._lock:
            self._challenges.append(record)

    def claim_execution(self, request: IdempotencyClaimRequest) -> IdempotencyClaim:
        scope = (
            request.binding.principal_issuer,
            request.binding.principal_subject,
            str(request.binding.tool_ref),
            str(request.idempotency_key),
        )
        with self._lock:
            existing_id = self._scopes.get(scope)
            if existing_id is not None:
                existing = self._records[existing_id]
                if existing.request_digest != request.request_digest:
                    raise IdempotencyConflictError
                if existing.state is IdempotencyRecordState.PENDING:
                    return IdempotencyClaim(
                        IdempotencyClaimStatus.IN_PROGRESS,
                        existing_id,
                        existing.state,
                    )
                return IdempotencyClaim(
                    IdempotencyClaimStatus.REUSED,
                    existing_id,
                    existing.state,
                    existing.outcome,
                )

            challenge = next(
                (
                    candidate
                    for candidate in self._challenges
                    if candidate.token_digest == request.confirmation_token_digest
                ),
                None,
            )
            if challenge is None:
                raise ConfirmationRejectedError(ConfirmationRejectionReason.NOT_FOUND)
            if challenge.expires_at <= request.now:
                raise ConfirmationRejectedError(ConfirmationRejectionReason.EXPIRED)
            if challenge.binding != request.binding:
                raise ConfirmationRejectedError(ConfirmationRejectionReason.BINDING_MISMATCH)
            self._challenges.remove(challenge)
            self._records[request.record_id] = _EvaluationRecord(
                request.request_digest,
                IdempotencyRecordState.PENDING,
            )
            self._scopes[scope] = request.record_id
            return IdempotencyClaim(
                IdempotencyClaimStatus.NEW,
                request.record_id,
                IdempotencyRecordState.PENDING,
            )

    def complete_execution(
        self,
        record_id: IdempotencyRecordId,
        *,
        state: IdempotencyRecordState,
        outcome: bytes,
        completed_at: datetime,
    ) -> None:
        del completed_at
        with self._lock:
            if self._fail_completion:
                raise MutationStateUnavailableError(operation="injected outcome persistence")
            record = self._records.get(record_id)
            if record is None or record.state is not IdempotencyRecordState.PENDING:
                raise MutationStateIntegrityError(reason="evaluation record is not pending")
            record.state = state
            record.outcome = outcome
