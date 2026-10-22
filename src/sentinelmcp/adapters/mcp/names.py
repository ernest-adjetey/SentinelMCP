"""Reversible MCP names for exact SentinelMCP tool references."""

from __future__ import annotations

import re

from sentinelmcp.domain import InvalidToolReferenceError, ToolRef

MCP_TOOL_NAME_MAX_LENGTH = 128
_PREFIX = "s."
_MCP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class MCPToolNameError(ValueError):
    """Raised when an exact tool reference cannot be represented as an MCP name."""


def encode_tool_ref(tool_ref: ToolRef) -> str:
    """Encode one exact reference without losing prerelease or build metadata."""

    if not isinstance(tool_ref, ToolRef):
        raise MCPToolNameError("tool reference must be a ToolRef")
    encoded_version = str(tool_ref.version).replace("+", "__")
    encoded = f"{_PREFIX}{tool_ref.namespace}.{tool_ref.name}_{encoded_version}"
    if len(encoded) > MCP_TOOL_NAME_MAX_LENGTH:
        raise MCPToolNameError(
            f"encoded MCP tool name exceeds {MCP_TOOL_NAME_MAX_LENGTH} characters"
        )
    if _MCP_NAME_PATTERN.fullmatch(encoded) is None:
        raise MCPToolNameError("encoded MCP tool name contains unsupported characters")
    return encoded


def decode_tool_name(name: str) -> ToolRef:
    """Decode and canonically validate one SentinelMCP-owned MCP tool name."""

    if (
        not isinstance(name, str)
        or len(name) > MCP_TOOL_NAME_MAX_LENGTH
        or _MCP_NAME_PATTERN.fullmatch(name) is None
        or not name.startswith(_PREFIX)
    ):
        raise MCPToolNameError("invalid SentinelMCP tool name")
    qualified_name, separator, encoded_version = name[len(_PREFIX) :].partition("_")
    if not separator or not qualified_name or not encoded_version:
        raise MCPToolNameError("invalid SentinelMCP tool name")
    version = encoded_version.replace("__", "+")
    if "_" in version:
        raise MCPToolNameError("invalid SentinelMCP version escape")
    try:
        tool_ref = ToolRef.parse(f"{qualified_name}@{version}")
    except InvalidToolReferenceError as error:
        raise MCPToolNameError("invalid SentinelMCP tool name") from error
    if encode_tool_ref(tool_ref) != name:
        raise MCPToolNameError("non-canonical SentinelMCP tool name")
    return tool_ref
