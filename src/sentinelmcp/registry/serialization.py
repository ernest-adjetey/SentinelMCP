"""Safe reconstruction of immutable definitions from canonical persisted bytes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import cast

from sentinelmcp.domain.errors import RegistryIntegrityError, SentinelMCPError
from sentinelmcp.domain.tools import (
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.registry.fingerprint import canonicalize_tool_definition
from sentinelmcp.registry.schema import validate_tool_definition_schemas

_DEFINITION_KEYS = frozenset(
    {
        "capabilities",
        "description",
        "idempotency_supported",
        "input_schema",
        "name",
        "namespace",
        "operation_class",
        "output_schema",
        "retry_policy",
        "timeout_policy",
        "version",
    }
)


def reconstruct_tool_definition(
    canonical_definition: bytes,
    *,
    stored_fingerprint: str,
    stored_namespace: str,
    stored_name: str,
    stored_version: str,
) -> ToolDefinition:
    """Reconstruct and verify one persisted canonical definition."""

    document = _decode_document(canonical_definition)
    try:
        definition = ToolDefinition(
            namespace=ToolNamespace(_string(document, "namespace")),
            name=ToolName(_string(document, "name")),
            version=ToolVersion(_string(document, "version")),
            description=_string(document, "description"),
            input_schema=_mapping(document, "input_schema"),
            output_schema=_mapping(document, "output_schema"),
            operation_class=OperationClass(_string(document, "operation_class")),
            timeout_policy=TimeoutPolicy(
                timeout_ms=_integer(_mapping(document, "timeout_policy"), "timeout_ms")
            ),
            retry_policy=_retry_policy(document),
            idempotency_supported=_boolean(document, "idempotency_supported"),
            capabilities=frozenset(_string_list(document, "capabilities")),
        )
        validate_tool_definition_schemas(definition)
    except (SentinelMCPError, ValueError) as error:
        raise RegistryIntegrityError(reason="persisted definition is invalid") from error

    persisted_identity = (stored_namespace, stored_name, stored_version)
    reconstructed_identity = (
        str(definition.namespace),
        str(definition.name),
        str(definition.version),
    )
    if reconstructed_identity != persisted_identity:
        raise RegistryIntegrityError(reason="persisted identity columns disagree with content")

    reconstructed_canonical = canonicalize_tool_definition(definition)
    if reconstructed_canonical != canonical_definition:
        raise RegistryIntegrityError(reason="persisted definition is not canonical")
    if sha256(canonical_definition).hexdigest() != stored_fingerprint:
        raise RegistryIntegrityError(reason="persisted fingerprint does not match content")
    return definition


def _decode_document(canonical_definition: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(canonical_definition.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryIntegrityError(
            reason="persisted definition is not valid UTF-8 JSON"
        ) from error
    if not isinstance(decoded, dict):
        raise RegistryIntegrityError(reason="persisted definition must be a JSON object")
    document = cast(dict[str, object], decoded)
    if frozenset(document) != _DEFINITION_KEYS:
        raise RegistryIntegrityError(reason="persisted definition fields are incomplete or unknown")
    return document


def _retry_policy(document: Mapping[str, object]) -> RetryPolicy:
    value = _mapping(document, "retry_policy")
    return RetryPolicy(
        max_attempts=_integer(value, "max_attempts"),
        initial_delay_ms=_integer(value, "initial_delay_ms"),
        maximum_delay_ms=_integer(value, "maximum_delay_ms"),
    )


def _mapping(document: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = document.get(field)
    if not isinstance(value, dict):
        raise RegistryIntegrityError(reason=f"persisted {field} must be an object")
    return cast(dict[str, object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str):
        raise RegistryIntegrityError(reason=f"persisted {field} must be a string")
    return value


def _integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryIntegrityError(reason=f"persisted {field} must be an integer")
    return value


def _boolean(document: Mapping[str, object], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise RegistryIntegrityError(reason=f"persisted {field} must be a boolean")
    return value


def _string_list(document: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = document.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RegistryIntegrityError(reason=f"persisted {field} must be a string array")
    return tuple(cast(list[str], value))
