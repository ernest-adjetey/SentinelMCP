"""End-to-end authentication and authorization rejection matrix."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.application import ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
)
from sentinelmcp.policy import (
    Authenticator,
    AuthorizationRule,
    JWTAuthenticator,
    JWTVerificationKey,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

ISSUER = "https://issuer.sentinelmcp.test"
AUDIENCE = "sentinelmcp-api"
KEY_ID = "gateway-ed25519-1"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
OTHER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[AdapterInvocation] = []

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.calls.append(invocation)
        return AdapterSuccess({"value": "ok"})


def make_token(
    *,
    private_key: Ed25519PrivateKey = PRIVATE_KEY,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_delta: timedelta = timedelta(minutes=5),
    scope: str = "tools:execute",
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "aud": audience,
            "exp": now + expires_delta,
            "iat": now,
            "iss": issuer,
            "scope": scope,
            "sub": "gateway-user",
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": KEY_ID},
    )


def secured_app() -> tuple[FastAPI, RecordingAdapter]:
    definition = ToolDefinition(
        namespace=ToolNamespace("demo.security"),
        name=ToolName("read"),
        version=ToolVersion("1.0.0"),
        description="Exercise the secured gateway.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
    )
    registry = InMemoryToolRegistry()
    registry.register(definition)
    adapter = RecordingAdapter()
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                definition.identity,
                OperationClass.READ,
                required_scopes=frozenset({"tools:execute"}),
            ),
        )
    )
    executor = ToolExecutor(registry, bindings, authorization_policy=policy)
    authenticator = JWTAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        keys=(JWTVerificationKey(KEY_ID, "EdDSA", PRIVATE_KEY.public_key()),),
    )
    return create_execution_app(executor, authenticator), adapter


def post(app: FastAPI, authorization: str | None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {} if authorization is None else {"Authorization": authorization}
            return await client.post(
                "/v1/execute",
                json={
                    "tool": {
                        "namespace": "demo.security",
                        "name": "read",
                        "version": "1.0.0",
                    },
                    "arguments": {},
                },
                headers=headers,
            )

    return asyncio.run(send())


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="missing"),
        pytest.param("Basic credentials", id="wrong-scheme"),
        pytest.param("Bearer", id="missing-token"),
        pytest.param("Bearer  token", id="leading-token-space"),
        pytest.param("Bearer not-a-jwt", id="malformed-token"),
        pytest.param(
            f"Bearer {make_token(expires_delta=timedelta(seconds=-1))}",
            id="expired",
        ),
        pytest.param(f"Bearer {make_token(audience='wrong-api')}", id="wrong-audience"),
        pytest.param(
            f"Bearer {make_token(issuer='https://wrong.test')}",
            id="wrong-issuer",
        ),
        pytest.param(
            f"Bearer {make_token(private_key=OTHER_PRIVATE_KEY)}",
            id="invalid-signature",
        ),
    ],
)
def test_authentication_failures_never_invoke_adapter(authorization: str | None) -> None:
    app, adapter = secured_app()

    response = post(app, authorization)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_failed"
    assert "traceback" not in response.text.casefold()
    assert adapter.calls == []


def test_unexpected_authenticator_failure_is_hidden() -> None:
    class ExplodingAuthenticator(Authenticator):
        def authenticate(self, _token: str) -> Principal:
            raise RuntimeError("secret identity provider detail")

    response = post(
        create_execution_app(
            ToolExecutor(
                InMemoryToolRegistry(),
                InMemoryRuntimeBindingRegistry(),
                authorization_policy=RuleBasedAuthorizationPolicy(()),
            ),
            ExplodingAuthenticator(),
        ),
        "Bearer opaque-test-token",
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_execution_error"
    assert "secret" not in response.text


def test_unauthorized_principal_never_invokes_adapter() -> None:
    app, adapter = secured_app()

    response = post(app, f"Bearer {make_token(scope='catalog:read')}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "authorization_failed"
    assert adapter.calls == []


def test_valid_authenticated_authorized_request_executes_once() -> None:
    app, adapter = secured_app()

    response = post(app, f"Bearer {make_token()}")

    assert response.status_code == 200
    assert response.json()["result"] == {"value": "ok"}
    assert len(adapter.calls) == 1
