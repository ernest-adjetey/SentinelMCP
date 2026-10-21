"""Storage-independent audit persistence contract."""

from typing import Protocol, runtime_checkable

from sentinelmcp.audit.models import AuditEvent
from sentinelmcp.execution import ExecutionId, RequestId


@runtime_checkable
class AuditStore(Protocol):
    def append(self, event: AuditEvent) -> None:
        """Append one immutable audit event."""

    def list_for_request(self, request_id: RequestId) -> tuple[AuditEvent, ...]:
        """Return events for one request in durable order."""

    def list_for_execution(self, execution_id: ExecutionId) -> tuple[AuditEvent, ...]:
        """Return events for one execution attempt in durable order."""
