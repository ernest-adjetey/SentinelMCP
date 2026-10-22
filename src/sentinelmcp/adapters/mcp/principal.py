"""Sentinel principal composition at MCP transport boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, final, runtime_checkable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier

from sentinelmcp.policy import (
    AuthenticationError,
    AuthenticationFailureReason,
    Authenticator,
    Principal,
)


@runtime_checkable
class MCPPrincipalResolver(Protocol):
    """Resolve a transport-authenticated Sentinel principal for one MCP request."""

    def resolve(self) -> Principal:
        """Return a principal or raise ``AuthenticationError``."""


@dataclass(frozen=True, slots=True)
class StaticMCPPrincipalResolver(MCPPrincipalResolver):
    """Inject one trusted principal for in-memory tests or a trusted stdio process."""

    principal: Principal

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal):
            raise TypeError("principal must be a Principal")

    def resolve(self) -> Principal:
        return self.principal


@final
class DenyAllMCPPrincipalResolver(MCPPrincipalResolver):
    """Fail closed when no authenticated principal boundary was configured."""

    __slots__ = ()

    def resolve(self) -> Principal:
        raise AuthenticationError(AuthenticationFailureReason.MISSING)


@final
class AccessTokenMCPPrincipalResolver(MCPPrincipalResolver):
    """Recover the normalized principal produced by the MCP bearer middleware."""

    __slots__ = ()

    def resolve(self) -> Principal:
        token = get_access_token()
        if token is None or token.subject is None or token.claims is None:
            raise AuthenticationError(AuthenticationFailureReason.MISSING)
        issuer = token.claims.get("iss")
        roles = token.claims.get("roles", [])
        if (
            not isinstance(issuer, str)
            or not isinstance(roles, list)
            or not all(isinstance(role, str) for role in roles)
        ):
            raise AuthenticationError(AuthenticationFailureReason.INVALID)
        try:
            return Principal(
                subject=token.subject,
                issuer=issuer,
                roles=frozenset(roles),
                scopes=frozenset(token.scopes),
            )
        except ValueError as error:
            raise AuthenticationError(AuthenticationFailureReason.INVALID) from error


@final
class AuthenticatorTokenVerifier(TokenVerifier):
    """Bridge the official SDK bearer middleware to Sentinel's Authenticator."""

    __slots__ = ("_authenticator", "_resource")

    def __init__(self, authenticator: Authenticator, *, resource: str | None = None) -> None:
        self._authenticator = authenticator
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = self._authenticator.authenticate(token)
        except AuthenticationError:
            return None
        return AccessToken(
            token=token,
            client_id=principal.subject,
            scopes=sorted(principal.scopes),
            resource=self._resource,
            subject=principal.subject,
            claims={"iss": principal.issuer, "roles": sorted(principal.roles)},
        )
