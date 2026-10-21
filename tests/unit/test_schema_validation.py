"""Tests for Draft 2020-12 registration and runtime validation."""

from __future__ import annotations

import socket

import pytest

from sentinelmcp.domain import (
    ArgumentValidationError,
    InvalidSchemaError,
    OperationClass,
    OutputValidationError,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.registry import DRAFT_2020_12_URI, validate_tool_definition_schemas


def make_definition(
    *,
    input_schema: dict[str, object] | None = None,
    output_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description="Look up one catalog asset.",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {"type": "object"},
        operation_class=OperationClass.READ,
    )


def test_valid_draft_2020_12_contract_accepts_valid_arguments_and_output() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(
            input_schema={
                "$schema": DRAFT_2020_12_URI,
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"found": {"type": "boolean"}},
                "required": ["found"],
            },
        )
    )

    validator.validate_arguments({"asset_id": "asset-1"})
    validator.validate_output({"found": True})


def test_runtime_argument_error_has_stable_paths_and_keyword_without_raw_value() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(
            input_schema={
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
            }
        )
    )
    secret_like_value = "private-" + "x" * 2_000

    with pytest.raises(ArgumentValidationError) as captured:
        validator.validate_arguments({"asset_id": secret_like_value.encode()})

    issue = captured.value.issues[0]
    assert issue.instance_path == ()
    assert issue.validator == "json"
    assert secret_like_value not in issue.message


def test_runtime_schema_failures_are_structured_and_sorted() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(
            input_schema={
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["asset_id", "limit"],
            }
        )
    )

    with pytest.raises(ArgumentValidationError) as captured:
        validator.validate_arguments({"asset_id": 7, "limit": 0})

    assert [issue.instance_path for issue in captured.value.issues] == [
        ("asset_id",),
        ("limit",),
    ]
    assert {issue.validator for issue in captured.value.issues} == {"minimum", "type"}
    assert all("7" not in issue.message for issue in captured.value.issues)


def test_runtime_output_failure_uses_output_error() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(output_schema={"type": "array", "items": {"type": "string"}})
    )

    with pytest.raises(OutputValidationError) as captured:
        validator.validate_output(["valid", 7])

    assert captured.value.tool_ref == validator.tool_ref
    assert captured.value.issues[0].instance_path == (1,)
    assert captured.value.issues[0].schema_path == ("items", "type")
    assert captured.value.issues[0].validator == "type"


def test_local_json_pointer_and_anchor_references_are_supported() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(
            input_schema={
                "$defs": {
                    "asset-id": {"type": "string", "minLength": 1},
                    "limit": {"$anchor": "limit", "type": "integer", "minimum": 1},
                },
                "type": "object",
                "properties": {
                    "asset_id": {"$ref": "#/$defs/asset-id"},
                    "limit": {"$ref": "#limit"},
                },
            }
        )
    )

    validator.validate_arguments({"asset_id": "asset-1", "limit": 1})
    with pytest.raises(ArgumentValidationError):
        validator.validate_arguments({"asset_id": "", "limit": 0})


def test_local_reference_to_boolean_subschema_is_supported() -> None:
    validator = validate_tool_definition_schemas(
        make_definition(input_schema={"$defs": {"anything": True}, "$ref": "#/$defs/anything"})
    )

    validator.validate_arguments({"any": ["json", 1]})


def test_local_reference_target_must_be_a_schema() -> None:
    with pytest.raises(InvalidSchemaError) as captured:
        validate_tool_definition_schemas(
            make_definition(input_schema={"description": "not a schema", "$ref": "#/description"})
        )

    assert captured.value.issues[0].message == (
        "local reference target must be a schema object or boolean"
    )


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
@pytest.mark.parametrize(
    "reference",
    ["https://schemas.example/tool.json", "file:///tmp/schema.json", "relative.json"],
)
def test_remote_and_relative_references_are_rejected_without_network(
    monkeypatch: pytest.MonkeyPatch,
    keyword: str,
    reference: str,
) -> None:
    network_attempted = False

    def fail_network(*_args: object, **_kwargs: object) -> None:
        nonlocal network_attempted
        network_attempted = True
        raise AssertionError("schema validation attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    with pytest.raises(InvalidSchemaError) as captured:
        validate_tool_definition_schemas(make_definition(input_schema={keyword: reference}))

    assert captured.value.schema_kind == "input"
    assert captured.value.issues[0].instance_path == (keyword,)
    assert captured.value.issues[0].validator == keyword
    assert network_attempted is False


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/$defs/missing"},
        {"$ref": "#missing-anchor"},
        {"$ref": "#/$defs/bad~2escape", "$defs": {"bad~2escape": {}}},
        {"$ref": "#/allOf/01", "allOf": [{}]},
    ],
)
def test_unresolved_or_invalid_local_references_are_rejected(schema: dict[str, object]) -> None:
    with pytest.raises(InvalidSchemaError) as captured:
        validate_tool_definition_schemas(make_definition(input_schema=schema))

    assert captured.value.issues[0].message.startswith("local reference")


