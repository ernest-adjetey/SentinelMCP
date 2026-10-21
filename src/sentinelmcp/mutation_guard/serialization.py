"""Bounded durable serialization for authoritative idempotency outcomes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

from sentinelmcp.domain import ToolRef, ValidationIssue
from sentinelmcp.domain.json_values import freeze_json, thaw_json
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionResult,
    ExecutionSuccess,
    RequestId,
)
from sentinelmcp.mutation_guard.errors import MutationStateIntegrityError

MAX_OUTCOME_BYTES = 10_000_000


def serialize_execution_result(result: ExecutionResult) -> bytes:
    """Serialize one safe normalized result without raw exceptions or credentials."""

    context = {
        "definition_fingerprint": result.context.definition_fingerprint,
        "execution_id": str(result.context.execution_id),
        "request_id": str(result.context.request_id),
        "started_at": result.context.started_at.isoformat(),
        "started_monotonic": result.context.started_monotonic,
        "tool_ref": str(result.context.tool_ref),
    }
    if isinstance(result, ExecutionSuccess):
        document: dict[str, object] = {
            "context": context,
            "duration_ms": result.duration_ms,
            "kind": "success",
            "result": thaw_json(result.result),
        }
    else:
        document = {
            "context": context,
            "duration_ms": result.duration_ms,
            "error": {
                "code": result.error.code.value,
                "issues": [
                    {
                        "instance_path": list(issue.instance_path),
                        "message": issue.message,
                        "schema_path": list(issue.schema_path),
                        "validator": issue.validator,
                    }
                    for issue in result.error.issues
                ],
                "message": result.error.message,
                "retry_after_ms": result.error.retry_after_ms,
                "upstream_status": result.error.upstream_status,
            },
            "kind": "failure",
        }
    try:
        encoded = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("execution outcome is not finite UTF-8 JSON") from error
    if len(encoded) > MAX_OUTCOME_BYTES:
        raise ValueError(f"execution outcome exceeds {MAX_OUTCOME_BYTES} bytes")
    return encoded


def deserialize_execution_result(encoded: bytes) -> ExecutionResult:
    """Reconstruct a previously normalized result or report durable corruption."""

    if len(encoded) > MAX_OUTCOME_BYTES:
        raise MutationStateIntegrityError(reason="persisted outcome exceeds the size limit")
    try:
        value = json.loads(encoded.decode("utf-8"), parse_constant=_reject_json_constant)
        document = _mapping(value, field="outcome")
        if frozenset(document) not in {
            frozenset({"context", "duration_ms", "kind", "result"}),
            frozenset({"context", "duration_ms", "error", "kind"}),
        }:
            raise ValueError("outcome fields are incomplete or unknown")
        context = _context(_mapping(document.get("context"), field="context"))
        duration_ms = _number(document.get("duration_ms"), field="duration_ms")
        kind = _string(document, "kind")
        if kind == "success":
            return ExecutionSuccess(
                context=context,
                result=freeze_json(document.get("result")),
                duration_ms=duration_ms,
            )
        if kind == "failure":
            error = _execution_error(_mapping(document.get("error"), field="error"))
            return ExecutionFailure(context=context, error=error, duration_ms=duration_ms)
        raise ValueError("outcome kind is invalid")
    except MutationStateIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MutationStateIntegrityError(reason="persisted outcome is invalid") from error


def _context(document: Mapping[str, object]) -> ExecutionContext:
    if frozenset(document) != frozenset(
        {
            "definition_fingerprint",
            "execution_id",
            "request_id",
            "started_at",
            "started_monotonic",
            "tool_ref",
        }
    ):
        raise ValueError("outcome context fields are incomplete or unknown")
    fingerprint = document.get("definition_fingerprint")
    if not isinstance(fingerprint, str):
        raise ValueError("definition fingerprint is invalid")
    return ExecutionContext(
        request_id=RequestId(UUID(_string(document, "request_id"))),
        execution_id=ExecutionId(UUID(_string(document, "execution_id"))),
        tool_ref=ToolRef.parse(_string(document, "tool_ref")),
        definition_fingerprint=fingerprint,
        started_at=datetime.fromisoformat(_string(document, "started_at")),
        started_monotonic=_number(document.get("started_monotonic"), field="started_monotonic"),
    )


def _execution_error(document: Mapping[str, object]) -> ExecutionError:
    fields = frozenset(document)
    legacy_fields = frozenset({"code", "issues", "message", "upstream_status"})
    if fields not in {legacy_fields, legacy_fields | {"retry_after_ms"}}:
        raise ValueError("outcome error fields are incomplete or unknown")
    upstream_status = document.get("upstream_status")
    if upstream_status is not None and (
        isinstance(upstream_status, bool) or not isinstance(upstream_status, int)
    ):
        raise ValueError("upstream status is invalid")
    retry_after_ms = document.get("retry_after_ms")
    if retry_after_ms is not None and (
        isinstance(retry_after_ms, bool) or not isinstance(retry_after_ms, int)
    ):
        raise ValueError("retry guidance is invalid")
    raw_issues = document.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("outcome issues must be an array")
    return ExecutionError(
        code=ExecutionErrorCode(_string(document, "code")),
        message=_string(document, "message"),
        upstream_status=upstream_status,
        retry_after_ms=retry_after_ms,
        issues=tuple(_issue(_mapping(value, field="issue")) for value in raw_issues),
    )


def _issue(document: Mapping[str, object]) -> ValidationIssue:
    if frozenset(document) != frozenset({"instance_path", "message", "schema_path", "validator"}):
        raise ValueError("outcome issue fields are incomplete or unknown")
    validator = document.get("validator")
    if validator is not None and not isinstance(validator, str):
        raise ValueError("outcome issue validator is invalid")
    return ValidationIssue(
        instance_path=_path(document.get("instance_path")),
        schema_path=_path(document.get("schema_path")),
        message=_string(document, "message"),
        validator=validator,
    )


def _path(value: object) -> tuple[str | int, ...]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, str | int) for item in value
    ):
        raise ValueError("outcome issue path is invalid")
    return tuple(cast(list[str | int], value))


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")
