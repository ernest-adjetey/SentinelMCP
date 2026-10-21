"""Immutable, payload-free audit records."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sentinelmcp.domain import OperationClass, ToolRef
from sentinelmcp.execution import ExecutionErrorCode, ExecutionId, RequestId

AUDIT_SCHEMA_VERSION = 1
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuditEventKind(StrEnum):
    EXECUTION_COMPLETED = "execution_completed"
    MUTATION_PREVIEWED = "mutation_previewed"
    MUTATION_DISPATCH_INTENT = "mutation_dispatch_intent"
    MUTATION_EXECUTION_COMPLETED = "mutation_execution_completed"
    REPLAY_ATTEMPTED = "replay_attempted"


class AuditOutcome(StrEnum):
    ATTEMPTED = "attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AuditEventId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("audit event ID must be a UUID")

    @classmethod
    def generate(cls) -> AuditEventId:
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One versioned audit fact containing no request or response payload."""

    event_id: AuditEventId
    recorded_at: datetime
    kind: AuditEventKind
    outcome: AuditOutcome
    principal_issuer: str
    principal_subject: str
    tool_ref: ToolRef
    request_id: RequestId
    execution_id: ExecutionId
    operation_class: OperationClass | None
    definition_fingerprint: str | None
    error_code: ExecutionErrorCode | None = None
    duration_ms: float | None = None
    attempt_count: int | None = None
    schema_version: int = AUDIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, AuditEventId):
            raise TypeError("event_id must be an AuditEventId")
        if (
            not isinstance(self.recorded_at, datetime)
            or self.recorded_at.tzinfo is None
            or self.recorded_at.utcoffset() is None
        ):
            raise ValueError("recorded_at must be timezone-aware")
        if not isinstance(self.kind, AuditEventKind):
            raise TypeError("kind must be an AuditEventKind")
        if not isinstance(self.outcome, AuditOutcome):
            raise TypeError("outcome must be an AuditOutcome")
        _require_bounded_string(self.principal_issuer, field="principal_issuer", maximum=2048)
        _require_bounded_string(self.principal_subject, field="principal_subject", maximum=255)
        if not isinstance(self.tool_ref, ToolRef):
            raise TypeError("tool_ref must be a ToolRef")
        if not isinstance(self.request_id, RequestId):
            raise TypeError("request_id must be a RequestId")
        if not isinstance(self.execution_id, ExecutionId):
            raise TypeError("execution_id must be an ExecutionId")
        if self.operation_class is not None and not isinstance(
            self.operation_class, OperationClass
        ):
            raise TypeError("operation_class must be an OperationClass or None")
        if self.definition_fingerprint is not None and (
            not isinstance(self.definition_fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.definition_fingerprint) is None
        ):
            raise ValueError("definition_fingerprint must be a lowercase SHA-256 digest or None")
        if self.error_code is not None and not isinstance(self.error_code, ExecutionErrorCode):
            raise TypeError("error_code must be an ExecutionErrorCode or None")
        if self.duration_ms is not None and (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int | float)
            or not math.isfinite(self.duration_ms)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be finite and non-negative")
        if self.attempt_count is not None and (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or not 0 <= self.attempt_count <= 1000
        ):
            raise ValueError("attempt_count must be from 0 through 1,000 or None")
        if isinstance(self.schema_version, bool) or self.schema_version != AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported audit schema version")
        if self.outcome is AuditOutcome.ATTEMPTED:
            if (
                self.error_code is not None
                or self.duration_ms is not None
                or self.attempt_count is not None
            ):
                raise ValueError("attempt events cannot contain a terminal result")
        elif self.duration_ms is None or self.attempt_count is None:
            raise ValueError("terminal audit events require duration_ms and attempt_count")
        if self.outcome is AuditOutcome.SUCCEEDED and self.error_code is not None:
            raise ValueError("successful audit events cannot contain an error code")
        if self.outcome is AuditOutcome.FAILED and self.error_code is None:
            raise ValueError("failed audit events require an error code")
        intent_kinds = {
            AuditEventKind.MUTATION_DISPATCH_INTENT,
            AuditEventKind.REPLAY_ATTEMPTED,
        }
        terminal_kinds = {
            AuditEventKind.EXECUTION_COMPLETED,
            AuditEventKind.MUTATION_EXECUTION_COMPLETED,
        }
        if self.kind in intent_kinds and self.outcome is not AuditOutcome.ATTEMPTED:
            raise ValueError("intent audit events require an attempted outcome")
        if (
            self.kind is AuditEventKind.MUTATION_PREVIEWED
            and self.outcome is not AuditOutcome.SUCCEEDED
        ):
            raise ValueError("preview audit events require a successful outcome")
        if self.kind in terminal_kinds and self.outcome is AuditOutcome.ATTEMPTED:
            raise ValueError("completion audit events require a terminal outcome")


def _require_bounded_string(value: object, *, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty string of at most {maximum} characters")
