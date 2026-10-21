"""REST preview and confirmed mutation flow against durable PostgreSQL state."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import Engine

from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
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
    Authenticator,
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

pytestmark = pytest.mark.postgres

PRINCIPALS = {
    "principal-a": Principal("user-a", "https://issuer.test", scopes=frozenset({"tools:write"})),
    "principal-b": Principal("user-b", "https://issuer.test", scopes=frozenset({"tools:write"})),
}


class MappingAuthenticator(Authenticator):
    def authenticate(self, token: str) -> Principal:
        return PRINCIPALS[token]


def mutation_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.profile"),
        name=ToolName("update"),
        version=ToolVersion("1.0.0"),
        description="Update one synthetic profile.",
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


def app_for(
    engine: Engine,
) -> tuple[FastAPI, list[FrozenJSONMapping]]:
    tool = mutation_definition()
    registry = InMemoryToolRegistry()
    registry.register(tool)
    calls: list[FrozenJSONMapping] = []

    async def update(arguments: FrozenJSONMapping) -> object:
        calls.append(arguments)
        return {"updated_name": cast(str, arguments["name"])}

    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(tool.ref, SyntheticToolAdapter(update)))
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                tool.identity,
                OperationClass.MUTATION,
                required_scopes=frozenset({"tools:write"}),
            ),
        )
    )
    executor = ToolExecutor(registry, bindings, authorization_policy=policy)
    tokens: Iterator[str] = iter(("a" * 43, "b" * 43, "c" * 43))
    mutations = MutationService(
        executor,
        PostgresMutationStateStore(engine),
        token_factory=lambda: next(tokens),
    )
    return create_execution_app(executor, MappingAuthenticator(), mutations), calls


def post(
    app: FastAPI,
    path: str,
    body: dict[str, object],
    *,
    principal: str = "principal-a",
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                path,
                json=body,
                headers={"Authorization": f"Bearer {principal}"},
            )

    return asyncio.run(send())


def body(name: str = "Ada") -> dict[str, object]:
    return {
        "tool": {"namespace": "demo.profile", "name": "update", "version": "1.0.0"},
        "arguments": {"name": name},
    }


def confirm_body(
    preview: httpx.Response,
    *,
    name: str = "Ada",
    key: str = "profile-update-1",
) -> dict[str, object]:
    payload = body(name)
    payload["confirmation_token"] = preview.json()["preview"]["confirmation_token"]
    payload["idempotency_key"] = key
    return payload


def test_preview_confirm_and_idempotent_reuse_execute_once(postgres_engine: Engine) -> None:
    app, calls = app_for(postgres_engine)

    preview = post(app, "/v1/mutations/preview", body())
    confirmed = post(app, "/v1/mutations/confirm", confirm_body(preview))
    reused = post(app, "/v1/mutations/confirm", confirm_body(preview))
    consumed_for_new_key = post(
        app,
        "/v1/mutations/confirm",
        confirm_body(preview, key="different-key"),
    )

    assert preview.status_code == 201
    assert preview.json()["status"] == "confirmation_required"
    assert confirmed.status_code == 200
    assert confirmed.json()["result"] == {"updated_name": "Ada"}
    assert reused.status_code == 200
    assert reused.json()["execution_id"] == confirmed.json()["execution_id"]
    assert consumed_for_new_key.status_code == 409
    assert consumed_for_new_key.json()["error"]["code"] == "mutation_confirmation_invalid"
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("principal", "name"),
    [("principal-b", "Ada"), ("principal-a", "Grace")],
)
def test_confirmation_is_bound_to_principal_and_arguments(
    postgres_engine: Engine,
    principal: str,
    name: str,
) -> None:
    app, calls = app_for(postgres_engine)
    preview = post(app, "/v1/mutations/preview", body())

    response = post(
        app,
        "/v1/mutations/confirm",
        confirm_body(preview, name=name),
        principal=principal,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mutation_confirmation_invalid"
    assert calls == []


def test_same_idempotency_key_with_different_confirmed_request_conflicts(
    postgres_engine: Engine,
) -> None:
    app, calls = app_for(postgres_engine)
    first_preview = post(app, "/v1/mutations/preview", body("Ada"))
    second_preview = post(app, "/v1/mutations/preview", body("Grace"))
    first = post(app, "/v1/mutations/confirm", confirm_body(first_preview, name="Ada"))
    conflict = post(app, "/v1/mutations/confirm", confirm_body(second_preview, name="Grace"))

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(calls) == 1
