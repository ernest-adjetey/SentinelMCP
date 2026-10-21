"""Strict payload-free audit document serialization."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sentinelmcp.audit.errors import AuditIntegrityError
from sentinelmcp.audit.models import AuditEvent, AuditEventId, AuditEventKind, AuditOutcome
from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.execution import ExecutionErrorCode, ExecutionId, RequestId

_FIELDS = {
    "schema_version",
    "event_id",
    "recorded_at",
    "kind",
    "outcome",
    "principal_issuer",
    "principal_subject",
    "tool_ref",
    "definition_fingerprint",
    "request_id",
    "execution_id",
    "operation_class",
    "error_code",
    "duration_ms",
    "attempt_count",
}


def audit_event_to_document(event: AuditEvent) -> dict[str, object]:
    if not isinstance(event, AuditEvent):
        raise TypeError("event must be an AuditEvent")
    return {
        "schema_version": event.schema_version,
        "event_id": str(event.event_id),
        "recorded_at": event.recorded_at.isoformat(),
        "kind": event.kind.value,
        "outcome": event.outcome.value,
        "principal_issuer": event.principal_issuer,
        "principal_subject": event.principal_subject,
        "tool_ref": {
            "namespace": str(event.tool_ref.namespace),
            "name": str(event.tool_ref.name),
            "version": str(event.tool_ref.version),
        },
        "definition_fingerprint": event.definition_fingerprint,
        "request_id": str(event.request_id),
        "execution_id": str(event.execution_id),
        "operation_class": (None if event.operation_class is None else event.operation_class.value),
        "error_code": None if event.error_code is None else event.error_code.value,
        "duration_ms": event.duration_ms,
        "attempt_count": event.attempt_count,
    }


def audit_event_from_document(document: object) -> AuditEvent:
    try:
        if not isinstance(document, dict) or set(document) != _FIELDS:
            raise ValueError("document fields are not the versioned audit schema")
        tool = document["tool_ref"]
        if not isinstance(tool, dict) or set(tool) != {"namespace", "name", "version"}:
            raise ValueError("tool_ref is invalid")
        operation_value = document["operation_class"]
        error_value = document["error_code"]
        return AuditEvent(
            event_id=AuditEventId(UUID(_string(document, "event_id"))),
            recorded_at=datetime.fromisoformat(_string(document, "recorded_at")),
            kind=AuditEventKind(_string(document, "kind")),
            outcome=AuditOutcome(_string(document, "outcome")),
            principal_issuer=_string(document, "principal_issuer"),
            principal_subject=_string(document, "principal_subject"),
            tool_ref=ToolRef(
                ToolNamespace(_mapping_string(tool, "namespace")),
                ToolName(_mapping_string(tool, "name")),
                ToolVersion(_mapping_string(tool, "version")),
            ),
            definition_fingerprint=_optional_string(document, "definition_fingerprint"),
            request_id=RequestId(UUID(_string(document, "request_id"))),
            execution_id=ExecutionId(UUID(_string(document, "execution_id"))),
            operation_class=(
                None if operation_value is None else OperationClass(cast(str, operation_value))
            ),
            error_code=(
                None if error_value is None else ExecutionErrorCode(cast(str, error_value))
            ),
            duration_ms=_optional_number(document, "duration_ms"),
            attempt_count=_optional_integer(document, "attempt_count"),
            schema_version=_integer(document, "schema_version"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AuditIntegrityError(reason="audit document violates schema") from error


def _string(document: dict[object, object], field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} is not a string")
    return value


def _mapping_string(document: dict[object, object], field: str) -> str:
    return _string(document, field)


def _optional_string(document: dict[object, object], field: str) -> str | None:
    value = document[field]
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} is not a string or null")
    return value


def _integer(document: dict[object, object], field: str) -> int:
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} is not an integer")
    return value


def _optional_integer(document: dict[object, object], field: str) -> int | None:
    value = document[field]
    return None if value is None else _integer(document, field)


def _optional_number(document: dict[object, object], field: str) -> float | None:
    value = document[field]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} is not numeric")
    return float(value)
