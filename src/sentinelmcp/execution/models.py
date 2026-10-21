"""Immutable values for one tool execution lifecycle."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sentinelmcp.domain.errors import SentinelMCPError, ValidationIssue
from sentinelmcp.domain.json_values import (
    FrozenJSONMapping,
    FrozenJSONValue,
    JSONValueError,
    freeze_json,
    freeze_json_mapping,
)
from sentinelmcp.domain.tools import ToolRef

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_ERROR_MESSAGE_LENGTH = 500


class InvalidExecutionRequestError(SentinelMCPError):
    """Raised when an execution request cannot become bounded immutable JSON."""

    code = "invalid_execution_request"

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Invalid execution request field {field!r}: {reason}")


@dataclass(frozen=True, slots=True)
class RequestId:
    """A gateway request identifier generated internally with UUID4."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise InvalidExecutionRequestError(field="request_id", reason="must be a UUID")

    @classmethod
    def generate(cls) -> RequestId:
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ExecutionId:
    """A distinct identifier for one dispatch attempt."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise InvalidExecutionRequestError(field="execution_id", reason="must be a UUID")

    @classmethod
    def generate(cls) -> ExecutionId:
        return cls(uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """An exact tool reference and detached immutable argument object."""

    tool_ref: ToolRef
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.tool_ref, ToolRef):
            raise InvalidExecutionRequestError(field="tool_ref", reason="must be a ToolRef")
        if not isinstance(self.arguments, Mapping):
            raise InvalidExecutionRequestError(field="arguments", reason="must be a JSON object")
        try:
            frozen = freeze_json_mapping(self.arguments)
        except JSONValueError as error:
            raise InvalidExecutionRequestError(
                field="arguments",
                reason="must be bounded JSON data",
            ) from error
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Trusted metadata associated with one execution attempt."""

    request_id: RequestId
    execution_id: ExecutionId
    tool_ref: ToolRef
    definition_fingerprint: str | None
    started_at: datetime
    started_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, RequestId):
            raise InvalidExecutionRequestError(field="request_id", reason="must be a RequestId")
        if not isinstance(self.execution_id, ExecutionId):
            raise InvalidExecutionRequestError(
                field="execution_id", reason="must be an ExecutionId"
            )
        if not isinstance(self.tool_ref, ToolRef):
            raise InvalidExecutionRequestError(field="tool_ref", reason="must be a ToolRef")
        fingerprint: object = self.definition_fingerprint
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or not _FINGERPRINT_PATTERN.fullmatch(fingerprint)
        ):
            raise InvalidExecutionRequestError(
                field="definition_fingerprint",
                reason="must be a lowercase SHA-256 digest",
            )
        started_at: object = self.started_at
        if not isinstance(started_at, datetime) or (
            started_at.tzinfo is None or started_at.utcoffset() is None
        ):
            raise InvalidExecutionRequestError(
                field="started_at",
                reason="must be timezone-aware",
            )
        started_monotonic: object = self.started_monotonic
        if (
            isinstance(started_monotonic, bool)
            or not isinstance(started_monotonic, int | float)
            or not math.isfinite(started_monotonic)
        ):
            raise InvalidExecutionRequestError(
                field="started_monotonic",
                reason="must be finite",
            )


class ExecutionErrorCode(StrEnum):
    """Stable execution failure categories shared by every transport."""

    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    TOOL_NOT_FOUND = "tool_not_found"
    INPUT_VALIDATION_FAILED = "input_validation_failed"
    AUTHORIZATION_FAILED = "authorization_failed"
    MUTATION_NOT_SUPPORTED = "mutation_not_supported"
    MUTATION_CONFIRMATION_INVALID = "mutation_confirmation_invalid"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_IN_PROGRESS = "idempotency_in_progress"
    MUTATION_STATE_UNAVAILABLE = "mutation_state_unavailable"
    MUTATION_OUTCOME_UNKNOWN = "mutation_outcome_unknown"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    BINDING_NOT_FOUND = "binding_not_found"
    ADAPTER_FAILURE = "adapter_failure"
    UPSTREAM_BAD_REQUEST = "upstream_bad_request"
    UPSTREAM_AUTHENTICATION_FAILED = "upstream_authentication_failed"
    UPSTREAM_AUTHORIZATION_FAILED = "upstream_authorization_failed"
    UPSTREAM_NOT_FOUND = "upstream_not_found"
    UPSTREAM_CONFLICT = "upstream_conflict"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_STATUS_ERROR = "upstream_status_error"
    UPSTREAM_SERVER_ERROR = "upstream_server_error"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_INVALID_CONTENT_TYPE = "upstream_invalid_content_type"
    UPSTREAM_MALFORMED_RESPONSE = "upstream_malformed_response"
    UPSTREAM_RESPONSE_TOO_LARGE = "upstream_response_too_large"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    INTERNAL_EXECUTION_ERROR = "internal_execution_error"


