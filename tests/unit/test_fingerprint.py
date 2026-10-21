"""Tests for canonical tool-definition fingerprints."""

from __future__ import annotations

import hashlib
import re

import pytest

from sentinelmcp.domain import (
    InvalidToolDefinitionError,
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.registry import canonicalize_tool_definition, fingerprint_tool_definition


def make_definition(
    *,
    description: str = "Look up one catalog asset.",
    input_schema: dict[str, object] | None = None,
    capabilities: frozenset[str] = frozenset({"lineage", "ownership"}),
    timeout_policy: TimeoutPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description=description,
        input_schema=input_schema or {"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
        timeout_policy=timeout_policy or TimeoutPolicy(),
        retry_policy=RetryPolicy(),
        capabilities=capabilities,
    )


def test_fingerprint_is_sha256_of_rfc8785_canonical_content() -> None:
    definition = make_definition()

    canonical = canonicalize_tool_definition(definition)
    fingerprint = fingerprint_tool_definition(definition)

    assert fingerprint == hashlib.sha256(canonical).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    assert canonical.startswith(b'{"capabilities"')


def test_fingerprint_has_stable_cross_release_vector() -> None:
    assert fingerprint_tool_definition(make_definition()) == (
        "e8e96d54cd5dfb28da43da301d02b155918ed9b38e6eecce6feba2166b0c44aa"
    )


def test_fingerprint_ignores_object_key_and_capability_order() -> None:
    first = make_definition(
        input_schema={
            "type": "object",
            "properties": {"name": {"minLength": 1, "type": "string"}},
        },
        capabilities=frozenset({"ownership", "lineage"}),
    )
    second = make_definition(
        input_schema={
            "properties": {"name": {"type": "string", "minLength": 1}},
            "type": "object",
        },
        capabilities=frozenset({"lineage", "ownership"}),
    )

    assert canonicalize_tool_definition(first) == canonicalize_tool_definition(second)
    assert fingerprint_tool_definition(first) == fingerprint_tool_definition(second)


def test_fingerprint_uses_json_number_canonicalization() -> None:
    integer_schema = make_definition(input_schema={"type": "number", "minimum": 1})
    float_schema = make_definition(input_schema={"minimum": 1.0, "type": "number"})

    assert fingerprint_tool_definition(integer_schema) == fingerprint_tool_definition(float_schema)


@pytest.mark.parametrize(
    ("changed", "unchanged"),
    [
        (make_definition(description="A different contract."), make_definition()),
        (
            make_definition(input_schema={"type": "object", "required": ["asset_id"]}),
            make_definition(),
        ),
        (make_definition(timeout_policy=TimeoutPolicy(timeout_ms=100)), make_definition()),
    ],
)
def test_semantic_content_changes_fingerprint(
    changed: ToolDefinition, unchanged: ToolDefinition
) -> None:
    assert fingerprint_tool_definition(changed) != fingerprint_tool_definition(unchanged)


def test_fingerprint_is_deterministic_across_repeated_calls() -> None:
    definition = make_definition()

    assert {fingerprint_tool_definition(definition) for _ in range(20)} == {
        fingerprint_tool_definition(definition)
    }


def test_fingerprint_rejects_values_outside_rfc8785_number_domain() -> None:
    definition = make_definition(input_schema={"const": 2**53})

    with pytest.raises(InvalidToolDefinitionError) as captured:
        fingerprint_tool_definition(definition)

    assert captured.value.field == "canonical_content"
    assert "RFC 8785" in captured.value.reason
