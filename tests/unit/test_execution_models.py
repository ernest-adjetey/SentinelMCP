"""Tests for immutable execution lifecycle values."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from sentinelmcp.domain import ToolRef, ValidationIssue
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterSuccess,
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    ExecutionSuccess,
    InvalidExecutionRequestError,
    RequestId,
)


def make_context(*, fingerprint: str | None = "a" * 64) -> ExecutionContext:
    return ExecutionContext(
        request_id=RequestId(UUID("00000000-0000-4000-8000-000000000001")),
        execution_id=ExecutionId(UUID("00000000-0000-4000-8000-000000000002")),
        tool_ref=ToolRef.parse("demo.math.add@1.0.0"),
        definition_fingerprint=fingerprint,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_monotonic=12.5,
    )


def test_execution_request_detaches_and_freezes_caller_arguments() -> None:
    values = [1, 2]
    source: dict[str, object] = {"nested": {"values": values}}

    request = ExecutionRequest(ToolRef.parse("demo.math.add@1.0.0"), source)
    values.append(3)
    source["later"] = True

    assert isinstance(request.arguments, FrozenJSONMapping)
    nested = request.arguments["nested"]
    assert isinstance(nested, FrozenJSONMapping)
    assert nested["values"] == (1, 2)
    assert "later" not in request.arguments
    with pytest.raises(TypeError):
        request.arguments["new"] = 1  # type: ignore[index]


def test_execution_request_rejects_untyped_or_non_json_values() -> None:
    with pytest.raises(InvalidExecutionRequestError, match="ToolRef"):
        ExecutionRequest("demo.math.add@1.0.0", {})  # type: ignore[arg-type]
    with pytest.raises(InvalidExecutionRequestError, match="JSON object"):
        ExecutionRequest(ToolRef.parse("demo.math.add@1.0.0"), [])  # type: ignore[arg-type]
    with pytest.raises(InvalidExecutionRequestError, match="bounded JSON"):
        ExecutionRequest(ToolRef.parse("demo.math.add@1.0.0"), {"bad": b"secret"})


def test_generated_request_and_execution_ids_are_distinct_uuid4_values() -> None:
    request_id = RequestId.generate()
    execution_id = ExecutionId.generate()

    assert request_id.value.version == 4
    assert execution_id.value.version == 4
    assert str(request_id) != str(execution_id)


def test_identifier_and_context_validation_is_strict() -> None:
    with pytest.raises(InvalidExecutionRequestError, match="request_id"):
        RequestId("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(InvalidExecutionRequestError, match="execution_id"):
        ExecutionId("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(InvalidExecutionRequestError, match="fingerprint"):
        make_context(fingerprint="ABC")
    with pytest.raises(InvalidExecutionRequestError, match="timezone-aware"):
        ExecutionContext(
            request_id=RequestId.generate(),
            execution_id=ExecutionId.generate(),
            tool_ref=ToolRef.parse("demo.math.add@1.0.0"),
            definition_fingerprint="a" * 64,
            started_at=datetime(2026, 1, 1),
            started_monotonic=0.0,
        )
    with pytest.raises(InvalidExecutionRequestError, match="finite"):
        ExecutionContext(
            request_id=RequestId.generate(),
            execution_id=ExecutionId.generate(),
            tool_ref=ToolRef.parse("demo.math.add@1.0.0"),
            definition_fingerprint=None,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            started_monotonic=float("inf"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", "bad-request-id"),
        ("execution_id", "bad-execution-id"),
        ("tool_ref", "demo.math.add@1.0.0"),
    ],
)
def test_context_rejects_untyped_identifiers(field: str, value: object) -> None:
    arguments: dict[str, object] = {
        "request_id": RequestId.generate(),
        "execution_id": ExecutionId.generate(),
        "tool_ref": ToolRef.parse("demo.math.add@1.0.0"),
        "definition_fingerprint": None,
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "started_monotonic": 0.0,
    }
    arguments[field] = value

    with pytest.raises(InvalidExecutionRequestError, match=field):
        ExecutionContext(**arguments)  # type: ignore[arg-type]


def test_adapter_invocation_and_success_are_deeply_immutable() -> None:
    context = make_context()
    arguments = ExecutionRequest(context.tool_ref, {"a": 1}).arguments
    assert isinstance(arguments, FrozenJSONMapping)
    invocation = AdapterInvocation(context, arguments)
    nested_source = [1]
    source = {"nested": nested_source}

    success = AdapterSuccess(source)
    nested_source.append(2)

    assert invocation.context is context
    assert isinstance(success.output, FrozenJSONMapping)
    assert success.output["nested"] == (1,)
    with pytest.raises(ValueError, match="resolved definition"):
        AdapterInvocation(make_context(fingerprint=None), arguments)
    with pytest.raises(TypeError, match="FrozenJSONMapping"):
        AdapterInvocation(make_context(), {})  # type: ignore[arg-type]


def test_execution_error_and_result_bounds_are_enforced() -> None:
    issue = ValidationIssue((), (), "safe", "type")
    error = ExecutionError(
        ExecutionErrorCode.INPUT_VALIDATION_FAILED,
        "Invalid input.",
        issues=(issue,),
    )
    success = ExecutionSuccess(make_context(), AdapterSuccess({"ok": True}).output, 1.25)
    failure = ExecutionFailure(make_context(), error, 0.0)

    assert isinstance(success.result, FrozenJSONMapping)
    assert success.result["ok"] is True
    assert failure.error.issues == (issue,)
    with pytest.raises(ValueError, match="bounded"):
        ExecutionError(ExecutionErrorCode.ADAPTER_FAILURE, "")
    with pytest.raises(ValueError, match="retry_after_ms"):
        ExecutionError(
            ExecutionErrorCode.RATE_LIMITED,
            "Safe failure.",
            retry_after_ms=-1,
        )
    with pytest.raises(ValueError, match="HTTP status"):
        AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE, upstream_status=700)
    with pytest.raises(TypeError, match="tuple"):
        ExecutionError(
            ExecutionErrorCode.INPUT_VALIDATION_FAILED,
            "Invalid input.",
            issues=[issue],  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="duration"):
        ExecutionFailure(make_context(), error, -1.0)
    with pytest.raises(ValueError, match="duration"):
        ExecutionFailure(make_context(), error, float("nan"))
    with pytest.raises(TypeError, match="ExecutionErrorCode"):
        AdapterFailure("upstream_timeout")  # type: ignore[arg-type]