def test_duplicate_anchors_are_rejected() -> None:
    schema: dict[str, object] = {
        "$defs": {
            "first": {"$anchor": "item", "type": "string"},
            "second": {"$anchor": "item", "type": "integer"},
        }
    }

    with pytest.raises(InvalidSchemaError, match="reference or dialect policy"):
        validate_tool_definition_schemas(make_definition(input_schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {
            "$defs": {
                "first": {"$ref": "#/$defs/second"},
                "second": {"$ref": "#/$defs/first"},
            },
            "$ref": "#/$defs/first",
        },
    ],
)
def test_direct_local_reference_cycles_are_rejected(schema: dict[str, object]) -> None:
    with pytest.raises(InvalidSchemaError) as captured:
        validate_tool_definition_schemas(make_definition(input_schema=schema))

    assert captured.value.issues[0].message == "local reference graph contains a direct cycle"


def test_recursive_data_schema_remains_supported() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "child": {"$ref": "#"},
        },
        "required": ["value"],
    }
    validator = validate_tool_definition_schemas(make_definition(input_schema=schema))

    validator.validate_arguments({"value": "root", "child": {"value": "leaf"}})
    with pytest.raises(ArgumentValidationError):
        validator.validate_arguments({"value": "root", "child": {"value": 1}})


@pytest.mark.parametrize(
    "schema",
    [
        {"$schema": "https://json-schema.org/draft/2019-09/schema"},
        {"$defs": {"nested": {"$schema": DRAFT_2020_12_URI}}},
        {"$defs": {"nested": {"$id": "nested.json"}}},
    ],
)
def test_dialect_and_nested_resource_policy_is_enforced(schema: dict[str, object]) -> None:
    with pytest.raises(InvalidSchemaError):
        validate_tool_definition_schemas(make_definition(input_schema=schema))


def test_invalid_schema_is_rejected_by_draft_2020_12_metaschema() -> None:
    with pytest.raises(InvalidSchemaError) as captured:
        validate_tool_definition_schemas(make_definition(output_schema={"type": 7}))

    assert captured.value.schema_kind == "output"
    assert captured.value.reason == "schema does not satisfy the Draft 2020-12 metaschema"
    assert captured.value.issues[0].instance_path == ("type",)
    assert captured.value.issues[0].validator is not None


def test_schema_depth_node_and_size_limits_are_enforced() -> None:
    deep: dict[str, object] = {}
    cursor = deep
    for _ in range(40):
        child: dict[str, object] = {}
        cursor["allOf"] = [child]
        cursor = child

    with pytest.raises(InvalidSchemaError, match="depth limit"):
        validate_tool_definition_schemas(make_definition(input_schema=deep))
    with pytest.raises(InvalidSchemaError, match="node limit"):
        validate_tool_definition_schemas(
            make_definition(input_schema={"enum": list(range(10_001))})
        )
    with pytest.raises(InvalidSchemaError, match="byte limit"):
        validate_tool_definition_schemas(
            make_definition(input_schema={"description": "x" * 1_000_001})
        )


def test_validation_issue_count_is_bounded() -> None:
    properties = {f"field-{index}": {"type": "integer"} for index in range(40)}
    arguments = {name: "invalid" for name in properties}
    validator = validate_tool_definition_schemas(
        make_definition(input_schema={"type": "object", "properties": properties})
    )

    with pytest.raises(ArgumentValidationError) as captured:
        validator.validate_arguments(arguments)

    assert len(captured.value.issues) == 33
    assert captured.value.issues[-1].validator == "maxIssues"


def test_runtime_instance_depth_and_node_limits_are_structured() -> None:
    validator = validate_tool_definition_schemas(make_definition(input_schema={}))
    deep: list[object] = []
    cursor = deep
    for _ in range(130):
        child: list[object] = []
        cursor.append(child)
        cursor = child

    with pytest.raises(ArgumentValidationError) as depth_error:
        validator.validate_arguments(deep)
    with pytest.raises(ArgumentValidationError) as node_error:
        validator.validate_arguments(list(range(100_001)))

    assert depth_error.value.issues[0].validator == "json"
    assert node_error.value.issues[0].validator == "maxNodes"
