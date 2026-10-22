"""Official Model Context Protocol transport adapter."""

from sentinelmcp.adapters.mcp.names import (
    MCP_TOOL_NAME_MAX_LENGTH,
    MCPToolNameError,
    decode_tool_name,
    encode_tool_ref,
)
from sentinelmcp.adapters.mcp.principal import (
    AccessTokenMCPPrincipalResolver,
    AuthenticatorTokenVerifier,
    DenyAllMCPPrincipalResolver,
    MCPPrincipalResolver,
    StaticMCPPrincipalResolver,
)
from sentinelmcp.adapters.mcp.server import (
    create_authenticated_streamable_http_app,
    create_mcp_server,
    run_stdio_server,
)

__all__ = [
    "MCP_TOOL_NAME_MAX_LENGTH",
    "AccessTokenMCPPrincipalResolver",
    "AuthenticatorTokenVerifier",
    "DenyAllMCPPrincipalResolver",
    "MCPPrincipalResolver",
    "MCPToolNameError",
    "StaticMCPPrincipalResolver",
    "create_authenticated_streamable_http_app",
    "create_mcp_server",
    "decode_tool_name",
    "encode_tool_ref",
    "run_stdio_server",
]
