"""Storage-independent replay record contract."""

from typing import Protocol, runtime_checkable

from sentinelmcp.replay.models import ReplayRecord, ReplayRecordId


@runtime_checkable
class ReplayStore(Protocol):
    def append(self, record: ReplayRecord) -> None:
        """Append one immutable replay record."""

    def get(self, record_id: ReplayRecordId) -> ReplayRecord:
        """Return one replay record or raise ReplayNotFoundError."""

    def list_records(self) -> tuple[ReplayRecord, ...]:
        """Return replay records in deterministic capture order."""
