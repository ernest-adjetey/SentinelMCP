"""Canonical binding, mutation value, and durable outcome tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest

from sentinelmcp.domain import ToolRef, ValidationIssue
from sentinelmcp.domain.json_values import freeze_json, freeze_json_mapping
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionSuccess,
    RequestId,
)
from sentinelmcp.mutation_guard import (
    ChallengeId,
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyKey,
    IdempotencyRecordId,
    IdempotencyRecordState,
    InvalidMutationRequestError,
    MutationBinding,
    MutationChallengeRecord,
    MutationPreview,
    MutationStateIntegrityError,
    arguments_digest,
    canonicalize_arguments,
    confirmation_token_digest,
    deserialize_execution_result,
    semantic_request_digest,
    serialize_execution_result,
)
from sentinelmcp.mutation_guard.serialization import MAX_OUTCOME_BYTES
from sentinelmcp.policy import Principal

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TOOL_REF = ToolRef.parse("demo.mutation.update@1.0.0")
FINGERPRINT = "a" * 64
PRINCIPAL = Principal("user-1", "https://issuer.test")


def context() -> ExecutionContext:
    return ExecutionContext(
        request_id=RequestId(UUID("00000000-0000-4000-8000-000000000001")),
        execution_id=ExecutionId(UUID("00000000-0000-4000-8000-000000000002")),
        tool_ref=TOOL_REF,
        definition_fingerprint=FINGERPRINT,
        started_at=NOW,
        started_monotonic=10.0,
    )


def test_canonical_arguments_and_semantic_digest_are_stable_and_bound() -> None:
    first = freeze_json_mapping({"z": 2, "a": {"nested": True}})
    reordered = freeze_json_mapping({"a": {"nested": True}, "z": 2})

    assert canonicalize_arguments(first) == canonicalize_arguments(reordered)
    assert arguments_digest(first) == arguments_digest(reordered)
    first_digest = semantic_request_digest(
        principal=PRINCIPAL,
        tool_ref=TOOL_REF,
        definition_fingerprint=FINGERPRINT,
        arguments=first,
    )
    changed_digest = semantic_request_digest(
        principal=Principal("user-2", PRINCIPAL.issuer),
        tool_ref=TOOL_REF,
        definition_fingerprint=FINGERPRINT,
        arguments=first,
    )

    assert first_digest != changed_digest
    assert len(confirmation_token_digest("x" * 43)) == 32


def test_non_i_json_arguments_and_invalid_tokens_are_rejected() -> None:
    with pytest.raises(InvalidMutationRequestError, match="RFC 8785"):
        canonicalize_arguments(freeze_json_mapping({"value": 2**1000}))
    for token in ("short", "x" * 257, "x" * 42 + "\u00e9"):
        with pytest.raises(InvalidMutationRequestError, match="token"):
            confirmation_token_digest(token)


@pytest.mark.parametrize(
    "value",
    ["", " leading", "trailing ", "bad/key", "x" * 256],
)
def test_idempotency_key_rejects_ambiguous_or_unbounded_values(value: str) -> None:
    with pytest.raises(ValueError, match="idempotency key"):
        IdempotencyKey(value)


def test_mutation_records_validate_digest_and_state_invariants() -> None:
    binding = MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, TOOL_REF, "a" * 64, "b" * 64)
    MutationChallengeRecord(ChallengeId.generate(), b"x" * 32, binding, NOW + timedelta(minutes=5))
    IdempotencyClaim(
        IdempotencyClaimStatus.NEW,
        IdempotencyRecordId.generate(),
        IdempotencyRecordState.PENDING,
    )
    IdempotencyClaim(
        IdempotencyClaimStatus.REUSED,
        IdempotencyRecordId.generate(),
        IdempotencyRecordState.SUCCEEDED,
        b"outcome",
    )

    with pytest.raises(ValueError, match="definition fingerprint"):
        MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, TOOL_REF, "bad", "b" * 64)
    with pytest.raises(ValueError, match="terminal"):
        IdempotencyClaim(
            IdempotencyClaimStatus.REUSED,
            IdempotencyRecordId.generate(),
            IdempotencyRecordState.PENDING,
        )


def test_success_and_failure_outcomes_round_trip_exact_context() -> None:
    success = ExecutionSuccess(context(), freeze_json({"updated": True}), 12.5)
    failure = ExecutionFailure(
        context(),
        ExecutionError(
            ExecutionErrorCode.OUTPUT_VALIDATION_FAILED,
            "The tool output does not satisfy the registered output schema.",
            issues=(ValidationIssue(("value",), ("type",), "not a string", "type"),),
        ),
        15.0,
    )

    restored_success = deserialize_execution_result(serialize_execution_result(success))
    restored_failure = deserialize_execution_result(serialize_execution_result(failure))

    assert restored_success == success
    assert restored_failure == failure


@pytest.mark.parametrize("encoded", [b"not-json", b"[]", b'{"kind":"success"}'])
def test_corrupt_outcomes_raise_integrity_error(encoded: bytes) -> None:
    with pytest.raises(MutationStateIntegrityError):
        deserialize_execution_result(encoded)


def test_mutation_value_objects_reject_invalid_types_digests_and_times() -> None:
    binding = MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, TOOL_REF, "a" * 64, "b" * 64)

    with pytest.raises(TypeError, match="challenge ID"):
        ChallengeId(cast(Any, "not-a-uuid"))
    with pytest.raises(TypeError, match="record ID"):
        IdempotencyRecordId(cast(Any, "not-a-uuid"))
    with pytest.raises(ValueError, match="idempotency key"):
        IdempotencyKey(cast(Any, 7))
    with pytest.raises(ValueError, match="principal"):
        MutationBinding("", PRINCIPAL.subject, TOOL_REF, "a" * 64, "b" * 64)
    with pytest.raises(TypeError, match="tool_ref"):
        MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, cast(Any, "bad"), "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="arguments digest"):
        MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, TOOL_REF, "a" * 64, "bad")
    with pytest.raises(ValueError, match="token digest"):
        MutationChallengeRecord(ChallengeId.generate(), b"short", binding, NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        MutationChallengeRecord(
            ChallengeId.generate(),
            b"x" * 32,
            binding,
            datetime(2026, 1, 2),
        )


def test_preview_and_claim_request_validate_public_invariants() -> None:
    arguments = freeze_json_mapping({"name": "Ada"})
    binding = MutationBinding(PRINCIPAL.issuer, PRINCIPAL.subject, TOOL_REF, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="token"):
        MutationPreview(
            challenge_id=ChallengeId.generate(),
            request_id=context().request_id,
            confirmation_token="",
            tool_ref=TOOL_REF,
            definition_fingerprint=FINGERPRINT,
            arguments=arguments,
            warnings=("warning",),
            expires_at=NOW,
        )
    with pytest.raises(ValueError, match="warning"):
        MutationPreview(
            challenge_id=ChallengeId.generate(),
            request_id=context().request_id,
            confirmation_token="t" * 43,
            tool_ref=TOOL_REF,
            definition_fingerprint=FINGERPRINT,
            arguments=arguments,
            warnings=(),
            expires_at=NOW,
        )

    with pytest.raises(ValueError, match="token digest"):
        IdempotencyClaimRequest(
            record_id=IdempotencyRecordId.generate(),
            confirmation_token_digest=b"short",
            binding=binding,
            idempotency_key=IdempotencyKey("request-1"),
            request_digest="c" * 64,
            request_id=context().request_id,
            execution_id=context().execution_id,
            started_at=NOW,
            now=NOW,
        )
    with pytest.raises(ValueError, match="request digest"):
        IdempotencyClaimRequest(
            record_id=IdempotencyRecordId.generate(),
            confirmation_token_digest=b"x" * 32,
            binding=binding,
            idempotency_key=IdempotencyKey("request-1"),
            request_digest="bad",
            request_id=context().request_id,
            execution_id=context().execution_id,
            started_at=NOW,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("status", "state", "outcome", "message"),
    [
        (
            IdempotencyClaimStatus.NEW,
            IdempotencyRecordState.SUCCEEDED,
            b"outcome",
            "new idempotency",
        ),
        (
            IdempotencyClaimStatus.IN_PROGRESS,
            IdempotencyRecordState.SUCCEEDED,
            b"outcome",
            "in-progress",
        ),
        (
            IdempotencyClaimStatus.REUSED,
            IdempotencyRecordState.SUCCEEDED,
            None,
            "only terminal",
        ),
        (
            IdempotencyClaimStatus.NEW,
            IdempotencyRecordState.PENDING,
            b"outcome",
            "only terminal",
        ),
    ],
)
def test_idempotency_claim_rejects_invalid_status_state_combinations(
    status: IdempotencyClaimStatus,
    state: IdempotencyRecordState,
    outcome: bytes | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        IdempotencyClaim(status, IdempotencyRecordId.generate(), state, outcome)


def _valid_outcome_document(*, kind: str = "success") -> dict[str, object]:
    document: dict[str, object] = {
        "context": {
            "definition_fingerprint": FINGERPRINT,
            "execution_id": str(context().execution_id),
            "request_id": str(context().request_id),
            "started_at": NOW.isoformat(),
            "started_monotonic": 1.0,
            "tool_ref": str(TOOL_REF),
        },
        "duration_ms": 2.0,
        "kind": kind,
    }
    if kind == "failure":
        document["error"] = {
            "code": ExecutionErrorCode.UPSTREAM_UNAVAILABLE.value,
            "issues": [],
            "message": "The upstream tool is unavailable.",
            "upstream_status": 503,
        }
    else:
        document["result"] = {"updated": True}
    return document


def _encoded(document: dict[str, object]) -> bytes:
    return json.dumps(document, allow_nan=True, separators=(",", ":")).encode()


def test_outcome_serialization_and_deserialization_enforce_bounds_and_strict_json() -> None:
    oversized = ExecutionSuccess(context(), "x" * MAX_OUTCOME_BYTES, 1.0)
    with pytest.raises(ValueError, match="exceeds"):
        serialize_execution_result(oversized)
    with pytest.raises(MutationStateIntegrityError, match="size limit"):
        deserialize_execution_result(b"x" * (MAX_OUTCOME_BYTES + 1))

    nonfinite = _valid_outcome_document()
    nonfinite["result"] = float("nan")
    with pytest.raises(MutationStateIntegrityError, match="invalid"):
        deserialize_execution_result(_encoded(nonfinite))


def test_malformed_durable_outcome_fields_fail_closed() -> None:
    documents: list[dict[str, object]] = []

    bad_context_fields = _valid_outcome_document()
    bad_context_fields["context"] = {"unexpected": True}
    documents.append(bad_context_fields)

    bad_fingerprint = _valid_outcome_document()
    cast(dict[str, object], bad_fingerprint["context"])["definition_fingerprint"] = None
    documents.append(bad_fingerprint)

    bad_duration = _valid_outcome_document()
    bad_duration["duration_ms"] = True
    documents.append(bad_duration)

    bad_kind = _valid_outcome_document()
    bad_kind["kind"] = "unknown"
    documents.append(bad_kind)

    bad_error_fields = _valid_outcome_document(kind="failure")
    bad_error_fields["error"] = {"code": "upstream_unavailable"}
    documents.append(bad_error_fields)

    bad_upstream_status = _valid_outcome_document(kind="failure")
    cast(dict[str, object], bad_upstream_status["error"])["upstream_status"] = True
    documents.append(bad_upstream_status)

    bad_issues = _valid_outcome_document(kind="failure")
    cast(dict[str, object], bad_issues["error"])["issues"] = "not-an-array"
    documents.append(bad_issues)

    bad_issue_fields = _valid_outcome_document(kind="failure")
    cast(dict[str, object], bad_issue_fields["error"])["issues"] = [{}]
    documents.append(bad_issue_fields)

    bad_validator = _valid_outcome_document(kind="failure")
    cast(dict[str, object], bad_validator["error"])["issues"] = [
        {
            "instance_path": [],
            "message": "invalid",
            "schema_path": [],
            "validator": 7,
        }
    ]
    documents.append(bad_validator)

    bad_path = _valid_outcome_document(kind="failure")
    cast(dict[str, object], bad_path["error"])["issues"] = [
        {
            "instance_path": [True],
            "message": "invalid",
            "schema_path": [],
            "validator": "type",
        }
    ]
    documents.append(bad_path)

    bad_string = _valid_outcome_document()
    cast(dict[str, object], bad_string["context"])["request_id"] = 7
    documents.append(bad_string)

    for document in documents:
        with pytest.raises(MutationStateIntegrityError, match="invalid"):
            deserialize_execution_result(_encoded(document))
