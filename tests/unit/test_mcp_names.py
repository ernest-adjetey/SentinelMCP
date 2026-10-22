"""Exact-version MCP tool-name codec tests."""

from __future__ import annotations

import pytest

from sentinelmcp.adapters.mcp import MCPToolNameError, decode_tool_name, encode_tool_ref
from sentinelmcp.domain import ToolName, ToolNamespace, ToolRef, ToolVersion


@pytest.mark.parametrize(
    "reference",
    [
        "demo.math.add@1.0.0",
        "demo.math.add@1.1.0-rc.1",
        "demo.math.add@1.1.0+build.42",
        "demo.math.add@1.1.0-rc.1+sha.abcdef",
    ],
)
def test_exact_tool_reference_round_trips(reference: str) -> None:
    tool_ref = ToolRef.parse(reference)

    encoded = encode_tool_ref(tool_ref)

    assert decode_tool_name(encoded) == tool_ref
    assert set(encoded) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
    assert len(encoded) <= 128


@pytest.mark.parametrize(
    "name",
    [
        "demo.math.add_1.0.0",
        "s.demo.math.add",
        "s.demo.math.add_1.0.0_build",
        "s.demo.math.add_1.0",
        "s.demo.math.add_1.0.0+build",
        "s.demo.math.add_1.0.0_",
    ],
)
def test_noncanonical_or_malformed_names_are_rejected(name: str) -> None:
    with pytest.raises(MCPToolNameError):
        decode_tool_name(name)


def test_reference_that_cannot_fit_mcp_limit_is_rejected() -> None:
    tool_ref = ToolRef(
        ToolNamespace(f"{'a' * 63}.{'b' * 63}"),
        ToolName("run"),
        ToolVersion("1.0.0"),
    )

    with pytest.raises(MCPToolNameError, match="128"):
        encode_tool_ref(tool_ref)


def test_codec_rejects_wrong_input_types() -> None:
    with pytest.raises(MCPToolNameError, match="ToolRef"):
        encode_tool_ref("demo.math.add@1.0.0")  # type: ignore[arg-type]
    with pytest.raises(MCPToolNameError):
        decode_tool_name(42)  # type: ignore[arg-type]
