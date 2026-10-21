"""Real PostgreSQL audit append races."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.engine import Engine

from sentinelmcp.audit import (
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditOutcome,
    PostgresAuditStore,
)
from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.execution import ExecutionId, RequestId

pytestmark = pytest.mark.postgres


def test_concurrent_audit_appends_preserve_every_distinct_event(
    postgres_engine: Engine,
) -> None:
    store = PostgresAuditStore(postgres_engine)
    base = _event()
    events = tuple(
        replace(
            base,
            event_id=AuditEventId(UUID(int=100 + index)),
            recorded_at=base.recorded_at + timedelta(microseconds=index),
        )
        for index in range(32)
    )

    with ThreadPoolExecutor(max_workers=32) as workers:
        tuple(workers.map(store.append, events))

    assert store.list_for_request(base.request_id) == events


def test_concurrent_identical_append_creates_one_event(postgres_engine: Engine) -> None:
    store = PostgresAuditStore(postgres_engine)
    event = _event()

    with ThreadPoolExecutor(max_workers=32) as workers:
        tuple(workers.map(store.append, (event,) * 32))

    assert store.list_for_request(event.request_id) == (event,)


def _event() -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId(UUID(int=99)),
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
        definition_fingerprint="b" * 64,
        request_id=RequestId(UUID(int=1)),
        execution_id=ExecutionId(UUID(int=2)),
        operation_class=OperationClass.READ,
        duration_ms=1.0,
        attempt_count=1,
    )
