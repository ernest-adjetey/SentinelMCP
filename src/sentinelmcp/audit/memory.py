"""Thread-safe in-memory audit store for tests and local composition."""

from collections.abc import Iterable
from threading import Lock
from typing import final
from uuid import UUID

from sentinelmcp.audit.errors import AuditIntegrityError
from sentinelmcp.audit.models import AuditEvent
from sentinelmcp.audit.protocol import AuditStore
from sentinelmcp.execution import ExecutionId, RequestId


@final
class InMemoryAuditStore(AuditStore):
    __slots__ = ("_events", "_ids", "_lock")

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._ids: dict[UUID, AuditEvent] = {}
        self._lock = Lock()

    def append(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent):
            raise TypeError("event must be an AuditEvent")
        with self._lock:
            existing = self._ids.get(event.event_id.value)
            if existing is not None:
                if existing != event:
                    raise AuditIntegrityError(reason="audit event ID has conflicting content")
                return
            self._ids[event.event_id.value] = event
            self._events.append(event)

    def list_for_request(self, request_id: RequestId) -> tuple[AuditEvent, ...]:
        if not isinstance(request_id, RequestId):
            raise TypeError("request_id must be a RequestId")
        with self._lock:
            return _ordered(event for event in self._events if event.request_id == request_id)

    def list_for_execution(self, execution_id: ExecutionId) -> tuple[AuditEvent, ...]:
        if not isinstance(execution_id, ExecutionId):
            raise TypeError("execution_id must be an ExecutionId")
        with self._lock:
            return _ordered(event for event in self._events if event.execution_id == execution_id)


def _ordered(events: Iterable[AuditEvent]) -> tuple[AuditEvent, ...]:
    return tuple(
        sorted(
            events,
            key=lambda event: (event.recorded_at, str(event.event_id)),
        )
    )
