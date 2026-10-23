"""Fail-loudly smoke test for a clean SentinelMCP wheel installation."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

import sentinelmcp
from mcp import Client
from sentinelmcp.adapters.mcp import StaticMCPPrincipalResolver, create_mcp_server
from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.application import create_demo_runtime
from sentinelmcp.domain import ToolRef
from sentinelmcp.domain.json_values import thaw_json
from sentinelmcp.execution import ExecutionRequest, ExecutionSuccess
from sentinelmcp.policy import Principal

EXPECTED_VERSION = "0.1.1"
EXPECTED_PROTOCOL = "2026-07-28"
EXPECTED_TOOL_NAMES = [
    "s.demo.catalog.lookup_1.0.0",
    "s.demo.math.add_1.0.0",
    "s.demo.profile.get_1.0.0",
]
PRINCIPAL = Principal("wheel-smoke", "urn:sentinelmcp:wheel-smoke")


class _SmokeAuthenticator:
    def authenticate(self, token: str) -> Principal:
        if token != "wheel-smoke-token":
            raise AssertionError("REST smoke received an unexpected bearer token")
        return PRINCIPAL


def _assert_installed_import() -> Path:
    module_path = Path(sentinelmcp.__file__).resolve()
    checkout_source = Path(__file__).resolve().parents[1] / "src"
    if sentinelmcp.__version__ != EXPECTED_VERSION:
        raise AssertionError(f"unexpected package version: {sentinelmcp.__version__}")
    if importlib.metadata.version("sentinelmcp") != EXPECTED_VERSION:
        raise AssertionError("distribution metadata and package version differ")
    if "site-packages" not in module_path.parts:
        raise AssertionError(f"package was not imported from site-packages: {module_path}")
    if checkout_source.resolve() in module_path.parents:
        raise AssertionError(f"package fell back to the source checkout: {module_path}")
    return module_path


def _assert_console_entry_point() -> list[dict[str, object]]:
    executable = Path(sys.executable).with_name("sentinel.exe" if os.name == "nt" else "sentinel")
    completed = subprocess.run(
        [str(executable), "tools", "list"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    document = json.loads(completed.stdout)
    expected: list[dict[str, object]] = [
        {"identity": "demo.catalog.lookup", "versions": ["1.0.0"]},
        {"identity": "demo.math.add", "versions": ["1.0.0"]},
        {"identity": "demo.profile.get", "versions": ["1.0.0"]},
    ]
    if document != {"status": "ok", "tools": expected}:
        raise AssertionError(f"unexpected sentinel tools output: {document}")
    return expected


async def _exercise_installed_runtime() -> dict[str, object]:
    runtime = create_demo_runtime()
    core_result = await runtime.executor.execute(
        ExecutionRequest(ToolRef.parse("demo.math.add@1.0.0"), {"a": 2, "b": 3}),
        principal=PRINCIPAL,
    )
    if not isinstance(core_result, ExecutionSuccess):
        raise AssertionError(f"core execution failed: {core_result}")
    if thaw_json(core_result.result) != {"sum": 5}:
        raise AssertionError(f"unexpected core result: {core_result.result}")

    app = create_execution_app(runtime.executor, _SmokeAuthenticator())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wheel-smoke") as client:
        response = await client.post(
            "/v1/execute",
            json={
                "tool": {"namespace": "demo.math", "name": "add", "version": "1.0.0"},
                "arguments": {"a": 2, "b": 3},
            },
            headers={"Authorization": "Bearer wheel-smoke-token"},
        )
    if response.status_code != 200 or response.json().get("result") != {"sum": 5}:
        raise AssertionError(f"unexpected REST response: {response.status_code} {response.text}")

    server = create_mcp_server(
        runtime.registry,
        runtime.executor,
        principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
    )
    async with Client(server) as client:
        if client.protocol_version != EXPECTED_PROTOCOL:
            raise AssertionError(f"unexpected MCP protocol: {client.protocol_version}")
        if client.server_info is None:
            raise AssertionError("MCP negotiation omitted server information")
        if (client.server_info.name, client.server_info.version) != (
            "sentinelmcp",
            EXPECTED_VERSION,
        ):
            raise AssertionError(f"unexpected MCP server information: {client.server_info}")
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        if names != EXPECTED_TOOL_NAMES:
            raise AssertionError(f"unexpected MCP tools: {names}")
        called = await client.call_tool("s.demo.math.add_1.0.0", {"a": 2, "b": 3})
        if called.is_error or called.structured_content != {"sum": 5}:
            raise AssertionError(f"unexpected MCP invocation result: {called}")

    return {
        "core_result": {"sum": 5},
        "mcp_protocol": EXPECTED_PROTOCOL,
        "mcp_result": called.structured_content,
        "mcp_tools": EXPECTED_TOOL_NAMES,
        "rest_result": response.json()["result"],
        "rest_status": response.status_code,
    }


def main() -> None:
    module_path = _assert_installed_import()
    console_tools = _assert_console_entry_point()
    result = asyncio.run(_exercise_installed_runtime())
    print(
        json.dumps(
            {
                "console_tools": console_tools,
                "module_path": str(module_path),
                "package_version": EXPECTED_VERSION,
                **result,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
