"""Strict replay-record serialization."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sentinelmcp.domain import OperationClass, ToolName, ToolNamespace, ToolRef, ToolVersion
from sentinelmcp.domain.errors import SentinelMCPError
from sentinelmcp.domain.json_values import FrozenJSONMapping, thaw_json
from sentinelmcp.execution import ExecutionId, RequestId
from sentinelmcp.replay.errors import ReplayIntegrityError
from sentinelmcp.replay.models import ReplayRecord, ReplayRecordId

_FIELDS = {
    "schema_version",
    "record_id",
    "captured_at",
    "principal_issuer",
    "principal_subject",
    "tool_ref",
    "definition_fingerprint",
    "request_id",
    "execution_id",
    "operation_class",
    "arguments",
}


def replay_record_to_document(record: ReplayRecord) -> dict[str, object]:
    if not isinstance(record, ReplayRecord):
        raise TypeError("record must be a ReplayRecord")
    return {
        "schema_version": record.schema_version,
        "record_id": str(record.record_id),
        "captured_at": record.captured_at.isoformat(),
        "principal_issuer": record.principal_issuer,
        "principal_subject": record.principal_subject,
        "tool_ref": {
            "namespace": str(record.tool_ref.namespace),
            "name": str(record.tool_ref.name),
            "version": str(record.tool_ref.version),
        },
        "definition_fingerprint": record.definition_fingerprint,
        "request_id": str(record.request_id),
        "execution_id": str(record.execution_id),
        "operation_class": record.operation_class.value,
        "arguments": thaw_json(cast(FrozenJSONMapping, record.arguments)),
    }


def replay_record_from_document(document: object) -> ReplayRecord:
    try:
        if not isinstance(document, dict) or set(document) != _FIELDS:
            raise ValueError("document fields are not the versioned replay schema")
        tool = document["tool_ref"]
        if not isinstance(tool, dict) or set(tool) != {"namespace", "name", "version"}:
            raise ValueError("tool_ref is invalid")
        arguments = document["arguments"]
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        return ReplayRecord(
            record_id=ReplayRecordId(UUID(_string(document, "record_id"))),
            captured_at=datetime.fromisoformat(_string(document, "captured_at")),
            principal_issuer=_string(document, "principal_issuer"),
            principal_subject=_string(document, "principal_subject"),
            tool_ref=ToolRef(
                ToolNamespace(_string(tool, "namespace")),
                ToolName(_string(tool, "name")),
                ToolVersion(_string(tool, "version")),
            ),
            definition_fingerprint=_string(document, "definition_fingerprint"),
            request_id=RequestId(UUID(_string(document, "request_id"))),
            execution_id=ExecutionId(UUID(_string(document, "execution_id"))),
            operation_class=OperationClass(_string(document, "operation_class")),
            arguments=cast(dict[str, object], arguments),
            schema_version=_integer(document, "schema_version"),
        )
    except (KeyError, TypeError, ValueError, SentinelMCPError) as error:
        raise ReplayIntegrityError(reason="replay document violates schema") from error


def _string(document: dict[object, object], field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} is not a string")
    return value


def _integer(document: dict[object, object], field: str) -> int:
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} is not an integer")
    return value
