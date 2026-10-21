"""Audit contract, serialization, and in-memory behavior."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from sentinelmcp.audit import (
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditIntegrityError,
    AuditOutcome,
    AuditStore,
    DurableAuditRecorder,
    InMemoryAuditStore,
    audit_event_from_document,
    audit_event_to_document,
)
from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.domain.json_values import freeze_json
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionSuccess,
    RequestId,
)
from sentinelmcp.policy import Principal

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
FINGERPRINT = "a" * 64
REQUEST_ID = RequestId(UUID("00000000-0000-4000-8000-000000000001"))
EXECUTION_ID = ExecutionId(UUID("00000000-0000-4000-8000-000000000002"))
EVENT_ID = AuditEventId(UUID("00000000-0000-4000-8000-000000000003"))
TOOL_REF = ToolRef(ToolNamespace("demo.audit"), ToolName("probe"), ToolVersion("1.0.0"))


def test_audit_document_round_trip_contains_only_versioned_safe_fields() -> None:
    event = _event()
    document = audit_event_to_document(event)

    assert audit_event_from_document(document) == event
    serialized = repr(document).lower()
    for forbidden in (
        "arguments",
        "authorization",
        "bearer",
        "confirmation_token",
        "idempotency_key",
        "result",
        "secret",
    ):
        assert forbidden not in serialized


def test_audit_document_rejects_extra_or_invalid_fields() -> None:
    document = audit_event_to_document(_event())
    document["arguments"] = {"secret": "not allowed"}
    with pytest.raises(AuditIntegrityError):
        audit_event_from_document(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_ref", "demo.audit.probe@1.0.0"),
        ("tool_ref", {"namespace": "demo.audit", "name": "probe"}),
        ("event_id", 1),
        ("definition_fingerprint", 1),
        ("attempt_count", "one"),
        ("duration_ms", True),
        ("schema_version", True),
        ("operation_class", 1),
    ],
)
def test_audit_document_rejects_corrupted_field_types(field: str, value: object) -> None:
    document = audit_event_to_document(_event())
    document[field] = value

    with pytest.raises(AuditIntegrityError, match="violates schema"):
        audit_event_from_document(document)


def test_audit_document_supports_null_fields_for_attempt_events() -> None:
    event = replace(
        _event(),
        kind=AuditEventKind.MUTATION_DISPATCH_INTENT,
        outcome=AuditOutcome.ATTEMPTED,
        error_code=None,
        duration_ms=None,
        attempt_count=None,
        definition_fingerprint=None,
        operation_class=None,
    )

    assert audit_event_from_document(audit_event_to_document(event)) == event


def test_audit_serializer_rejects_non_events() -> None:
    with pytest.raises(TypeError, match="event must be an AuditEvent"):
        audit_event_to_document(object())  # type: ignore[arg-type]


def test_audit_document_rejects_unsupported_schema_version() -> None:
    document = audit_event_to_document(_event())
    document["schema_version"] = 2
    with pytest.raises(AuditIntegrityError):
        audit_event_from_document(document)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"recorded_at": datetime(2026, 1, 1)}, "timezone-aware"),
        ({"definition_fingerprint": "bad"}, "SHA-256"),
        ({"duration_ms": float("inf")}, "finite"),
        ({"attempt_count": 1001}, "1,000"),
        ({"outcome": AuditOutcome.ATTEMPTED}, "attempt events"),
        (
            {
                "outcome": AuditOutcome.SUCCEEDED,
                "error_code": ExecutionErrorCode.ADAPTER_FAILURE,
            },
            "successful",
        ),
        ({"outcome": AuditOutcome.FAILED, "error_code": None}, "failed"),
        ({"kind": AuditEventKind.MUTATION_DISPATCH_INTENT}, "intent"),
        ({"kind": AuditEventKind.MUTATION_PREVIEWED}, "preview"),
    ],
)
def test_audit_event_rejects_invalid_terminal_shapes(
    changes: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        replace(_event(), **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"event_id": object()}, "event_id"),
        ({"kind": "execution_completed"}, "kind"),
        ({"outcome": "failed"}, "outcome"),
        ({"principal_issuer": ""}, "principal_issuer"),
        ({"principal_subject": "x" * 256}, "principal_subject"),
        ({"tool_ref": object()}, "tool_ref"),
        ({"request_id": object()}, "request_id"),
        ({"execution_id": object()}, "execution_id"),
        ({"operation_class": "read"}, "operation_class"),
        ({"error_code": "upstream_timeout"}, "error_code"),
        ({"duration_ms": True}, "duration_ms"),
        ({"duration_ms": -1}, "duration_ms"),
        ({"attempt_count": True}, "attempt_count"),
        ({"attempt_count": -1}, "attempt_count"),
        ({"schema_version": True}, "schema version"),
        (
            {
                "outcome": AuditOutcome.SUCCEEDED,
                "error_code": None,
                "duration_ms": None,
            },
            "terminal audit events",
        ),
        (
            {
                "kind": AuditEventKind.REPLAY_ATTEMPTED,
                "outcome": AuditOutcome.SUCCEEDED,
                "error_code": None,
            },
            "intent audit events",
        ),
        (
            {
                "kind": AuditEventKind.EXECUTION_COMPLETED,
                "outcome": AuditOutcome.ATTEMPTED,
                "error_code": None,
                "duration_ms": None,
                "attempt_count": None,
            },
            "completion audit events",
        ),
    ],
)
def test_audit_event_rejects_invalid_contract_types(
    changes: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        replace(_event(), **changes)


def test_in_memory_store_is_ordered_idempotent_and_detects_conflicts() -> None:
    store = InMemoryAuditStore()
    later = _event(recorded_at=NOW + timedelta(seconds=1))
    earlier = _event(
        event_id=AuditEventId(UUID("00000000-0000-4000-8000-000000000004")),
    )

    store.append(later)
    store.append(earlier)
    store.append(earlier)

    assert isinstance(store, AuditStore)
    assert store.list_for_request(REQUEST_ID) == (earlier, later)
    assert store.list_for_execution(EXECUTION_ID) == (earlier, later)
    with pytest.raises(AuditIntegrityError, match="conflicting"):
        store.append(replace(earlier, principal_subject="other"))


def test_in_memory_store_rejects_invalid_api_types() -> None:
    store = InMemoryAuditStore()

    with pytest.raises(TypeError, match="event"):
        store.append(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="request_id"):
        store.list_for_request(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="execution_id"):
        store.list_for_execution(object())  # type: ignore[arg-type]


def test_audit_identifiers_and_recorder_validate_construction() -> None:
    generated = AuditEventId.generate()
    assert str(generated) == str(generated.value)

    with pytest.raises(TypeError, match="UUID"):
        AuditEventId("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AuditStore"):
        DurableAuditRecorder(object())  # type: ignore[arg-type]


def test_durable_recorder_derives_safe_success_and_failure_events() -> None:
    store = InMemoryAuditStore()
    recorder = DurableAuditRecorder(
        store,
        event_id_factory=lambda: EVENT_ID,
        wall_clock=lambda: NOW,
    )
    context = _context()
    principal = Principal("actor", "https://issuer.example")

    preview = DurableAuditRecorder(store, wall_clock=lambda: NOW).record_preview(
        principal=principal,
        context=context,
    )
    assert preview.kind is AuditEventKind.MUTATION_PREVIEWED
    assert preview.outcome is AuditOutcome.SUCCEEDED
    assert preview.attempt_count == 0

    intent = recorder.record_intent(
        kind=AuditEventKind.MUTATION_DISPATCH_INTENT,
        principal=principal,
        context=context,
        operation_class=OperationClass.MUTATION,
    )
    assert intent.outcome is AuditOutcome.ATTEMPTED

    failure = ExecutionFailure(
        context=context,
        error=ExecutionError(ExecutionErrorCode.UPSTREAM_TIMEOUT, "safe"),
        duration_ms=12.5,
    )
    failed = DurableAuditRecorder(store, wall_clock=lambda: NOW).record_result(
        kind=AuditEventKind.EXECUTION_COMPLETED,
        principal=principal,
        result=failure,
        operation_class=OperationClass.READ,
        attempt_count=2,
    )
    assert failed.error_code is ExecutionErrorCode.UPSTREAM_TIMEOUT
    assert failed.attempt_count == 2

    success = ExecutionSuccess(
        context=context,
        result=freeze_json({"secret": "not audited"}),
        duration_ms=1.0,
    )
    succeeded = DurableAuditRecorder(store, wall_clock=lambda: NOW).record_result(
        kind=AuditEventKind.EXECUTION_COMPLETED,
        principal=principal,
        result=success,
        operation_class=OperationClass.READ,
        attempt_count=1,
    )
    assert succeeded.outcome is AuditOutcome.SUCCEEDED
    assert "not audited" not in repr(audit_event_to_document(succeeded))


def _context() -> ExecutionContext:
    return ExecutionContext(
        request_id=REQUEST_ID,
        execution_id=EXECUTION_ID,
        tool_ref=TOOL_REF,
        definition_fingerprint=FINGERPRINT,
        started_at=NOW,
        started_monotonic=1.0,
    )


def _event(
    *,
    event_id: AuditEventId = EVENT_ID,
    recorded_at: datetime = NOW,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        recorded_at=recorded_at,
        kind=AuditEventKind.EXECUTION_COMPLETED,
        outcome=AuditOutcome.FAILED,
        principal_issuer="https://issuer.example",
        principal_subject="actor",
        tool_ref=TOOL_REF,
        request_id=REQUEST_ID,
        execution_id=EXECUTION_ID,
        operation_class=OperationClass.READ,
        definition_fingerprint=FINGERPRINT,
        error_code=ExecutionErrorCode.UPSTREAM_TIMEOUT,
        duration_ms=5.0,
        attempt_count=2,
    )
