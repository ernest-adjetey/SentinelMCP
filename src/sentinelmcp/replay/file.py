"""Append-only local replay records stored as one bounded JSON file per ID."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import final

from sentinelmcp.replay.errors import (
    ReplayIntegrityError,
    ReplayNotFoundError,
    ReplayStoreUnavailableError,
)
from sentinelmcp.replay.models import ReplayRecord, ReplayRecordId
from sentinelmcp.replay.protocol import ReplayStore
from sentinelmcp.replay.serialization import (
    replay_record_from_document,
    replay_record_to_document,
)

MAX_REPLAY_DOCUMENT_BYTES = 131_072


@final
class FileReplayStore(ReplayStore):
    """Durable local store intended for single-host operator workflows."""

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        self._root = root

    def append(self, record: ReplayRecord) -> None:
        if not isinstance(record, ReplayRecord):
            raise TypeError("record must be a ReplayRecord")
        document = replay_record_to_document(record)
        encoded = (
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_REPLAY_DOCUMENT_BYTES:
            raise ReplayIntegrityError(reason="serialized replay record exceeds the file limit")
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ReplayStoreUnavailableError(operation="append") from None
        try:
            descriptor = os.open(
                self._path(record.record_id),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                existing = self.get(record.record_id)
            except (ReplayNotFoundError, ReplayStoreUnavailableError):
                raise ReplayStoreUnavailableError(operation="append") from None
            if existing != record:
                raise ReplayIntegrityError(
                    reason="replay record ID has conflicting content"
                ) from None
            return
        except OSError:
            raise ReplayStoreUnavailableError(operation="append") from None
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise ReplayStoreUnavailableError(operation="append") from None

    def get(self, record_id: ReplayRecordId) -> ReplayRecord:
        if not isinstance(record_id, ReplayRecordId):
            raise TypeError("record_id must be a ReplayRecordId")
        try:
            encoded = self._path(record_id).read_bytes()
        except FileNotFoundError:
            raise ReplayNotFoundError(record_id.value) from None
        except OSError:
            raise ReplayStoreUnavailableError(operation="lookup") from None
        if not encoded or len(encoded) > MAX_REPLAY_DOCUMENT_BYTES:
            raise ReplayIntegrityError(reason="replay document size is invalid")
        try:
            document = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKeyError) as error:
            raise ReplayIntegrityError(reason="replay document is not strict UTF-8 JSON") from error
        return replay_record_from_document(document)

    def list_records(self) -> tuple[ReplayRecord, ...]:
        try:
            if not self._root.exists():
                return ()
            paths = tuple(self._root.glob("*.json"))
        except OSError:
            raise ReplayStoreUnavailableError(operation="list") from None
        records: list[ReplayRecord] = []
        for path in paths:
            try:
                record_id = ReplayRecordId.parse(path.stem)
            except (TypeError, ValueError):
                raise ReplayIntegrityError(
                    reason="replay directory contains an invalid filename"
                ) from None
            records.append(self.get(record_id))
        return tuple(sorted(records, key=lambda item: (item.captured_at, str(item.record_id))))

    def _path(self, record_id: ReplayRecordId) -> Path:
        return self._root / f"{record_id}.json"


class _DuplicateJSONKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result
