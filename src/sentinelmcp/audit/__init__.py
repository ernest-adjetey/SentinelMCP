"""Versioned audit event contracts and append-only persistence."""

from sentinelmcp.audit.errors import AuditIntegrityError, AuditUnavailableError
from sentinelmcp.audit.memory import InMemoryAuditStore
from sentinelmcp.audit.models import (
    AUDIT_SCHEMA_VERSION,
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditOutcome,
)
from sentinelmcp.audit.postgres import PostgresAuditStore, audit_events, clear_audit_events
from sentinelmcp.audit.protocol import AuditStore
from sentinelmcp.audit.recorder import AuditRecorder, DurableAuditRecorder, NoOpAuditRecorder
from sentinelmcp.audit.serialization import audit_event_from_document, audit_event_to_document

__all__ = [
    "AUDIT_SCHEMA_VERSION",
    "AuditEvent",
    "AuditEventId",
    "AuditEventKind",
    "AuditIntegrityError",
    "AuditOutcome",
    "AuditRecorder",
    "AuditStore",
    "AuditUnavailableError",
    "DurableAuditRecorder",
    "InMemoryAuditStore",
    "NoOpAuditRecorder",
    "PostgresAuditStore",
    "audit_event_from_document",
    "audit_event_to_document",
    "audit_events",
    "clear_audit_events",
]
