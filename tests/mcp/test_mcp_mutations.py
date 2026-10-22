"""Official MCP mutation workflow against durable PostgreSQL state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from mcp.server import Server
from mcp.types import CallToolResult
from sqlalchemy.engine import Engine

from mcp import Client
from sentinelmcp.adapters.mcp import (
    StaticMCPPrincipalResolver,
    create_mcp_server,
    encode_tool_ref,
)
from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
    SyntheticToolAdapter,
)
from sentinelmcp.mutation_guard import PostgresMutationStateStore
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

pytestmark = pytest.mark.postgres

PRINCIPAL_A = Principal(
    "mcp-user-a",
    "https://issuer.test",
    scopes=frozenset({"tools:write"}),
)
PRINCIPAL_B = Principal(
    "mcp-user-b",
    "https://issuer.test",
    scopes=frozenset({"tools:write"}),
)


class FailingResultAudit:
    def record_preview(self, **fields: object) -> None:
        del fields

    def record_intent(self, **fields: object) -> None:
        del fields

    def record_result(self, **fields: object) -> None:
        del fields
        raise RuntimeError("private audit failure")


@dataclass(frozen=True, slots=True)
class MutationRuntime:
    definition: ToolDefinition
    registry: InMemoryToolRegistry
    executor: ToolExecutor
    service: MutationService
    calls: list[FrozenJSONMapping]


def mutation_definition(version: str = "1.0.0") -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.profile"),
        name=ToolName("update"),
        version=ToolVersion(version),
        description="Update one synthetic profile through durable confirmation.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"updated_name": {"type": "string"}},
            "required": ["updated_name"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.MUTATION,
        idempotency_supported=True,
    )


def mutation_runtime(
    engine: Engine,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    audit: FailingResultAudit | None = None,
    required_scope: str = "tools:write",
) -> MutationRuntime:
    item = mutation_definition()
    registry = InMemoryToolRegistry()
    registry.register(item)
    calls: list[FrozenJSONMapping] = []

    async def update(arguments: FrozenJSONMapping) -> object:
        calls.append(arguments)
        return {"updated_name": cast(str, arguments["name"])}

    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(item.ref, SyntheticToolAdapter(update)))
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                item.identity,
                OperationClass.MUTATION,
                required_scopes=frozenset({required_scope}),
            ),
        )
    )
    executor = ToolExecutor(registry, bindings, authorization_policy=policy)
    tokens: Iterator[str] = iter(("a" * 43, "b" * 43, "c" * 43, "d" * 43))
    service = MutationService(
        executor,
        PostgresMutationStateStore(engine),
        wall_clock=clock,
        token_factory=lambda: next(tokens),
        audit=audit,
    )
    return MutationRuntime(item, registry, executor, service, calls)


def server_for(
    runtime: MutationRuntime,
    principal: Principal = PRINCIPAL_A,
) -> Server[Any]:
    return create_mcp_server(
        runtime.registry,
        runtime.executor,
        principal_resolver=StaticMCPPrincipalResolver(principal),
        mutation_service=runtime.service,
    )


def preview_arguments(name: str = "Ada") -> dict[str, object]:
    return {"action": "preview", "arguments": {"name": name}}


def confirm_arguments(
    preview: dict[str, object],
    *,
    name: str = "Ada",
    idempotency_key: str = "profile-update-1",
) -> dict[str, object]:
    challenge = cast(dict[str, object], preview["preview"])
    return {
        "action": "confirm",
        "arguments": {"name": name},
        "confirmation_token": challenge["confirmation_token"],
        "idempotency_key": idempotency_key,
    }


def error_code(result: CallToolResult) -> str:
    structured = cast(dict[str, object], result.structured_content)
    return cast(str, cast(dict[str, object], structured["error"])["code"])


def test_preview_and_idempotent_confirm_use_durable_shared_workflow(
    postgres_engine: Engine,
) -> None:
    runtime = mutation_runtime(postgres_engine)
    tool_name = encode_tool_ref(runtime.definition.ref)

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            listed = await client.list_tools()
            assert listed.tools[0].output_schema is None
            assert "oneOf" in listed.tools[0].input_schema
            preview_result = await client.call_tool(tool_name, preview_arguments())
            preview = cast(dict[str, object], preview_result.structured_content)
            assert preview["status"] == "confirmation_required"
            assert runtime.calls == []

            confirmed = await client.call_tool(tool_name, confirm_arguments(preview))
            reused = await client.call_tool(tool_name, confirm_arguments(preview))
            consumed = await client.call_tool(
                tool_name,
                confirm_arguments(preview, idempotency_key="different-key"),
            )
            assert confirmed.structured_content == {"updated_name": "Ada"}
            assert reused.structured_content == {"updated_name": "Ada"}
            assert error_code(consumed) == "mutation_confirmation_invalid"

    asyncio.run(exercise())
    assert len(runtime.calls) == 1


@pytest.mark.parametrize(
    ("principal", "name"),
    [(PRINCIPAL_B, "Ada"), (PRINCIPAL_A, "Grace")],
)
def test_confirmation_is_bound_to_principal_and_arguments(
    postgres_engine: Engine,
    principal: Principal,
    name: str,
) -> None:
    runtime = mutation_runtime(postgres_engine)
    tool_name = encode_tool_ref(runtime.definition.ref)

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            preview_result = await client.call_tool(tool_name, preview_arguments())
        preview = cast(dict[str, object], preview_result.structured_content)
        async with Client(server_for(runtime, principal)) as client:
            rejected = await client.call_tool(
                tool_name,
                confirm_arguments(preview, name=name),
            )
            assert error_code(rejected) == "mutation_confirmation_invalid"

    asyncio.run(exercise())
    assert runtime.calls == []


def test_expired_challenge_and_unknown_version_do_not_dispatch(postgres_engine: Engine) -> None:
    now = [datetime(2026, 6, 1, tzinfo=UTC)]
    runtime = mutation_runtime(postgres_engine, clock=lambda: now[0])
    tool_name = encode_tool_ref(runtime.definition.ref)

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            preview_result = await client.call_tool(tool_name, preview_arguments())
            preview = cast(dict[str, object], preview_result.structured_content)
            now[0] += timedelta(minutes=5, seconds=1)
            expired = await client.call_tool(tool_name, confirm_arguments(preview))
            assert error_code(expired) == "mutation_confirmation_invalid"

            unknown_ref = ToolRef(
                runtime.definition.namespace,
                runtime.definition.name,
                ToolVersion("1.1.0"),
            )
            unknown = await client.call_tool(
                encode_tool_ref(unknown_ref),
                preview_arguments(),
            )
            assert error_code(unknown) == "tool_not_found"

    asyncio.run(exercise())
    assert runtime.calls == []


def test_authorization_denial_prevents_preview_and_dispatch(postgres_engine: Engine) -> None:
    runtime = mutation_runtime(postgres_engine, required_scope="admin:write")

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            denied = await client.call_tool(
                encode_tool_ref(runtime.definition.ref),
                preview_arguments(),
            )
            assert error_code(denied) == "authorization_failed"

    asyncio.run(exercise())
    assert runtime.calls == []


def test_idempotency_conflict_and_concurrent_reuse_dispatch_once(
    postgres_engine: Engine,
) -> None:
    runtime = mutation_runtime(postgres_engine)
    tool_name = encode_tool_ref(runtime.definition.ref)

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            first_preview_result = await client.call_tool(tool_name, preview_arguments("Ada"))
            second_preview_result = await client.call_tool(tool_name, preview_arguments("Grace"))
            first_preview = cast(dict[str, object], first_preview_result.structured_content)
            second_preview = cast(dict[str, object], second_preview_result.structured_content)
            first = await client.call_tool(
                tool_name,
                confirm_arguments(first_preview, idempotency_key="shared-key"),
            )
            conflict = await client.call_tool(
                tool_name,
                confirm_arguments(
                    second_preview,
                    name="Grace",
                    idempotency_key="shared-key",
                ),
            )
            assert first.structured_content == {"updated_name": "Ada"}
            assert error_code(conflict) == "idempotency_conflict"

            third_preview_result = await client.call_tool(tool_name, preview_arguments("Lin"))
            third_preview = cast(dict[str, object], third_preview_result.structured_content)
            confirmation = confirm_arguments(
                third_preview,
                name="Lin",
                idempotency_key="concurrent-key",
            )
            left, right = await asyncio.gather(
                client.call_tool(tool_name, confirmation),
                client.call_tool(tool_name, confirmation),
            )
            assert left.structured_content == {"updated_name": "Lin"}
            assert right.structured_content == {"updated_name": "Lin"}

    asyncio.run(exercise())
    assert [call["name"] for call in runtime.calls] == ["Ada", "Lin"]


def test_post_dispatch_audit_failure_is_reported_as_unknown_outcome(
    postgres_engine: Engine,
) -> None:
    runtime = mutation_runtime(postgres_engine, audit=FailingResultAudit())
    tool_name = encode_tool_ref(runtime.definition.ref)

    async def exercise() -> None:
        async with Client(server_for(runtime)) as client:
            preview_result = await client.call_tool(tool_name, preview_arguments())
            preview = cast(dict[str, object], preview_result.structured_content)
            result = await client.call_tool(tool_name, confirm_arguments(preview))
            assert error_code(result) == "mutation_outcome_unknown"

    asyncio.run(exercise())
    assert len(runtime.calls) == 1
