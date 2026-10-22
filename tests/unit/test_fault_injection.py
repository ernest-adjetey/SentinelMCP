"""Validation and state-transition tests for deterministic fault injectors."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import httpx
import pytest

from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import ExecutionId, RequestId
from sentinelmcp.fault_injection import (
    DeterministicHTTPTransport,
    DeterministicMutationStateStore,
    HTTPFaultKind,
    HTTPFaultStep,
)
from sentinelmcp.mutation_guard import (
    ChallengeId,
    ConfirmationRejectedError,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyConflictError,
    IdempotencyKey,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationBinding,
    MutationChallengeRecord,
    MutationStateIntegrityError,
    MutationStateUnavailableError,
    confirmation_token_digest,
)

NOW = datetime(2026, 10, 21, tzinfo=UTC)
TOKEN = "t" * 43
RECORD_UUID = UUID("00000000-0000-4000-8000-000000000002")
BINDING = MutationBinding(
    "urn:test",
    "actor",
    ToolRef.parse("evaluation.mutation.update@1.0.0"),
    "a" * 64,
    "b" * 64,
)


def _challenge(
    *,
    binding: MutationBinding = BINDING,
    expires_at: datetime = NOW + timedelta(minutes=1),
) -> MutationChallengeRecord:
    return MutationChallengeRecord(
        ChallengeId(UUID("00000000-0000-4000-8000-000000000001")),
        confirmation_token_digest(TOKEN),
        binding,
        expires_at,
    )


def _claim_request(
    *,
    record_id: UUID = RECORD_UUID,
    token: str = TOKEN,
    binding: MutationBinding = BINDING,
    key: str = "evaluation-key",
    request_digest: str = "c" * 64,
) -> IdempotencyClaimRequest:
    return IdempotencyClaimRequest(
        IdempotencyRecordId(record_id),
        confirmation_token_digest(token),
        binding,
        IdempotencyKey(key),
        request_digest,
        RequestId(UUID("00000000-0000-4000-8000-000000000003")),
        ExecutionId(UUID("00000000-0000-4000-8000-000000000004")),
        NOW,
        NOW,
    )


def test_http_fault_step_and_plan_validation() -> None:
    with pytest.raises(ValueError, match="at least one"):
        DeterministicHTTPTransport(())
    with pytest.raises(ValueError, match="requires an HTTP status"):
        HTTPFaultStep(HTTPFaultKind.STATUS)
    with pytest.raises(ValueError, match="only valid"):
        HTTPFaultStep(HTTPFaultKind.SUCCESS, status=200)
    with pytest.raises(ValueError, match="delay_seconds"):
        HTTPFaultStep(HTTPFaultKind.SUCCESS, delay_seconds=2)
    with pytest.raises(ValueError, match="response_bytes"):
        HTTPFaultStep(HTTPFaultKind.SUCCESS, response_bytes=-1)
    with pytest.raises(TypeError, match="HTTPFaultKind"):
        HTTPFaultStep(cast(HTTPFaultKind, "success"))


def test_http_transport_repeats_last_step_and_tracks_bounded_metadata() -> None:
    async def run() -> tuple[int, int, list[str], list[str]]:
        transport = DeterministicHTTPTransport((HTTPFaultStep(HTTPFaultKind.SUCCESS),))
        async with httpx.AsyncClient(transport=transport) as client:
            first = await client.post("https://faults.example.test/one")
            await client.post("https://faults.example.test/two")
        return (
            first.status_code,
            transport.request_count,
            transport.request_methods,
            transport.request_urls,
        )

    status, count, methods, urls = asyncio.run(run())
    assert status == 200
    assert count == 2
    assert methods == ["POST", "POST"]
    assert urls == [
        "https://faults.example.test/one",
        "https://faults.example.test/two",
    ]


def test_mutation_store_reports_new_in_progress_terminal_reuse_and_conflict() -> None:
    store = DeterministicMutationStateStore()
    store.issue_challenge(_challenge())
    request = _claim_request()

    first = store.claim_execution(request)
    in_progress = store.claim_execution(request)
    store.complete_execution(
        first.record_id,
        state=IdempotencyRecordState.SUCCEEDED,
        outcome=b"outcome",
        completed_at=NOW,
    )
    reused = store.claim_execution(request)

    assert first.status is IdempotencyClaimStatus.NEW
    assert in_progress.status is IdempotencyClaimStatus.IN_PROGRESS
    assert reused.status is IdempotencyClaimStatus.REUSED
    assert reused.outcome == b"outcome"
    with pytest.raises(IdempotencyConflictError):
        store.claim_execution(replace(request, request_digest="d" * 64))


def test_mutation_store_rejects_missing_expired_and_mismatched_challenges() -> None:
    missing = DeterministicMutationStateStore()
    with pytest.raises(ConfirmationRejectedError):
        missing.claim_execution(_claim_request())

    expired = DeterministicMutationStateStore()
    expired.issue_challenge(_challenge(expires_at=NOW))
    with pytest.raises(ConfirmationRejectedError):
        expired.claim_execution(_claim_request())

    mismatch = DeterministicMutationStateStore()
    other_binding = replace(BINDING, arguments_digest="d" * 64)
    mismatch.issue_challenge(_challenge(binding=other_binding))
    with pytest.raises(ConfirmationRejectedError):
        mismatch.claim_execution(_claim_request())


def test_mutation_store_injects_completion_failure_and_checks_integrity() -> None:
    failing = DeterministicMutationStateStore(fail_completion=True)
    failing.issue_challenge(_challenge())
    claim = failing.claim_execution(_claim_request())
    with pytest.raises(MutationStateUnavailableError):
        failing.complete_execution(
            claim.record_id,
            state=IdempotencyRecordState.SUCCEEDED,
            outcome=b"outcome",
            completed_at=NOW,
        )

    store = DeterministicMutationStateStore()
    with pytest.raises(MutationStateIntegrityError):
        store.complete_execution(
            IdempotencyRecordId.generate(),
            state=IdempotencyRecordState.SUCCEEDED,
            outcome=b"outcome",
            completed_at=NOW,
        )
