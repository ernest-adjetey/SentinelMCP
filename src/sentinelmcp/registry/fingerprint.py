"""Canonical fingerprints for immutable tool definitions."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, cast

import rfc8785

from sentinelmcp.domain.errors import InvalidToolDefinitionError
from sentinelmcp.domain.json_values import FrozenJSONValue, thaw_json
from sentinelmcp.domain.tools import ToolDefinition


def canonicalize_tool_definition(definition: ToolDefinition) -> bytes:
    """Serialize semantic definition content with RFC 8785 JSON canonicalization."""

    document: dict[str, object] = {
        "capabilities": sorted(definition.capabilities),
        "description": definition.description,
        "idempotency_supported": definition.idempotency_supported,
        "input_schema": _thaw_schema(definition.input_schema),
        "name": str(definition.name),
        "namespace": str(definition.namespace),
        "operation_class": definition.operation_class.value,
        "output_schema": _thaw_schema(definition.output_schema),
        "retry_policy": {
            "initial_delay_ms": definition.retry_policy.initial_delay_ms,
            "max_attempts": definition.retry_policy.max_attempts,
            "maximum_delay_ms": definition.retry_policy.maximum_delay_ms,
        },
        "timeout_policy": {"timeout_ms": definition.timeout_policy.timeout_ms},
        "version": str(definition.version),
    }
    try:
        return rfc8785.dumps(cast(Any, document))
    except rfc8785.CanonicalizationError as error:
        raise InvalidToolDefinitionError(
            field="canonical_content",
            reason="must be representable by the RFC 8785 JSON Canonicalization Scheme",
        ) from error


def fingerprint_tool_definition(definition: ToolDefinition) -> str:
    """Return the lowercase hexadecimal SHA-256 digest of canonical semantic content."""

    return sha256(canonicalize_tool_definition(definition)).hexdigest()


def _thaw_schema(schema: object) -> object:
    return thaw_json(cast(FrozenJSONValue, schema))
