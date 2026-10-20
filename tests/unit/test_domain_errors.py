"""Tests for structured registry-domain failures."""

from sentinelmcp.domain import (
    ArgumentValidationError,
    ImmutableToolVersionConflict,
    InvalidSchemaError,
    OutputValidationError,
    ToolAlreadyExistsError,
    ToolNotFoundError,
    ToolRef,
    ValidationIssue,
)


def test_schema_error_preserves_bounded_structured_details() -> None:
    issue = ValidationIssue(
        instance_path=("properties", "asset_id"),
        schema_path=("properties",),
        message="not valid",
        validator="type",
    )

    error = InvalidSchemaError(schema_kind="input", reason="metaschema rejection", issues=(issue,))

    assert error.code == "invalid_schema"
    assert error.schema_kind == "input"
    assert error.reason == "metaschema rejection"
    assert error.issues == (issue,)
    assert str(error) == "Invalid input schema: metaschema rejection"


def test_lookup_and_conflict_errors_expose_typed_context() -> None:
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")

    missing = ToolNotFoundError(reference)
    duplicate = ToolAlreadyExistsError(reference)
    conflict = ImmutableToolVersionConflict(
        tool_ref=reference,
        existing_fingerprint="a" * 64,
        proposed_fingerprint="b" * 64,
    )

    assert missing.tool == reference
    assert duplicate.tool_ref == reference
    assert conflict.tool_ref == reference
    assert conflict.existing_fingerprint == "a" * 64
    assert conflict.proposed_fingerprint == "b" * 64
    assert conflict.code == "immutable_tool_version_conflict"


def test_runtime_validation_errors_share_structured_issues() -> None:
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")
    issue = ValidationIssue(
        instance_path=("asset_id",),
        schema_path=("properties", "asset_id", "type"),
        message="must be a string",
        validator="type",
    )

    argument_error = ArgumentValidationError(tool_ref=reference, issues=(issue,))
    output_error = OutputValidationError(tool_ref=reference, issues=(issue,))

    assert argument_error.code == "argument_validation_failed"
    assert output_error.code == "output_validation_failed"
    assert argument_error.tool_ref == output_error.tool_ref == reference
    assert argument_error.issues == output_error.issues == (issue,)
    assert "1 issue(s)" in str(argument_error)
