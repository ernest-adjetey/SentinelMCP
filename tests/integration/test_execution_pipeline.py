"""Integration tests across registry, binding, executor, and synthetic adapters."""

from __future__ import annotations

import asyncio

from sentinelmcp.application import create_demo_runtime
from sentinelmcp.domain import ToolRef
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
)
from sentinelmcp.policy import Principal

TEST_PRINCIPAL = Principal("integration-test", "https://issuer.test")


def execute(tool: str, arguments: dict[str, object]) -> ExecutionSuccess | ExecutionFailure:
    runtime = create_demo_runtime()
    result = asyncio.run(
        runtime.executor.execute(
            ExecutionRequest(ToolRef.parse(tool), arguments),
            principal=TEST_PRINCIPAL,
        )
    )
    assert isinstance(result, ExecutionSuccess | ExecutionFailure)
    return result


def test_math_tool_runs_through_complete_validated_pipeline() -> None:
    result = execute("demo.math.add@1.0.0", {"a": 2, "b": 3})

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["sum"] == 5
    assert result.context.definition_fingerprint is not None


def test_catalog_tool_returns_nested_contract_valid_output() -> None:
    result = execute("demo.catalog.lookup@1.0.0", {"id": "abc123"})

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["asset_id"] == "abc123"
    details = result.result["details"]
    assert isinstance(details, FrozenJSONMapping)
    assert details["tags"] == ("synthetic", "public")


def test_profile_tool_returns_nested_preferences() -> None:
    result = execute("demo.profile.get@1.0.0", {"username": "ernest-demo"})

    assert isinstance(result, ExecutionSuccess)
    assert isinstance(result.result, FrozenJSONMapping)
    assert result.result["display_name"] == "Ernest Demo"
    preferences = result.result["preferences"]
    assert isinstance(preferences, FrozenJSONMapping)
    assert preferences["theme"] == "light"


def test_unknown_exact_tool_and_invalid_arguments_are_structured() -> None:
    unknown = execute("demo.math.add@2.0.0", {"a": 2, "b": 3})
    invalid = execute("demo.math.add@1.0.0", {"a": "two", "b": 3})

    assert isinstance(unknown, ExecutionFailure)
    assert isinstance(invalid, ExecutionFailure)
    assert unknown.error.code is ExecutionErrorCode.TOOL_NOT_FOUND
    assert invalid.error.code is ExecutionErrorCode.INPUT_VALIDATION_FAILED


def test_registered_demo_tool_without_binding_is_structured() -> None:
    runtime = create_demo_runtime()
    reference = ToolRef.parse("demo.math.add@1.0.0")
    runtime.bindings.unbind(reference)

    result = asyncio.run(
        runtime.executor.execute(
            ExecutionRequest(reference, {"a": 2, "b": 3}),
            principal=TEST_PRINCIPAL,
        )
    )

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.BINDING_NOT_FOUND
