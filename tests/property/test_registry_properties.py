"""Property tests for immutable registry and schema invariants."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sentinelmcp.domain import (
    ArgumentValidationError,
    ImmutableToolVersionConflict,
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.registry import (
    InMemoryToolRegistry,
    RegistrationStatus,
    canonicalize_tool_definition,
    fingerprint_tool_definition,
    validate_tool_definition_schemas,
)

SAFE_JSON_SCALARS = st.none() | st.booleans() | st.integers(-(2**53) + 1, 2**53 - 1) | st.text()
SAFE_JSON = st.recursive(
    SAFE_JSON_SCALARS,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=20), children, max_size=5)
    ),
    max_leaves=20,
)
SEMVER_COMPONENT = st.integers(min_value=0, max_value=100)
DESCRIPTION = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=80,
)


def make_definition(
    *,
    version: str = "1.0.0",
    description: str = "Lookup.",
    input_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion(version),
        description=description,
        input_schema=input_schema or {"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
    )


def reverse_object_order(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: reverse_object_order(child) for key, child in reversed(tuple(value.items()))}
    if isinstance(value, list):
        return [reverse_object_order(child) for child in value]
    return value


@settings(max_examples=75, deadline=None)
@given(SAFE_JSON)
def test_canonicalization_and_fingerprint_are_stable_under_object_reordering(
    value: object,
) -> None:
    original = make_definition(input_schema={"const": value})
    reordered_schema = reverse_object_order({"const": value})
    assert isinstance(reordered_schema, dict)
    reordered = make_definition(input_schema=reordered_schema)

    assert canonicalize_tool_definition(original) == canonicalize_tool_definition(reordered)
    assert fingerprint_tool_definition(original) == fingerprint_tool_definition(reordered)


@settings(max_examples=75, deadline=None)
@given(st.lists(st.integers(min_value=-100, max_value=100), max_size=20))
def test_definition_is_isolated_from_caller_mutation(values: list[int]) -> None:
    enum_values = list(values)
    source: dict[str, object] = {"enum": enum_values}
    definition = make_definition(input_schema=source)
    before = fingerprint_tool_definition(definition)

    enum_values.append(10_000)
    source["description"] = "caller mutation"

    assert fingerprint_tool_definition(definition) == before
    assert 10_000 not in definition.input_schema["enum"]  # type: ignore[operator]


@settings(max_examples=50, deadline=None)
@given(DESCRIPTION)
def test_registration_is_idempotent_for_identical_content(description: str) -> None:
    registry = InMemoryToolRegistry()
    definition = make_definition(description=description)

    first = registry.register(definition)
    second = registry.register(definition)

    assert first.status is RegistrationStatus.REGISTERED
    assert second.status is RegistrationStatus.ALREADY_REGISTERED
    assert second.fingerprint == first.fingerprint
    assert registry.list_versions(definition.identity) == (definition.version,)


@settings(max_examples=50, deadline=None)
@given(st.sets(st.tuples(SEMVER_COMPONENT, SEMVER_COMPONENT, SEMVER_COMPONENT), max_size=12))
def test_registered_versions_remain_isolated(
    components: set[tuple[int, int, int]],
) -> None:
    registry = InMemoryToolRegistry()
    definitions = [
        make_definition(version=f"{major}.{minor}.{patch}") for major, minor, patch in components
    ]
    for definition in definitions:
        registry.register(definition)

    for definition in definitions:
        assert registry.get(definition.ref) is definition
    assert registry.list_versions(make_definition().identity) == tuple(
        sorted(definition.version for definition in definitions)
    )


@settings(max_examples=50, deadline=None)
@given(DESCRIPTION, DESCRIPTION)
def test_conflicting_content_never_overwrites(
    first_description: str, second_description: str
) -> None:
    if first_description == second_description:
        return
    registry = InMemoryToolRegistry()
    original = make_definition(description=first_description)
    conflicting = make_definition(description=second_description)
    registry.register(original)

    with pytest.raises(ImmutableToolVersionConflict):
        registry.register(conflicting)

    assert registry.get(original.ref) is original


@settings(max_examples=75, deadline=None)
@given(st.integers(min_value=-10_000, max_value=10_000))
def test_runtime_validation_accepts_valid_and_rejects_invalid_integers(value: int) -> None:
    validator = validate_tool_definition_schemas(
        make_definition(
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer", "const": value}},
                "required": ["value"],
            }
        )
    )

    validator.validate_arguments({"value": value})
    with pytest.raises(ArgumentValidationError):
        validator.validate_arguments({"value": value + 1})
