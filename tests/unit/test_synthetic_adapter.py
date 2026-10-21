"""Tests for safe normalization by the in-process synthetic adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest

from sentinelmcp.domain import ToolRef
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    ExecutionContext,
    ExecutionErrorCode,
    ExecutionId,
    ExecutionRequest,
    RequestId,
    SyntheticToolAdapter,
)


def make_invocation() -> AdapterInvocation:
    reference = ToolRef.parse("demo.synthetic.run@1.0.0")
    arguments = ExecutionRequest(reference, {}).arguments
    assert isinstance(arguments, FrozenJSONMapping)
    return AdapterInvocation(
        ExecutionContext(
            request_id=RequestId(UUID("00000000-0000-4000-8000-000000000001")),
            execution_id=ExecutionId(UUID("00000000-0000-4000-8000-000000000002")),
            tool_ref=reference,
            definition_fingerprint="a" * 64,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            started_monotonic=1.0,
        ),
        arguments,
    )


def test_synthetic_adapter_rejects_non_callable_handler() -> None:
    with pytest.raises(TypeError, match="callable"):
        SyntheticToolAdapter(object())  # type: ignore[arg-type]


def test_synthetic_adapter_classifies_non_json_output_as_output_failure() -> None:
    async def invalid_output(_arguments: object) -> object:
        return object()

    result = asyncio.run(SyntheticToolAdapter(invalid_output).execute(make_invocation()))

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.OUTPUT_VALIDATION_FAILED


def test_synthetic_adapter_hides_handler_exception_as_adapter_failure() -> None:
    async def exploding(_arguments: object) -> object:
        raise RuntimeError("private handler detail")

    result = asyncio.run(SyntheticToolAdapter(exploding).execute(make_invocation()))

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.ADAPTER_FAILURE
