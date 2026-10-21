"""Append-only PostgreSQL audit persistence."""

from __future__ import annotations

from datetime import datetime
from typing import final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Uuid,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Select

from sentinelmcp.audit.errors import AuditIntegrityError, AuditUnavailableError
from sentinelmcp.audit.models import (
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditOutcome,
)
from sentinelmcp.audit.protocol import AuditStore
from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.domain.errors import SentinelMCPError
from sentinelmcp.execution import ExecutionErrorCode, ExecutionId, RequestId

audit_metadata = MetaData()
audit_events = Table(
    "audit_events",
    audit_metadata,
    Column("event_id", Uuid(as_uuid=True), primary_key=True),
    Column("schema_version", SmallInteger(), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("principal_issuer", String(2048), nullable=False),
    Column("principal_subject", String(255), nullable=False),
    Column("namespace", String(191), nullable=False),
    Column("name", String(63), nullable=False),
    Column("version", String(128), nullable=False),
    Column("definition_fingerprint", String(64), nullable=True),
    Column("request_id", Uuid(as_uuid=True), nullable=False),
    Column("execution_id", Uuid(as_uuid=True), nullable=False),
    Column("operation_class", String(16), nullable=True),
    Column("error_code", String(64), nullable=True),
    Column("duration_ms", Float(), nullable=True),
    Column("attempt_count", Integer(), nullable=True),
    CheckConstraint("schema_version = 1", name="ck_audit_events_schema_version"),
    CheckConstraint(
        "kind IN ('execution_completed', 'mutation_previewed', "
        "'mutation_dispatch_intent', 'mutation_execution_completed', 'replay_attempted')",
        name="ck_audit_events_kind",
    ),
    CheckConstraint(
        "outcome IN ('attempted', 'succeeded', 'failed')",
        name="ck_audit_events_outcome",
    ),
    CheckConstraint(
        "operation_class IS NULL OR operation_class IN ('read', 'mutation')",
        name="ck_audit_events_operation_class",
    ),
    CheckConstraint(
        "(kind IN ('mutation_dispatch_intent', 'replay_attempted') "
        "AND outcome = 'attempted') OR "
        "(kind = 'mutation_previewed' AND outcome = 'succeeded') OR "
        "(kind IN ('execution_completed', 'mutation_execution_completed') "
        "AND outcome IN ('succeeded', 'failed'))",
        name="ck_audit_events_kind_outcome",
    ),
    CheckConstraint(
        "(outcome = 'attempted' AND error_code IS NULL AND duration_ms IS NULL "
        "AND attempt_count IS NULL) OR "
        "(outcome = 'succeeded' AND error_code IS NULL AND duration_ms IS NOT NULL "
        "AND attempt_count IS NOT NULL) OR "
        "(outcome = 'failed' AND error_code IS NOT NULL AND duration_ms IS NOT NULL "
        "AND attempt_count IS NOT NULL)",
        name="ck_audit_events_terminal_shape",
    ),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_audit_events_duration"),
    CheckConstraint(
        "attempt_count IS NULL OR (attempt_count >= 0 AND attempt_count <= 1000)",
        name="ck_audit_events_attempt_count",
    ),
)
Index("ix_audit_events_request", audit_events.c.request_id, audit_events.c.recorded_at)
Index("ix_audit_events_execution", audit_events.c.execution_id, audit_events.c.recorded_at)
Index("ix_audit_events_recorded_at", audit_events.c.recorded_at)


@final
class PostgresAuditStore(AuditStore):
    """Durable immutable audit rows with no ordinary update or delete operation."""

    __slots__ = ("_engine",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent):
            raise TypeError("event must be an AuditEvent")
        statement = (
            insert(audit_events)
            .values(**_event_values(event))
            .on_conflict_do_nothing(index_elements=[audit_events.c.event_id])
            .returning(audit_events.c.event_id)
        )
        try:
            with self._engine.begin() as connection:
                inserted: object = connection.execute(statement).scalar_one_or_none()
                if inserted is not None:
                    return
                row = (
                    connection.execute(
                        select(audit_events).where(audit_events.c.event_id == event.event_id.value)
                    )
                    .mappings()
                    .one()
                )
                if _event_from_row(row) != event:
                    raise AuditIntegrityError(reason="audit event ID has conflicting content")
        except AuditIntegrityError:
            raise
        except SQLAlchemyError:
            raise AuditUnavailableError(operation="audit append") from None

    def list_for_request(self, request_id: RequestId) -> tuple[AuditEvent, ...]:
        if not isinstance(request_id, RequestId):
            raise TypeError("request_id must be a RequestId")
        return self._list(
            select(audit_events)
            .where(audit_events.c.request_id == request_id.value)
            .order_by(audit_events.c.recorded_at, audit_events.c.event_id),
            operation="request audit lookup",
        )

    def list_for_execution(self, execution_id: ExecutionId) -> tuple[AuditEvent, ...]:
        if not isinstance(execution_id, ExecutionId):
            raise TypeError("execution_id must be an ExecutionId")
        return self._list(
            select(audit_events)
            .where(audit_events.c.execution_id == execution_id.value)
            .order_by(audit_events.c.recorded_at, audit_events.c.event_id),
            operation="execution audit lookup",
        )

    def _list(
        self,
        statement: Select[tuple[object, ...]],
        *,
        operation: str,
    ) -> tuple[AuditEvent, ...]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            return tuple(_event_from_row(row) for row in rows)
        except AuditIntegrityError:
            raise
        except SQLAlchemyError:
            raise AuditUnavailableError(operation=operation) from None


def clear_audit_events(engine: Engine) -> None:
    """Delete audit rows for isolated tests only; never exposed by AuditStore."""

    with engine.begin() as connection:
        connection.execute(delete(audit_events))


def _event_values(event: AuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id.value,
        "schema_version": event.schema_version,
        "recorded_at": event.recorded_at,
        "kind": event.kind.value,
        "outcome": event.outcome.value,
        "principal_issuer": event.principal_issuer,
        "principal_subject": event.principal_subject,
        "namespace": str(event.tool_ref.namespace),
        "name": str(event.tool_ref.name),
        "version": str(event.tool_ref.version),
        "definition_fingerprint": event.definition_fingerprint,
        "request_id": event.request_id.value,
        "execution_id": event.execution_id.value,
        "operation_class": (None if event.operation_class is None else event.operation_class.value),
        "error_code": None if event.error_code is None else event.error_code.value,
        "duration_ms": event.duration_ms,
        "attempt_count": event.attempt_count,
    }


def _event_from_row(row: RowMapping) -> AuditEvent:
    try:
        operation = _optional_string(row, "operation_class")
        error = _optional_string(row, "error_code")
        return AuditEvent(
            event_id=AuditEventId(_uuid(row, "event_id")),
            schema_version=_integer(row, "schema_version"),
            recorded_at=_datetime(row, "recorded_at"),
            kind=AuditEventKind(_string(row, "kind")),
            outcome=AuditOutcome(_string(row, "outcome")),
            principal_issuer=_string(row, "principal_issuer"),
            principal_subject=_string(row, "principal_subject"),
            tool_ref=ToolRef(
                ToolNamespace(_string(row, "namespace")),
                ToolName(_string(row, "name")),
                ToolVersion(_string(row, "version")),
            ),
            definition_fingerprint=_optional_string(row, "definition_fingerprint"),
            request_id=RequestId(_uuid(row, "request_id")),
            execution_id=ExecutionId(_uuid(row, "execution_id")),
            operation_class=None if operation is None else OperationClass(operation),
            error_code=None if error is None else ExecutionErrorCode(error),
            duration_ms=_optional_float(row, "duration_ms"),
            attempt_count=_optional_integer(row, "attempt_count"),
        )
    except (KeyError, TypeError, ValueError, SentinelMCPError) as error:
        raise AuditIntegrityError(reason="persisted audit row violates schema") from error


def _string(row: RowMapping, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise TypeError(f"{field} is not a string")
    return value


def _optional_string(row: RowMapping, field: str) -> str | None:
    value = row.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} is not a string or null")
    return value


def _uuid(row: RowMapping, field: str) -> UUID:
    value = row.get(field)
    if not isinstance(value, UUID):
        raise TypeError(f"{field} is not a UUID")
    return value


def _datetime(row: RowMapping, field: str) -> datetime:
    value = row.get(field)
    if not isinstance(value, datetime):
        raise TypeError(f"{field} is not a datetime")
    return value


def _integer(row: RowMapping, field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} is not an integer")
    return value


def _optional_integer(row: RowMapping, field: str) -> int | None:
    value = row.get(field)
    return None if value is None else _integer(row, field)


def _optional_float(row: RowMapping, field: str) -> float | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field} is not numeric")
    return float(value)
