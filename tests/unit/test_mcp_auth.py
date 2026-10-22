"""MCP transport authentication and principal-boundary tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from sentinelmcp.adapters.mcp import (
    AccessTokenMCPPrincipalResolver,
    AuthenticatorTokenVerifier,
    create_authenticated_streamable_http_app,
)
from sentinelmcp.application import create_demo_runtime
from sentinelmcp.policy import (
    AuthenticationError,
    AuthenticationFailureReason,
    Authenticator,
    Principal,
)


class MappingAuthenticator(Authenticator):
    def authenticate(self, token: str) -> Principal:
        if token != "valid-token":
            raise AuthenticationError(AuthenticationFailureReason.INVALID)
        return Principal(
            "mcp-user",
            "https://issuer.test",
            roles=frozenset({"operator"}),
            scopes=frozenset({"tools:read"}),
        )


def test_authenticator_token_verifier_preserves_only_normalized_identity() -> None:
    verifier = AuthenticatorTokenVerifier(
        MappingAuthenticator(),
        resource="https://gateway.test/mcp",
    )

    async def exercise() -> None:
        valid = await verifier.verify_token("valid-token")
        invalid = await verifier.verify_token("private-invalid-token")

        assert valid is not None
        assert valid.client_id == "mcp-user"
        assert valid.subject == "mcp-user"
        assert valid.scopes == ["tools:read"]
        assert valid.resource == "https://gateway.test/mcp"
        assert valid.claims == {"iss": "https://issuer.test", "roles": ["operator"]}
        assert invalid is None

    asyncio.run(exercise())


def test_access_token_context_resolves_bounded_sentinel_principal() -> None:
    access_token = AccessToken(
        token="not-exposed-beyond-sdk-context",
        client_id="mcp-user",
        subject="mcp-user",
        scopes=["tools:read"],
        claims={"iss": "https://issuer.test", "roles": ["operator"]},
    )
    context_token = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        principal = AccessTokenMCPPrincipalResolver().resolve()
    finally:
        auth_context_var.reset(context_token)

    assert principal == Principal(
        "mcp-user",
        "https://issuer.test",
        roles=frozenset({"operator"}),
        scopes=frozenset({"tools:read"}),
    )


def test_access_token_context_fails_closed_when_missing_or_malformed() -> None:
    resolver = AccessTokenMCPPrincipalResolver()
    with pytest.raises(AuthenticationError):
        resolver.resolve()

    malformed = AccessToken(
        token="token",
        client_id="mcp-user",
        subject="mcp-user",
        scopes=[],
        claims={"iss": 7, "roles": []},
    )
    context_token = auth_context_var.set(AuthenticatedUser(malformed))
    try:
        with pytest.raises(AuthenticationError):
            resolver.resolve()
    finally:
        auth_context_var.reset(context_token)


def test_streamable_http_rejects_missing_and_invalid_bearer_tokens() -> None:
    runtime = create_demo_runtime()
    app = create_authenticated_streamable_http_app(
        runtime.registry,
        runtime.executor,
        MappingAuthenticator(),
        auth_settings=AuthSettings(
            issuer_url=AnyHttpUrl("https://issuer.test"),
            resource_server_url=AnyHttpUrl("https://gateway.test/mcp"),
            required_scopes=["tools:read"],
            validate_token_resource=True,
        ),
        token_resource="https://gateway.test/mcp",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://gateway.test"
        ) as client:
            missing = await client.post("/mcp", json={})
            invalid = await client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer private-invalid-token"},
            )
        assert missing.status_code == 401
        assert invalid.status_code == 401
        assert "private-invalid-token" not in invalid.text

    asyncio.run(exercise())
