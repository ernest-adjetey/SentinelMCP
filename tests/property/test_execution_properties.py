"""Generated checks for the highest-value execution safety invariants."""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from sentinelmcp.application import ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
)
from sentinelmcp.registry import InMemoryToolRegistry


class PropertyAdapter:
    def __init__(self, output: object) -> None:
        self.output = output
        self.call_count = 0

    async def execute(self, _invocation: AdapterInvocation) -> AdapterResult:
        self.call_count += 1
        return AdapterSuccess(self.output)


def make_executor(adapter: PropertyAdapter) -> tuple[ToolExecutor, ToolDefinition]:
    definition = ToolDefinition(
        namespace=ToolNamespace("demo.property"),
        name=ToolName("check"),
        version=ToolVersion("1.0.0"),
        description="Exercise generated execution invariants.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
    )
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    return ToolExecutor(registry, bindings), definition


INVALID_NUMBERS = st.one_of(
    st.none(),
    st.booleans(),
    st.text(max_size=30),
    st.lists(st.integers(), max_size=5),
    st.dictionaries(st.text(max_size=10), st.integers(), max_size=3),
)

INVALID_OUTPUTS = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=30),
    st.lists(st.integers(), max_size=5),
    st.fixed_dictionaries({"value": st.integers()}),
)


@settings(max_examples=75, deadline=None)
@given(INVALID_NUMBERS)
def test_generated_invalid_arguments_never_invoke_adapter(value: object) -> None:
    adapter = PropertyAdapter({"value": "valid"})
    executor, definition = make_executor(adapter)

    result = asyncio.run(executor.execute(ExecutionRequest(definition.ref, {"value": value})))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.INPUT_VALIDATION_FAILED
    assert adapter.call_count == 0


@settings(max_examples=75, deadline=None)
@given(INVALID_OUTPUTS)
def test_generated_contract_invalid_outputs_never_become_success(output: object) -> None:
    adapter = PropertyAdapter(output)
    executor, definition = make_executor(adapter)

    result = asyncio.run(executor.execute(ExecutionRequest(definition.ref, {"value": 1})))

    assert isinstance(result, ExecutionFailure)
    assert result.error.code is ExecutionErrorCode.OUTPUT_VALIDATION_FAILED
    assert adapter.call_count == 1


@settings(max_examples=75, deadline=None)
@given(st.lists(st.integers(), max_size=20))
def test_execution_request_isolated_from_generated_caller_mutation(values: list[int]) -> None:
    original = list(values)
    source: dict[str, object] = {"values": original}
    request = ExecutionRequest(
        make_executor(PropertyAdapter({"value": "valid"}))[1].ref,
        source,
    )

    original.append(999_999)
    source["later"] = True

    assert request.arguments["values"] == tuple(values)
    assert "later" not in request.arguments
