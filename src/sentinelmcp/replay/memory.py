"""Thread-safe in-memory replay storage."""

from threading import RLock
from typing import final

from sentinelmcp.replay.errors import ReplayIntegrityError, ReplayNotFoundError
from sentinelmcp.replay.models import ReplayRecord, ReplayRecordId
from sentinelmcp.replay.protocol import ReplayStore


@final
class InMemoryReplayStore(ReplayStore):
    __slots__ = ("_lock", "_records")

    def __init__(self) -> None:
        self._records: dict[ReplayRecordId, ReplayRecord] = {}
        self._lock = RLock()

    def append(self, record: ReplayRecord) -> None:
        if not isinstance(record, ReplayRecord):
            raise TypeError("record must be a ReplayRecord")
        with self._lock:
            existing = self._records.get(record.record_id)
            if existing is not None:
                if existing != record:
                    raise ReplayIntegrityError(reason="replay record ID has conflicting content")
                return
            self._records[record.record_id] = record

    def get(self, record_id: ReplayRecordId) -> ReplayRecord:
        if not isinstance(record_id, ReplayRecordId):
            raise TypeError("record_id must be a ReplayRecordId")
        with self._lock:
            try:
                return self._records[record_id]
            except KeyError:
                raise ReplayNotFoundError(record_id.value) from None

    def list_records(self) -> tuple[ReplayRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (record.captured_at, str(record.record_id)),
                )
            )
