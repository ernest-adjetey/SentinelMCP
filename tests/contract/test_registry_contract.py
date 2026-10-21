"""Storage-neutral contract tests for every ToolRegistry implementation."""

from __future__ import annotations

from typing import cast

import pytest

from sentinelmcp.domain import (
    ImmutableToolVersionConflict,
    InvalidSchemaError,
    OperationClass,
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolNotFoundError,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.registry import (
    InMemoryToolRegistry,
    RegistrationStatus,
    ToolRegistry,
    fingerprint_tool_definition,
)


def make_definition(
    *,
    namespace: str = "catalog.asset",
    name: str = "lookup",
    version: str = "1.0.0",
    description: str = "Look up one catalog asset.",
    input_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace(namespace),
        name=ToolName(name),
        version=ToolVersion(version),
        description=description,
        input_schema=input_schema or {"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
    )


@pytest.fixture(
    params=(
        "memory",
        pytest.param("postgres", marks=pytest.mark.postgres),
    )
)
def registry(request: pytest.FixtureRequest) -> ToolRegistry:
    backend = cast(str, request.param)
    if backend == "memory":
        return InMemoryToolRegistry()
    return cast(ToolRegistry, request.getfixturevalue("postgres_registry"))


def test_registry_satisfies_storage_independent_protocol(registry: ToolRegistry) -> None:
    assert isinstance(registry, ToolRegistry)


def test_new_exact_version_is_registered_and_retrievable(registry: ToolRegistry) -> None:
    definition = make_definition()

    result = registry.register(definition)

    assert result.status is RegistrationStatus.REGISTERED
    assert result.definition == definition
    assert result.fingerprint == fingerprint_tool_definition(definition)
    assert registry.exists(definition.ref)
    assert registry.get(definition.ref) == definition


def test_identical_exact_reregistration_is_explicit_and_idempotent(
    registry: ToolRegistry,
) -> None:
    original = make_definition(
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}}
    )
    reordered_copy = make_definition(
        input_schema={"properties": {"id": {"type": "string"}}, "type": "object"}
    )

    first = registry.register(original)
    second = registry.register(reordered_copy)

    assert first.status is RegistrationStatus.REGISTERED
    assert second.status is RegistrationStatus.ALREADY_REGISTERED
    assert second.definition == original
    assert second.fingerprint == first.fingerprint
    assert registry.list_versions(original.identity) == (ToolVersion("1.0.0"),)


def test_conflicting_exact_version_is_rejected_without_overwrite(registry: ToolRegistry) -> None:
    original = make_definition()
    conflicting = make_definition(description="A conflicting immutable definition.")
    registered = registry.register(original)

    with pytest.raises(ImmutableToolVersionConflict) as captured:
        registry.register(conflicting)

    assert captured.value.tool_ref == original.ref
    assert captured.value.existing_fingerprint == registered.fingerprint
    assert captured.value.proposed_fingerprint == fingerprint_tool_definition(conflicting)
    assert registry.get(original.ref) == original


def test_new_versions_are_isolated_and_latest_uses_semver(registry: ToolRegistry) -> None:
    definitions = [
        make_definition(version="10.0.0"),
        make_definition(version="2.0.0-rc.1"),
        make_definition(version="1.9.9"),
        make_definition(version="2.0.0"),
    ]
    for definition in definitions:
        registry.register(definition)

    identity = definitions[0].identity
    assert registry.list_versions(identity) == (
        ToolVersion("1.9.9"),
        ToolVersion("2.0.0-rc.1"),
        ToolVersion("2.0.0"),
        ToolVersion("10.0.0"),
    )
    assert registry.latest(identity).version == ToolVersion("10.0.0")
    assert all(registry.get(definition.ref) == definition for definition in definitions)


def test_latest_has_deterministic_build_metadata_tiebreaker(registry: ToolRegistry) -> None:
    first = make_definition(version="1.0.0+build-a")
    second = make_definition(version="1.0.0+build-z")
    registry.register(second)
    registry.register(first)

    assert registry.latest(first.identity).version == ToolVersion("1.0.0+build-z")


def test_tool_and_version_lists_are_deterministically_sorted(registry: ToolRegistry) -> None:
    registry.register(make_definition(namespace="zeta", name="lookup"))
    registry.register(make_definition(namespace="alpha", name="search"))
    registry.register(make_definition(namespace="alpha", name="lookup"))

    assert [str(identity) for identity in registry.list_tools()] == [
        "alpha.lookup",
        "alpha.search",
        "zeta.lookup",
    ]


def test_missing_exact_and_latest_lookups_are_structured(registry: ToolRegistry) -> None:
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")
    identity = reference.identity

    assert registry.exists(reference) is False
    assert registry.list_versions(identity) == ()
    with pytest.raises(ToolNotFoundError) as exact_error:
        registry.get(reference)
    with pytest.raises(ToolNotFoundError) as latest_error:
        registry.latest(identity)

    assert exact_error.value.tool == reference
    assert latest_error.value.tool == identity


def test_registration_rejects_invalid_schemas_before_storage(registry: ToolRegistry) -> None:
    definition = make_definition(input_schema={"$ref": "https://example.invalid/schema"})

    with pytest.raises(InvalidSchemaError):
        registry.register(definition)

    assert registry.exists(definition.ref) is False


def test_retrieval_preserves_deep_immutability(registry: ToolRegistry) -> None:
    source_schema: dict[str, object] = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    }
    definition = make_definition(input_schema=source_schema)
    registry.register(definition)

    source_schema["type"] = "array"
    reconstructed = registry.get(definition.ref)

    assert reconstructed == definition
    assert reconstructed.input_schema["type"] == "object"


def test_empty_registry_lists_are_stable(registry: ToolRegistry) -> None:
    identity = ToolIdentity(ToolNamespace("empty"), ToolName("lookup"))

    assert registry.list_tools() == ()
    assert registry.list_versions(identity) == ()
