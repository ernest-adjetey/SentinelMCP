"""Real PostgreSQL audit durability and failure behavior."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.engine import Engine

from sentinelmcp.audit import (
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditIntegrityError,
    AuditOutcome,
    AuditUnavailableError,
    PostgresAuditStore,
    audit_events,
)
from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.execution import ExecutionId, RequestId
from sentinelmcp.registry import create_postgres_engine

pytestmark = pytest.mark.postgres


def test_postgres_audit_append_lookup_idempotency_and_restart(
    postgres_engine: Engine,
    postgres_database_url: str,
) -> None:
    store = PostgresAuditStore(postgres_engine)
    event = _event()
    store.append(event)
    store.append(event)

    assert store.list_for_request(event.request_id) == (event,)
    assert store.list_for_execution(event.execution_id) == (event,)

    restarted_engine = create_postgres_engine(postgres_database_url)
    try:
        assert PostgresAuditStore(restarted_engine).list_for_request(event.request_id) == (event,)
    finally:
        restarted_engine.dispose()

    with pytest.raises(AuditIntegrityError, match="conflicting"):
        store.append(replace(event, principal_subject="other"))


def test_postgres_audit_detects_corrupted_rows(postgres_engine: Engine) -> None:
    store = PostgresAuditStore(postgres_engine)
    event = _event()
    store.append(event)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(audit_events)
            .where(audit_events.c.event_id == event.event_id.value)
            .values(namespace="INVALID NAMESPACE")
        )

    with pytest.raises(AuditIntegrityError, match="violates schema"):
        store.list_for_request(event.request_id)


def test_postgres_audit_translates_database_unavailability() -> None:
    engine = create_postgres_engine(
        "postgresql+psycopg://sentinelmcp_test@127.0.0.1:1/sentinelmcp_test?connect_timeout=1",
    )
    try:
        store = PostgresAuditStore(engine)
        with pytest.raises(AuditUnavailableError, match="audit append"):
            store.append(_event())
        with pytest.raises(AuditUnavailableError, match="audit lookup"):
            store.list_for_request(_event().request_id)
    finally:
        engine.dispose()


def _event() -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId(UUID("10000000-0000-4000-8000-000000000001")),
        recorded_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        kind=AuditEventKind.EXECUTION_COMPLETED,
        outcome=AuditOutcome.SUCCEEDED,
        principal_issuer="https://issuer.example",
        principal_subject="actor",
        tool_ref=ToolRef(
            ToolNamespace("demo.audit"),
            ToolName("probe"),
            ToolVersion("1.0.0"),
        ),
        definition_fingerprint="a" * 64,
        request_id=RequestId(UUID("10000000-0000-4000-8000-000000000002")),
        execution_id=ExecutionId(UUID("10000000-0000-4000-8000-000000000003")),
        operation_class=OperationClass.READ,
        duration_ms=2.5,
        attempt_count=1,
    )
