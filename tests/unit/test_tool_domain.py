"""Tests for immutable tool-domain contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sentinelmcp.domain import (
    InvalidToolDefinitionError,
    InvalidToolReferenceError,
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)


def make_definition(
    *,
    input_schema: dict[str, object] | None = None,
    output_schema: dict[str, object] | None = None,
    operation_class: OperationClass = OperationClass.READ,
    retry_policy: RetryPolicy | None = None,
    idempotency_supported: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description="Look up one catalog asset.",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {"type": "object"},
        operation_class=operation_class,
        retry_policy=retry_policy or RetryPolicy(),
        idempotency_supported=idempotency_supported,
        capabilities=frozenset({"lineage", "ownership"}),
    )


@pytest.mark.parametrize(
    "value",
    ["catalog", "catalog.asset", "a0.prod-1", "a.b.c"],
)
def test_namespace_accepts_documented_character_set(value: str) -> None:
    assert str(ToolNamespace(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", "Catalog", "catalog_asset", ".catalog", "catalog.", "catalog..asset", "1catalog"],
)
def test_namespace_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(InvalidToolReferenceError) as captured:
        ToolNamespace(value)

    assert captured.value.component in {"namespace", "namespace segment"}
    assert captured.value.code == "invalid_tool_reference"


def test_namespace_rejects_wrong_type_and_operational_bounds() -> None:
    with pytest.raises(InvalidToolReferenceError, match="must be a string"):
        ToolNamespace(7)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="characters"):
        ToolNamespace("a" * 192)
    with pytest.raises(InvalidToolReferenceError, match="segments"):
        ToolNamespace(".".join("a" for _ in range(17)))


@pytest.mark.parametrize("value", ["lookup", "asset-lookup", "a1"])
def test_tool_name_accepts_documented_character_set(value: str) -> None:
    assert str(ToolName(value)) == value


@pytest.mark.parametrize("value", ["", "Lookup", "asset.lookup", "asset_lookup", "-lookup"])
def test_tool_name_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(InvalidToolReferenceError):
        ToolName(value)


def test_tool_name_rejects_wrong_type() -> None:
    with pytest.raises(InvalidToolReferenceError, match="must be a string"):
        ToolName(7)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["0.0.0", "1.2.3", "2.0.0-rc.1", "1.2.3+build.7", "1.2.3-alpha+build.7"],
)
def test_tool_version_accepts_full_semver(value: str) -> None:
    assert str(ToolVersion(value)) == value


@pytest.mark.parametrize("value", ["1", "1.2", "v1.2.3", "1.02.3", "1.2.3 ", ""])
def test_tool_version_rejects_malformed_semver(value: str) -> None:
    with pytest.raises(InvalidToolReferenceError):
        ToolVersion(value)


def test_tool_version_rejects_wrong_type_and_length_bound() -> None:
    with pytest.raises(InvalidToolReferenceError, match="must be a string"):
        ToolVersion(1)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="1-128"):
        ToolVersion("1.0.0+" + "a" * 129)


def test_tool_version_orders_by_semver_precedence() -> None:
    versions = [ToolVersion("2.0.0"), ToolVersion("1.0.0"), ToolVersion("2.0.0-rc.1")]

    assert [str(version) for version in sorted(versions)] == ["1.0.0", "2.0.0-rc.1", "2.0.0"]


def test_tool_version_has_deterministic_build_metadata_tiebreaker() -> None:
    versions = [ToolVersion("1.0.0+z"), ToolVersion("1.0.0+a")]

    assert [str(version) for version in sorted(versions)] == ["1.0.0+a", "1.0.0+z"]


def test_tool_version_comparison_defers_for_unrelated_types() -> None:
    assert ToolVersion.__lt__(ToolVersion("1.0.0"), "1.0.0") is NotImplemented


def test_tool_reference_round_trips_canonical_text() -> None:
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")

    assert str(reference) == "catalog.asset.lookup@1.0.0"
    assert str(reference.identity) == "catalog.asset.lookup"


def test_identity_and_reference_require_typed_components() -> None:
    namespace = ToolNamespace("catalog")
    name = ToolName("lookup")
    version = ToolVersion("1.0.0")

    with pytest.raises(InvalidToolReferenceError, match="ToolNamespace"):
        ToolIdentity("catalog", name)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="ToolName"):
        ToolIdentity(namespace, "lookup")  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="ToolNamespace"):
        ToolRef("catalog", name, version)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="ToolName"):
        ToolRef(namespace, "lookup", version)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolReferenceError, match="ToolVersion"):
        ToolRef(namespace, name, "1.0.0")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["catalog.asset.lookup", "lookup@1.0.0", "catalog.asset.lookup@@1.0.0"],
)
def test_tool_reference_rejects_invalid_shapes(value: str) -> None:
    with pytest.raises(InvalidToolReferenceError):
        ToolRef.parse(value)


def test_tool_reference_rejects_non_string_input() -> None:
    with pytest.raises(InvalidToolReferenceError):
        ToolRef.parse(1)  # type: ignore[arg-type]


def test_definition_is_deeply_immutable_and_detached_from_caller() -> None:
    source_input: dict[str, object] = {
        "type": "object",
        "properties": {"asset_id": {"type": "string", "enum": ["asset-1"]}},
    }
    definition = make_definition(input_schema=source_input)

    properties = source_input["properties"]
    assert isinstance(properties, dict)
    properties["asset_id"] = {"type": "integer"}
    source_input["required"] = ["asset_id"]

    frozen_properties = definition.input_schema["properties"]
    assert frozen_properties["asset_id"]["type"] == "string"  # type: ignore[index]
    assert "required" not in definition.input_schema
    with pytest.raises(TypeError):
        definition.input_schema["type"] = "array"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        definition.description = "Changed"  # type: ignore[misc]
    assert str(definition.ref) == "catalog.asset.lookup@1.0.0"
    assert str(definition.identity) == "catalog.asset.lookup"


def test_definition_rejects_cyclic_or_non_json_schema_data() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(InvalidToolDefinitionError, match="cyclic"):
        make_definition(input_schema=cyclic)
    with pytest.raises(InvalidToolDefinitionError, match="unsupported JSON"):
        make_definition(output_schema={"bad": object()})


def test_policy_metadata_is_bounded_and_frozen() -> None:
    with pytest.raises(InvalidToolDefinitionError):
        TimeoutPolicy(timeout_ms=0)
    with pytest.raises(InvalidToolDefinitionError, match="integer"):
        TimeoutPolicy(timeout_ms=True)
    with pytest.raises(InvalidToolDefinitionError):
        RetryPolicy(max_attempts=11)
    with pytest.raises(InvalidToolDefinitionError):
        RetryPolicy(initial_delay_ms=-1)
    with pytest.raises(InvalidToolDefinitionError):
        RetryPolicy(maximum_delay_ms=300_001)
    with pytest.raises(InvalidToolDefinitionError, match="initial delay"):
        RetryPolicy(initial_delay_ms=2_000, maximum_delay_ms=1_000)
    with pytest.raises(FrozenInstanceError):
        TimeoutPolicy().timeout_ms = 100  # type: ignore[misc]


def test_non_idempotent_mutation_cannot_declare_retries() -> None:
    with pytest.raises(InvalidToolDefinitionError, match="idempotency"):
        make_definition(
            operation_class=OperationClass.MUTATION,
            retry_policy=RetryPolicy(max_attempts=2),
        )

    definition = make_definition(
        operation_class=OperationClass.MUTATION,
        retry_policy=RetryPolicy(max_attempts=2),
        idempotency_supported=True,
    )
    assert definition.retry_policy.max_attempts == 2


def test_capabilities_are_validated_and_frozen() -> None:
    definition = make_definition()

    assert definition.capabilities == frozenset({"lineage", "ownership"})
    with pytest.raises(InvalidToolReferenceError):
        ToolDefinition(
            namespace=ToolNamespace("catalog"),
            name=ToolName("lookup"),
            version=ToolVersion("1.0.0"),
            description="Lookup.",
            input_schema={},
            output_schema={},
            operation_class=OperationClass.READ,
            capabilities=frozenset({"Invalid"}),
        )


def test_definition_rejects_invalid_metadata_types_and_bounds() -> None:
    valid: dict[str, object] = {
        "namespace": ToolNamespace("catalog"),
        "name": ToolName("lookup"),
        "version": ToolVersion("1.0.0"),
        "description": "Lookup.",
        "input_schema": {},
        "output_schema": {},
        "operation_class": OperationClass.READ,
    }
    invalid_fields = {
        "namespace": "catalog",
        "name": "lookup",
        "version": "1.0.0",
        "operation_class": "read",
        "timeout_policy": 10,
        "retry_policy": 1,
        "description": 1,
        "input_schema": [],
        "output_schema": [],
        "idempotency_supported": 1,
    }
    for field_name, invalid_value in invalid_fields.items():
        arguments = valid | {field_name: invalid_value}
        with pytest.raises(InvalidToolDefinitionError) as captured:
            ToolDefinition(**arguments)  # type: ignore[arg-type]
        assert captured.value.field == field_name

    for description in ("", " padded", "padded ", "a" * 1_001):
        with pytest.raises(InvalidToolDefinitionError):
            ToolDefinition(**(valid | {"description": description}))  # type: ignore[arg-type]


def test_definition_rejects_invalid_capability_containers() -> None:
    arguments = {
        "namespace": ToolNamespace("catalog"),
        "name": ToolName("lookup"),
        "version": ToolVersion("1.0.0"),
        "description": "Lookup.",
        "input_schema": {},
        "output_schema": {},
        "operation_class": OperationClass.READ,
    }

    with pytest.raises(InvalidToolDefinitionError, match="collection"):
        ToolDefinition(**arguments, capabilities="lineage")  # type: ignore[arg-type]
    with pytest.raises(InvalidToolDefinitionError, match="iterable"):
        ToolDefinition(**arguments, capabilities=1)  # type: ignore[arg-type]
    with pytest.raises(InvalidToolDefinitionError, match="more than"):
        ToolDefinition(
            **arguments,  # type: ignore[arg-type]
            capabilities=frozenset(f"capability-{index}" for index in range(65)),
        )
