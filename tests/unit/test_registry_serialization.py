"""Unit tests for defensive reconstruction of persisted tool definitions."""

from __future__ import annotations

import json
from hashlib import sha256

import pytest

from sentinelmcp.domain import OperationClass, RegistryIntegrityError, ToolDefinition
from sentinelmcp.domain.tools import ToolName, ToolNamespace, ToolVersion
from sentinelmcp.registry import canonicalize_tool_definition
from sentinelmcp.registry.serialization import reconstruct_tool_definition


def make_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0+build.7"),
        description="Look up an asset.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
        capabilities=frozenset({"catalog"}),
    )


def reconstruct(canonical: bytes, fingerprint: str | None = None) -> ToolDefinition:
    definition = make_definition()
    return reconstruct_tool_definition(
        canonical,
        stored_fingerprint=fingerprint or sha256(canonical).hexdigest(),
        stored_namespace=str(definition.namespace),
        stored_name=str(definition.name),
        stored_version=str(definition.version),
    )


def test_canonical_definition_round_trips_exactly() -> None:
    definition = make_definition()
    canonical = canonicalize_tool_definition(definition)

    assert reconstruct(canonical) == definition


@pytest.mark.parametrize(
    ("canonical", "reason"),
    [
        (b"\xff", "UTF-8 JSON"),
        (b"{", "UTF-8 JSON"),
        (b"[]", "JSON object"),
        (b'{"namespace":"catalog.asset"}', "fields are incomplete"),
    ],
)
def test_malformed_persisted_content_is_rejected(canonical: bytes, reason: str) -> None:
    with pytest.raises(RegistryIntegrityError, match=reason):
        reconstruct(canonical)


def test_noncanonical_content_is_rejected() -> None:
    definition = make_definition()
    document = json.loads(canonicalize_tool_definition(definition))
    noncanonical = json.dumps(document, indent=2).encode()

    with pytest.raises(RegistryIntegrityError, match="not canonical"):
        reconstruct(noncanonical)


def test_fingerprint_mismatch_is_rejected() -> None:
    canonical = canonicalize_tool_definition(make_definition())

    with pytest.raises(RegistryIntegrityError, match="fingerprint"):
        reconstruct(canonical, fingerprint="0" * 64)


def test_identity_column_mismatch_is_rejected() -> None:
    definition = make_definition()
    canonical = canonicalize_tool_definition(definition)

    with pytest.raises(RegistryIntegrityError, match="identity columns"):
        reconstruct_tool_definition(
            canonical,
            stored_fingerprint=sha256(canonical).hexdigest(),
            stored_namespace="wrong.namespace",
            stored_name=str(definition.name),
            stored_version=str(definition.version),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("namespace", 7),
        ("description", False),
        ("input_schema", []),
        ("idempotency_supported", "false"),
        ("capabilities", [1]),
    ],
)
def test_invalid_persisted_field_types_are_rejected(field: str, value: object) -> None:
    document = json.loads(canonicalize_tool_definition(make_definition()))
    document[field] = value
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(RegistryIntegrityError):
        reconstruct(canonical)
