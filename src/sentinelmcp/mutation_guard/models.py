"""Immutable models for mutation confirmation and idempotency state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.domain.tools import ToolRef
from sentinelmcp.execution import ExecutionId, RequestId

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,253}[A-Za-z0-9])?$")
MAX_IDEMPOTENCY_KEY_LENGTH = 255


@dataclass(frozen=True, slots=True, order=True)
class ChallengeId:
    """Public stable identifier for one opaque confirmation challenge."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("challenge ID value must be a UUID")

    @classmethod
    def generate(cls) -> ChallengeId:
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, order=True)
class IdempotencyRecordId:
    """Durable identifier for one accepted mutation record."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("idempotency record ID value must be a UUID")

    @classmethod
    def generate(cls) -> IdempotencyRecordId:
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    """Caller-selected key bounded for durable scope uniqueness."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _IDEMPOTENCY_KEY_PATTERN.fullmatch(self.value):
            raise ValueError(
                "idempotency key must contain 1-255 ASCII letters, digits, '.', '_', ':', or '-'"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class MutationBinding:
    """Exact immutable values to which a confirmation challenge is bound."""

    principal_issuer: str
    principal_subject: str
    tool_ref: ToolRef
    definition_fingerprint: str
    arguments_digest: str

    def __post_init__(self) -> None:
        if not self.principal_issuer or not self.principal_subject:
            raise ValueError("mutation binding principal must be non-empty")
        if not isinstance(self.tool_ref, ToolRef):
            raise TypeError("mutation binding tool_ref must be a ToolRef")
        if not _DIGEST_PATTERN.fullmatch(self.definition_fingerprint):
            raise ValueError("definition fingerprint must be a lowercase SHA-256 digest")
        if not _DIGEST_PATTERN.fullmatch(self.arguments_digest):
            raise ValueError("arguments digest must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class MutationChallengeRecord:
    """Durable challenge content; the raw token is intentionally absent."""

    challenge_id: ChallengeId
    token_digest: bytes
    binding: MutationBinding
    expires_at: datetime

    def __post_init__(self) -> None:
        if len(self.token_digest) != 32:
            raise ValueError("confirmation token digest must contain 32 bytes")
        _require_aware(self.expires_at, field="expires_at")


@dataclass(frozen=True, slots=True)
class MutationPreview:
    """Caller-visible preview and raw single-use token returned exactly once."""

    challenge_id: ChallengeId
    request_id: RequestId
    confirmation_token: str
    tool_ref: ToolRef
    definition_fingerprint: str
    arguments: FrozenJSONMapping
    warnings: tuple[str, ...]
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.confirmation_token:
            raise ValueError("confirmation token must be non-empty")
        if not self.warnings:
            raise ValueError("mutation preview must contain at least one warning")
        _require_aware(self.expires_at, field="expires_at")


class IdempotencyRecordState(StrEnum):
    """Durable mutation delivery states."""

    PENDING = "pending"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    OUTCOME_UNKNOWN = "outcome_unknown"


class IdempotencyClaimStatus(StrEnum):
    """Result of atomically claiming one scoped idempotency key."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    REUSED = "reused"


@dataclass(frozen=True, slots=True)
class IdempotencyClaimRequest:
    """All trusted values required for atomic challenge consumption and key claim."""

    record_id: IdempotencyRecordId
    confirmation_token_digest: bytes
    binding: MutationBinding
    idempotency_key: IdempotencyKey
    request_digest: str
    request_id: RequestId
    execution_id: ExecutionId
    started_at: datetime
    now: datetime

    def __post_init__(self) -> None:
        if len(self.confirmation_token_digest) != 32:
            raise ValueError("confirmation token digest must contain 32 bytes")
        if not _DIGEST_PATTERN.fullmatch(self.request_digest):
            raise ValueError("request digest must be a lowercase SHA-256 digest")
        _require_aware(self.started_at, field="started_at")
        _require_aware(self.now, field="now")


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """Atomic claim result, optionally carrying one durable terminal outcome."""

    status: IdempotencyClaimStatus
    record_id: IdempotencyRecordId
    state: IdempotencyRecordState
    outcome: bytes | None = None

    def __post_init__(self) -> None:
        if (
            self.status is IdempotencyClaimStatus.NEW
            and self.state is not IdempotencyRecordState.PENDING
        ):
            raise ValueError("new idempotency claims must be pending")
        if (
            self.status is IdempotencyClaimStatus.IN_PROGRESS
            and self.state is not IdempotencyRecordState.PENDING
        ):
            raise ValueError("in-progress idempotency claims must be pending")
        if (
            self.status is IdempotencyClaimStatus.REUSED
            and self.state is IdempotencyRecordState.PENDING
        ):
            raise ValueError("reused idempotency claims must be terminal")
        if (self.state is IdempotencyRecordState.PENDING) is (self.outcome is not None):
            raise ValueError("only terminal idempotency claims carry an outcome")


def _require_aware(value: datetime, *, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
