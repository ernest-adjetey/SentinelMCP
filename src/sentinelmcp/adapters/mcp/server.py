"""Official MCP SDK adapter over SentinelMCP's shared application lifecycle."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast, final

from mcp.server import Server, ServerRequestContext
from mcp.server.auth.settings import AuthSettings
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from starlette.applications import Starlette

from mcp import MCPError
from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import OperationClass, ToolDefinition, ToolNotFoundError, ToolRef
from sentinelmcp.domain.json_values import FrozenJSONValue, thaw_json
from sentinelmcp.execution import (
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    InvalidExecutionRequestError,
)
from sentinelmcp.mutation_guard import MutationPreview
from sentinelmcp.policy import AuthenticationError, Authenticator, Principal
from sentinelmcp.registry import ToolRegistry

from .names import MCPToolNameError, decode_tool_name, encode_tool_ref
from .principal import (
    AccessTokenMCPPrincipalResolver,
    AuthenticatorTokenVerifier,
    DenyAllMCPPrincipalResolver,
    MCPPrincipalResolver,
)

_SERVER_NAME = "sentinelmcp"
_SERVER_VERSION = "0.1.1"
_AUTHENTICATION_PROTOCOL_ERROR = -32001


@final
class _MCPToolAdapter:
    __slots__ = ("_executor", "_mutation_service", "_principal_resolver", "_registry")

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        principal_resolver: MCPPrincipalResolver,
        mutation_service: MutationService | None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._principal_resolver = principal_resolver
        self._mutation_service = mutation_service

    async def list_tools(
        self,
        context: ServerRequestContext[Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        del context, params
        try:
            self._principal_resolver.resolve()
        except AuthenticationError as error:
            raise MCPError(
                _AUTHENTICATION_PROTOCOL_ERROR,
                "A valid SentinelMCP principal is required.",
            ) from error
        try:
            definitions = _registered_definitions(self._registry)
            return ListToolsResult(tools=[_mcp_tool(definition) for definition in definitions])
        except MCPToolNameError as error:
            raise MCPError(
                -32603,
                "A registered tool cannot be represented safely by MCP.",
            ) from error
        except Exception as error:
            raise MCPError(-32603, "SentinelMCP tool discovery failed.") from error

    async def call_tool(
        self,
        context: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        del context
        try:
            principal = self._principal_resolver.resolve()
        except AuthenticationError:
            return _boundary_failure(
                ExecutionErrorCode.AUTHENTICATION_FAILED,
                "A valid SentinelMCP principal is required.",
            )
        try:
            tool_ref = decode_tool_name(params.name)
        except MCPToolNameError:
            return _boundary_failure(
                ExecutionErrorCode.INVALID_REQUEST,
                "The MCP tool name is invalid.",
            )
        try:
            definition = self._registry.get(tool_ref)
        except ToolNotFoundError:
            return _boundary_failure(
                ExecutionErrorCode.TOOL_NOT_FOUND,
                "The requested exact tool version is not registered.",
                tool_ref=tool_ref,
            )
        except Exception:
            return _boundary_failure(
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
                "SentinelMCP could not resolve the tool contract.",
                tool_ref=tool_ref,
            )
        arguments = {} if params.arguments is None else params.arguments
        if definition.operation_class is OperationClass.MUTATION:
            return await self._call_mutation(definition, arguments, principal)
        return await self._call_read(definition.ref, arguments, principal)

    async def _call_read(
        self,
        tool_ref: ToolRef,
        arguments: Mapping[str, object],
        principal: Principal,
    ) -> CallToolResult:
        try:
            request = ExecutionRequest(tool_ref, arguments)
        except InvalidExecutionRequestError:
            return _boundary_failure(
                ExecutionErrorCode.INVALID_REQUEST,
                "Tool arguments must be bounded JSON data.",
                tool_ref=tool_ref,
            )
        result = await self._executor.execute(request, principal=principal)
        return _execution_result(result)

    async def _call_mutation(
        self,
        definition: ToolDefinition,
        payload: Mapping[str, object],
        principal: Principal,
    ) -> CallToolResult:
        if self._mutation_service is None:
            return _boundary_failure(
                ExecutionErrorCode.MUTATION_NOT_SUPPORTED,
                "Durable mutation confirmation is not configured.",
                tool_ref=definition.ref,
            )
        action = payload.get("action")
        arguments = payload.get("arguments")
        if not isinstance(action, str) or not isinstance(arguments, Mapping):
            return _boundary_failure(
                ExecutionErrorCode.INVALID_REQUEST,
                "Mutation calls require an action and argument object.",
                tool_ref=definition.ref,
            )
        try:
            request = ExecutionRequest(definition.ref, arguments)
        except InvalidExecutionRequestError:
            return _boundary_failure(
                ExecutionErrorCode.INVALID_REQUEST,
                "Mutation arguments must be bounded JSON data.",
                tool_ref=definition.ref,
            )
        if action == "preview" and set(payload) == {"action", "arguments"}:
            result = self._mutation_service.preview(request, principal=principal)
            if isinstance(result, MutationPreview):
                return _preview_result(result)
            return _execution_result(result)
        if action == "confirm" and set(payload) == {
            "action",
            "arguments",
            "confirmation_token",
            "idempotency_key",
        }:
            confirmation_token = payload.get("confirmation_token")
            idempotency_key = payload.get("idempotency_key")
            if not isinstance(confirmation_token, str) or not isinstance(idempotency_key, str):
                return _boundary_failure(
                    ExecutionErrorCode.INVALID_REQUEST,
                    "Mutation confirmation fields must be strings.",
                    tool_ref=definition.ref,
                )
            confirmed = await self._mutation_service.confirm(
                request,
                principal=principal,
                confirmation_token=confirmation_token,
                idempotency_key=idempotency_key,
            )
            return _execution_result(confirmed)
        return _boundary_failure(
            ExecutionErrorCode.INVALID_REQUEST,
            "Mutation action or fields are invalid.",
            tool_ref=definition.ref,
        )


def create_mcp_server(
    registry: ToolRegistry,
    executor: ToolExecutor,
    *,
    principal_resolver: MCPPrincipalResolver | None = None,
    mutation_service: MutationService | None = None,
) -> Server[Any]:
    """Compose an MCP server over the same registry and application services as REST."""

    resolver = DenyAllMCPPrincipalResolver() if principal_resolver is None else principal_resolver
    adapter = _MCPToolAdapter(registry, executor, resolver, mutation_service)
    return Server(
        _SERVER_NAME,
        version=_SERVER_VERSION,
        title="SentinelMCP",
        description=(
            "Reliability, safety, security, and observability control plane for exact-versioned "
            "agent tools."
        ),
        instructions=(
            "Call exact tool versions returned by tools/list. Mutation tools require a preview "
            "action followed by a confirm action using the returned single-use challenge."
        ),
        get_tool_input_schema=lambda name: _input_schema_for_name(registry, name),
        on_list_tools=adapter.list_tools,
        on_call_tool=adapter.call_tool,
    )


def create_authenticated_streamable_http_app(
    registry: ToolRegistry,
    executor: ToolExecutor,
    authenticator: Authenticator,
    *,
    auth_settings: AuthSettings,
    mutation_service: MutationService | None = None,
    token_resource: str | None = None,
    path: str = "/mcp",
) -> Starlette:
    """Compose stateless Streamable HTTP with official auth middleware and Sentinel JWTs."""

    server = create_mcp_server(
        registry,
        executor,
        principal_resolver=AccessTokenMCPPrincipalResolver(),
        mutation_service=mutation_service,
    )
    return server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
        auth=auth_settings,
        token_verifier=AuthenticatorTokenVerifier(authenticator, resource=token_resource),
    )


async def run_stdio_server(server: Server[Any]) -> None:
    """Run an already-composed server over stdio; its principal resolver remains authoritative."""

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _registered_definitions(registry: ToolRegistry) -> tuple[ToolDefinition, ...]:
    return tuple(
        registry.get(ToolRef(identity.namespace, identity.name, version))
        for identity in registry.list_tools()
        for version in registry.list_versions(identity)
    )


def _mcp_tool(definition: ToolDefinition) -> Tool:
    is_read = definition.operation_class is OperationClass.READ
    return Tool(
        name=encode_tool_ref(definition.ref),
        title=str(definition.ref),
        description=definition.description,
        input_schema=_input_schema(definition),
        output_schema=_json_mapping(definition.output_schema) if is_read else None,
        annotations=ToolAnnotations(
            read_only_hint=is_read,
            destructive_hint=not is_read,
            idempotent_hint=is_read or definition.idempotency_supported,
            open_world_hint=True,
        ),
        _meta={
            "io.sentinelmcp/tool-ref": str(definition.ref),
            "io.sentinelmcp/operation-class": definition.operation_class.value,
        },
    )


def _input_schema_for_name(registry: ToolRegistry, name: str) -> dict[str, object] | None:
    try:
        return _input_schema(registry.get(decode_tool_name(name)))
    except (MCPToolNameError, ToolNotFoundError):
        return None


def _input_schema(definition: ToolDefinition) -> dict[str, object]:
    registered = _json_mapping(definition.input_schema)
    if definition.operation_class is OperationClass.READ:
        return registered
    return {
        "type": "object",
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "preview"},
                    "arguments": registered,
                },
                "required": ["action", "arguments"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "confirm"},
                    "arguments": registered,
                    "confirmation_token": {"type": "string", "minLength": 1},
                    "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 255},
                },
                "required": [
                    "action",
                    "arguments",
                    "confirmation_token",
                    "idempotency_key",
                ],
                "additionalProperties": False,
            },
        ],
    }


def _json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    thawed = thaw_json(cast(FrozenJSONValue, value))
    if not isinstance(thawed, dict):
        raise TypeError("registered schema must be a JSON object")
    return cast(dict[str, object], thawed)


def _preview_result(preview: MutationPreview) -> CallToolResult:
    payload: dict[str, object] = {
        "status": "confirmation_required",
        "request_id": str(preview.request_id),
        "tool": str(preview.tool_ref),
        "preview": {
            "challenge_id": str(preview.challenge_id),
            "confirmation_token": preview.confirmation_token,
            "definition_fingerprint": preview.definition_fingerprint,
            "expires_at": preview.expires_at.isoformat(),
            "warnings": list(preview.warnings),
        },
    }
    return _call_result(payload, is_error=False, tool_ref=preview.tool_ref)


def _execution_result(result: ExecutionSuccess | ExecutionFailure) -> CallToolResult:
    if isinstance(result, ExecutionSuccess):
        payload = thaw_json(result.result)
        return _call_result(
            payload,
            is_error=False,
            tool_ref=result.context.tool_ref,
            metadata={
                "request_id": str(result.context.request_id),
                "execution_id": str(result.context.execution_id),
                "duration_ms": round(result.duration_ms, 3),
            },
        )
    payload = _execution_failure_payload(result)
    return _call_result(payload, is_error=True, tool_ref=result.context.tool_ref)


def _execution_failure_payload(result: ExecutionFailure) -> dict[str, object]:
    return {
        "status": "error",
        "request_id": str(result.context.request_id),
        "execution_id": str(result.context.execution_id),
        "tool": str(result.context.tool_ref),
        "error": _error_payload(result.error),
        "duration_ms": round(result.duration_ms, 3),
    }


def _error_payload(error: ExecutionError) -> dict[str, object]:
    payload: dict[str, object] = {"code": error.code.value, "message": error.message}
    if error.upstream_status is not None:
        payload["upstream_status"] = error.upstream_status
    if error.retry_after_ms is not None:
        payload["retry_after_ms"] = error.retry_after_ms
    if error.issues:
        payload["issues"] = [
            {
                "instance_path": list(issue.instance_path),
                "schema_path": list(issue.schema_path),
                "message": issue.message,
                "validator": issue.validator,
            }
            for issue in error.issues
        ]
    return payload


def _boundary_failure(
    code: ExecutionErrorCode,
    message: str,
    *,
    tool_ref: ToolRef | None = None,
) -> CallToolResult:
    payload: dict[str, object] = {
        "status": "error",
        "tool": str(tool_ref) if tool_ref is not None else None,
        "error": {"code": code.value, "message": message},
    }
    return _call_result(payload, is_error=True, tool_ref=tool_ref)


def _call_result(
    payload: object,
    *,
    is_error: bool,
    tool_ref: ToolRef | None,
    metadata: Mapping[str, object] | None = None,
) -> CallToolResult:
    result_meta = dict(metadata or {})
    if tool_ref is not None:
        result_meta["tool_ref"] = str(tool_ref)
    return CallToolResult(
        content=[
            TextContent(
                text=json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            )
        ],
        structured_content=payload,
        is_error=is_error,
        _meta={"io.sentinelmcp/execution": result_meta},
    )
