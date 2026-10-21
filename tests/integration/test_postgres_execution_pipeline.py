"""Gateway execution through the durable registry and in-memory runtime binding."""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest

from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.application import ToolExecutor
from sentinelmcp.domain import OperationClass, ToolDefinition, ToolName, ToolNamespace, ToolVersion
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
    SyntheticToolAdapter,
)
from sentinelmcp.policy import (
    Authenticator,
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import PostgresToolRegistry

pytestmark = pytest.mark.postgres


class IntegrationAuthenticator(Authenticator):
    def authenticate(self, token: str) -> Principal:
        assert token == "integration-token"
        return Principal(subject="integration", issuer="https://issuer.test")


def catalog_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.catalog"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description="Return a synthetic catalog record.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
    )


async def catalog_lookup(arguments: FrozenJSONMapping) -> object:
    return {"asset_id": cast(str, arguments["id"])}


def test_fastapi_executor_uses_postgres_registry_without_storage_coupling(
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = catalog_definition()
    postgres_registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, SyntheticToolAdapter(catalog_lookup)))
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, OperationClass.READ),)
    )
    app = create_execution_app(
        ToolExecutor(postgres_registry, bindings, authorization_policy=policy),
        IntegrationAuthenticator(),
    )

    async def execute() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/execute",
                json={
                    "tool": {
                        "namespace": "demo.catalog",
                        "name": "lookup",
                        "version": "1.0.0",
                    },
                    "arguments": {"id": "asset-42"},
                },
                headers={"Authorization": "Bearer integration-token"},
            )

    response = asyncio.run(execute())

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool"] == "demo.catalog.lookup@1.0.0"
    assert payload["result"] == {"asset_id": "asset-42"}