@dataclass(frozen=True, slots=True)
class ExecutionError:
    """A bounded client-safe failure without raw exception or body content."""

    code: ExecutionErrorCode
    message: str
    upstream_status: int | None = None
    retry_after_ms: int | None = None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, ExecutionErrorCode):
            raise TypeError("code must be an ExecutionErrorCode")
        message: object = self.message
        if not isinstance(message, str) or not message or len(message) > _MAX_ERROR_MESSAGE_LENGTH:
            raise ValueError("message must be non-empty and bounded")
        upstream_status: object = self.upstream_status
        if upstream_status is not None and (
            isinstance(upstream_status, bool)
            or not isinstance(upstream_status, int)
            or not 100 <= upstream_status <= 599
        ):
            raise ValueError("upstream_status must be a valid HTTP status")
        retry_after_ms: object = self.retry_after_ms
        if retry_after_ms is not None and (
            isinstance(retry_after_ms, bool)
            or not isinstance(retry_after_ms, int)
            or not 0 <= retry_after_ms <= 300_000
        ):
            raise ValueError("retry_after_ms must be from 0 through 300,000")
        raw_issues: object = self.issues
        if not isinstance(raw_issues, tuple):
            raise TypeError("issues must be a tuple")


@dataclass(frozen=True, slots=True)
class AdapterInvocation:
    """The immutable input passed from the executor to an adapter."""

    context: ExecutionContext
    arguments: FrozenJSONMapping

    def __post_init__(self) -> None:
        if not isinstance(self.context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if self.context.definition_fingerprint is None:
            raise ValueError("adapter invocation requires a resolved definition fingerprint")
        if not isinstance(self.arguments, FrozenJSONMapping):
            raise TypeError("adapter arguments must be a FrozenJSONMapping")


@dataclass(frozen=True, slots=True, init=False)
class AdapterSuccess:
    """A successful adapter result detached from adapter-owned mutable values."""

    output: FrozenJSONValue

    def __init__(self, output: object) -> None:
        object.__setattr__(self, "output", freeze_json(output))


@dataclass(frozen=True, slots=True)
class AdapterFailure:
    """A normalized adapter failure with no raw exception or response body."""

    code: ExecutionErrorCode
    upstream_status: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ExecutionErrorCode):
            raise TypeError("code must be an ExecutionErrorCode")
        upstream_status: object = self.upstream_status
        if upstream_status is not None and (
            isinstance(upstream_status, bool)
            or not isinstance(upstream_status, int)
            or not 100 <= upstream_status <= 599
        ):
            raise ValueError("upstream_status must be a valid HTTP status")


type AdapterResult = AdapterSuccess | AdapterFailure


@dataclass(frozen=True, slots=True)
class ExecutionSuccess:
    """A contract-valid successful execution."""

    context: ExecutionContext
    result: FrozenJSONValue
    duration_ms: float

    def __post_init__(self) -> None:
        _validate_duration(self.duration_ms)
        object.__setattr__(self, "result", freeze_json(self.result))


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    """A deterministic failure for one execution attempt."""

    context: ExecutionContext
    error: ExecutionError
    duration_ms: float

    def __post_init__(self) -> None:
        _validate_duration(self.duration_ms)


type ExecutionResult = ExecutionSuccess | ExecutionFailure


def _validate_duration(duration_ms: float) -> None:
    raw_duration: object = duration_ms
    if (
        isinstance(raw_duration, bool)
        or not isinstance(raw_duration, int | float)
        or not math.isfinite(raw_duration)
        or raw_duration < 0
    ):
        raise ValueError("duration_ms must be finite and non-negative")
