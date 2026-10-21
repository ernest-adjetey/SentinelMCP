"""Versioned replay-safe request records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sentinelmcp.domain import OperationClass, ToolRef
from sentinelmcp.domain.json_values import (
    FrozenJSONMapping,
    FrozenJSONValue,
    JSONValueError,
    freeze_json_mapping,
    thaw_json,
)
from sentinelmcp.execution import ExecutionErrorCode, ExecutionId, RequestId

REPLAY_SCHEMA_VERSION = 1
MAX_REPLAY_ARGUMENT_BYTES = 65_536
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "bearertoken",
        "confirmationtoken",
        "cookie",
        "credential",
        "idempotencykey",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "setcookie",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class ReplayRecordId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("replay record ID must be a UUID")

    @classmethod
    def generate(cls) -> ReplayRecordId:
        return cls(uuid4())

    @classmethod
    def parse(cls, value: str) -> ReplayRecordId:
        if not isinstance(value, str):
            raise TypeError("replay record ID text must be a string")
        return cls(UUID(value))

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """One exact historical request approved for constrained replay."""

    record_id: ReplayRecordId
    captured_at: datetime
    principal_issuer: str
    principal_subject: str
    tool_ref: ToolRef
    definition_fingerprint: str
    request_id: RequestId
    execution_id: ExecutionId
    operation_class: OperationClass
    arguments: Mapping[str, object]
    schema_version: int = REPLAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, ReplayRecordId):
            raise TypeError("record_id must be a ReplayRecordId")
        if (
            not isinstance(self.captured_at, datetime)
            or self.captured_at.tzinfo is None
            or self.captured_at.utcoffset() is None
        ):
            raise ValueError("captured_at must be timezone-aware")
        _bounded_string(self.principal_issuer, field="principal_issuer", maximum=2048)
        _bounded_string(self.principal_subject, field="principal_subject", maximum=255)
        if not isinstance(self.tool_ref, ToolRef):
            raise TypeError("tool_ref must be a ToolRef")
        if (
            not isinstance(self.definition_fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(self.definition_fingerprint) is None
        ):
            raise ValueError("definition_fingerprint must be a lowercase SHA-256 digest")
        if not isinstance(self.request_id, RequestId):
            raise TypeError("request_id must be a RequestId")
        if not isinstance(self.execution_id, ExecutionId):
            raise TypeError("execution_id must be an ExecutionId")
        if not isinstance(self.operation_class, OperationClass):
            raise TypeError("operation_class must be an OperationClass")
        if isinstance(self.schema_version, bool) or self.schema_version != REPLAY_SCHEMA_VERSION:
            raise ValueError("unsupported replay schema version")
        try:
            arguments = freeze_json_mapping(self.arguments)
        except JSONValueError as error:
            raise ValueError("arguments must be bounded JSON data") from error
        _reject_sensitive_keys(arguments)
        encoded = json.dumps(
            thaw_json(arguments),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > MAX_REPLAY_ARGUMENT_BYTES:
            raise ValueError("arguments exceed the replay size limit")
        object.__setattr__(self, "arguments", arguments)


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    record_id: ReplayRecordId
    tool_ref: ToolRef
    definition_fingerprint: str
    can_execute: bool
    error_code: ExecutionErrorCode | None = None
    target_kind: str = "synthetic"


def _reject_sensitive_keys(value: FrozenJSONValue) -> None:
    if isinstance(value, FrozenJSONMapping):
        for key, item in value.items():
            if _normalize_key(key) in _SENSITIVE_KEYS:
                raise ValueError("arguments contain a sensitive field name")
            _reject_sensitive_keys(item)
    elif isinstance(value, tuple):
        for item in value:
            _reject_sensitive_keys(item)


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def _bounded_string(value: object, *, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be a non-empty string of at most {maximum} characters")
