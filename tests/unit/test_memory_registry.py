"""Contract tests for the in-memory immutable tool registry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from sentinelmcp.domain import (
    ArgumentValidationError,
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


def test_registry_satisfies_storage_independent_protocol() -> None:
    assert isinstance(InMemoryToolRegistry(), ToolRegistry)


def test_new_exact_version_is_registered_and_retrievable() -> None:
    registry = InMemoryToolRegistry()
    definition = make_definition()

    result = registry.register(definition)

    assert result.status is RegistrationStatus.REGISTERED
    assert result.definition is definition
    assert result.fingerprint == fingerprint_tool_definition(definition)
    assert registry.exists(definition.ref)
    assert registry.get(definition.ref) is definition


def test_identical_exact_reregistration_is_explicit_and_idempotent() -> None:
    registry = InMemoryToolRegistry()
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
    assert second.definition is original
    assert second.fingerprint == first.fingerprint
    assert registry.list_versions(original.identity) == (ToolVersion("1.0.0"),)


def test_conflicting_exact_version_is_rejected_without_overwrite() -> None:
    registry = InMemoryToolRegistry()
    original = make_definition()
    conflicting = make_definition(description="A conflicting immutable definition.")
    registered = registry.register(original)

    with pytest.raises(ImmutableToolVersionConflict) as captured:
        registry.register(conflicting)

    assert captured.value.tool_ref == original.ref
    assert captured.value.existing_fingerprint == registered.fingerprint
    assert captured.value.proposed_fingerprint == fingerprint_tool_definition(conflicting)
    assert registry.get(original.ref) is original


def test_new_versions_are_isolated_and_latest_uses_semver() -> None:
    registry = InMemoryToolRegistry()
    definitions = [
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
    )
    assert registry.latest(identity).version == ToolVersion("2.0.0")
    assert all(registry.get(definition.ref) is definition for definition in definitions)


def test_latest_has_deterministic_build_metadata_tiebreaker() -> None:
    registry = InMemoryToolRegistry()
    first = make_definition(version="1.0.0+build-a")
    second = make_definition(version="1.0.0+build-z")
    registry.register(second)
    registry.register(first)

    assert registry.latest(first.identity).version == ToolVersion("1.0.0+build-z")


def test_tool_and_version_lists_are_deterministically_sorted() -> None:
    registry = InMemoryToolRegistry()
    registry.register(make_definition(namespace="zeta", name="lookup"))
    registry.register(make_definition(namespace="alpha", name="search"))
    registry.register(make_definition(namespace="alpha", name="lookup"))

    assert [str(identity) for identity in registry.list_tools()] == [
        "alpha.lookup",
        "alpha.search",
        "zeta.lookup",
    ]


def test_missing_exact_and_latest_lookups_are_structured() -> None:
    registry = InMemoryToolRegistry()
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")
    identity = reference.identity

    assert registry.exists(reference) is False
    assert registry.list_versions(identity) == ()
    with pytest.raises(ToolNotFoundError) as exact_error:
        registry.get(reference)
    with pytest.raises(ToolNotFoundError) as latest_error:
        registry.latest(identity)
    with pytest.raises(ToolNotFoundError):
        registry.validator_for(reference)

    assert exact_error.value.tool == reference
    assert latest_error.value.tool == identity


def test_registration_rejects_invalid_schemas_before_storage() -> None:
    registry = InMemoryToolRegistry()
    definition = make_definition(input_schema={"$ref": "https://example.invalid/schema"})

    with pytest.raises(InvalidSchemaError):
        registry.register(definition)

    assert registry.exists(definition.ref) is False


def test_registry_reuses_compiled_runtime_validator() -> None:
    registry = InMemoryToolRegistry()
    definition = make_definition(
        input_schema={
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        }
    )
    registry.register(definition)

    validator = registry.validator_for(definition.ref)
    validator.validate_arguments({"asset_id": "asset-1"})
    with pytest.raises(ArgumentValidationError):
        validator.validate_arguments({"asset_id": 1})


def test_concurrent_identical_registration_has_one_insert() -> None:
    registry = InMemoryToolRegistry()
    definition = make_definition()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(registry.register, [definition] * 32))

    assert sum(result.status is RegistrationStatus.REGISTERED for result in results) == 1
    assert sum(result.status is RegistrationStatus.ALREADY_REGISTERED for result in results) == 31
    assert registry.list_tools() == (
        ToolIdentity(namespace=ToolNamespace("catalog.asset"), name=ToolName("lookup")),
    )
