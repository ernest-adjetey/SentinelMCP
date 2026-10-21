"""Bounded signed-JWT authentication and normalized caller identity."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast, final, runtime_checkable

import jwt
from jwt.algorithms import AllowedPublicKeys

MAX_TOKEN_BYTES = 8_192
MAX_SUBJECT_LENGTH = 255
MAX_ISSUER_LENGTH = 2_048
MAX_CLAIM_ITEMS = 64
_CLAIM_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9:._/-]{0,126}[A-Za-z0-9])?$")


class AuthenticationFailureReason(StrEnum):
    """Stable internal categories that never include token contents."""

    MISSING = "missing"
    MALFORMED = "malformed"
    INVALID = "invalid"


class AuthenticationError(Exception):
    """Expected authentication failure without raw token or library details."""

    def __init__(self, reason: AuthenticationFailureReason) -> None:
        self.reason = reason
        super().__init__("Caller authentication failed")


@dataclass(frozen=True, slots=True)
class Principal:
    """Normalized authenticated identity carried beyond the ingress boundary."""

    subject: str
    issuer: str
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, str)
            or not self.subject
            or len(self.subject) > MAX_SUBJECT_LENGTH
            or any(character.isspace() or ord(character) < 32 for character in self.subject)
        ):
            raise ValueError("principal subject must be a bounded non-whitespace identifier")
        if (
            not isinstance(self.issuer, str)
            or not self.issuer
            or self.issuer != self.issuer.strip()
            or len(self.issuer) > MAX_ISSUER_LENGTH
        ):
            raise ValueError("principal issuer must be a bounded non-whitespace value")
        object.__setattr__(self, "roles", _validated_claim_values(self.roles, claim="roles"))
        object.__setattr__(self, "scopes", _validated_claim_values(self.scopes, claim="scopes"))


@dataclass(frozen=True, slots=True)
class JWTVerificationKey:
    """One configured verification key bound to a key ID and algorithm."""

    key_id: str
    algorithm: str
    public_key: AllowedPublicKeys | str | bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not self.key_id or len(self.key_id) > 128:
            raise ValueError("JWT key ID must contain 1-128 characters")
        if not isinstance(self.algorithm, str) or not self.algorithm:
            raise ValueError("JWT key algorithm must be explicit")


@runtime_checkable
class Authenticator(Protocol):
    """Ingress boundary that converts a credential into a normalized principal."""

    def authenticate(self, token: str) -> Principal:
        """Authenticate one bearer token or raise ``AuthenticationError``."""


@final
class JWTAuthenticator(Authenticator):
    """Verify bounded JWTs against preconfigured key IDs and algorithms."""

    __slots__ = ("_algorithms", "_audience", "_issuer", "_keys", "_leeway_seconds")

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        keys: Iterable[JWTVerificationKey],
        allowed_algorithms: frozenset[str] = frozenset({"EdDSA"}),
        leeway_seconds: int = 0,
    ) -> None:
        if (
            not isinstance(issuer, str)
            or not issuer
            or issuer != issuer.strip()
            or len(issuer) > MAX_ISSUER_LENGTH
            or not isinstance(audience, str)
            or not audience
            or audience != audience.strip()
            or len(audience) > 255
        ):
            raise ValueError("JWT issuer and audience must be bounded non-whitespace strings")
        if not allowed_algorithms or any(
            not isinstance(algorithm, str) or not algorithm or len(algorithm) > 32
            for algorithm in allowed_algorithms
        ):
            raise ValueError("at least one JWT algorithm must be allowed")
        if (
            isinstance(leeway_seconds, bool)
            or not isinstance(leeway_seconds, int)
            or not 0 <= leeway_seconds <= 300
        ):
            raise ValueError("JWT leeway must be an integer from 0 through 300 seconds")
        configured: dict[str, JWTVerificationKey] = {}
        for key in keys:
            if key.key_id in configured:
                raise ValueError(f"duplicate JWT key ID: {key.key_id}")
            if key.algorithm not in allowed_algorithms:
                raise ValueError("JWT key algorithm is outside the configured allowlist")
            configured[key.key_id] = key
        if not configured:
            raise ValueError("at least one JWT verification key is required")
        self._issuer = issuer
        self._audience = audience
        self._keys = MappingProxyType(configured)
        self._algorithms = tuple(sorted(allowed_algorithms))
        self._leeway_seconds = leeway_seconds

    def authenticate(self, token: str) -> Principal:
        if not isinstance(token, str) or not token or token != token.strip():
            raise AuthenticationError(AuthenticationFailureReason.MALFORMED)
        try:
            if len(token.encode("ascii")) > MAX_TOKEN_BYTES:
                raise AuthenticationError(AuthenticationFailureReason.MALFORMED)
        except UnicodeEncodeError as error:
            raise AuthenticationError(AuthenticationFailureReason.MALFORMED) from error

        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            algorithm = header.get("alg")
            if not isinstance(key_id, str) or not isinstance(algorithm, str):
                raise AuthenticationError(AuthenticationFailureReason.INVALID)
            if algorithm not in self._algorithms:
                raise AuthenticationError(AuthenticationFailureReason.INVALID)
            key = self._keys.get(key_id)
            if key is None or key.algorithm != algorithm:
                raise AuthenticationError(AuthenticationFailureReason.INVALID)
            claims = jwt.decode(
                token,
                key=key.public_key,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={
                    "require": ["aud", "exp", "iat", "iss", "sub"],
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_nbf": True,
                    "verify_signature": True,
                },
            )
            return _principal_from_claims(claims)
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, TypeError, ValueError) as error:
            raise AuthenticationError(AuthenticationFailureReason.INVALID) from error


@final
class DenyAllAuthenticator(Authenticator):
    """Safe composition default for an ingress without configured verification keys."""

    __slots__ = ()

    def authenticate(self, token: str) -> Principal:
        del token
        raise AuthenticationError(AuthenticationFailureReason.INVALID)


def _principal_from_claims(claims: Mapping[str, object]) -> Principal:
    subject = claims.get("sub")
    issuer = claims.get("iss")
    if not isinstance(subject, str) or not isinstance(issuer, str):
        raise AuthenticationError(AuthenticationFailureReason.INVALID)
    roles_value = claims.get("roles", [])
    if not isinstance(roles_value, list) or not all(isinstance(role, str) for role in roles_value):
        raise AuthenticationError(AuthenticationFailureReason.INVALID)
    scope_value = claims.get("scope", "")
    if not isinstance(scope_value, str):
        raise AuthenticationError(AuthenticationFailureReason.INVALID)
    try:
        return Principal(
            subject=subject,
            issuer=issuer,
            roles=frozenset(cast(list[str], roles_value)),
            scopes=frozenset(scope_value.split()),
        )
    except ValueError as error:
        raise AuthenticationError(AuthenticationFailureReason.INVALID) from error


def _validated_claim_values(values: Iterable[str], *, claim: str) -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"principal {claim} must be a collection")
    try:
        result = frozenset(values)
    except TypeError as error:
        raise ValueError(f"principal {claim} must contain hashable strings") from error
    if len(result) > MAX_CLAIM_ITEMS:
        raise ValueError(f"principal {claim} exceeds the item limit")
    if any(
        not isinstance(value, str) or not _CLAIM_VALUE_PATTERN.fullmatch(value) for value in result
    ):
        raise ValueError(f"principal {claim} contains an invalid value")
    return result
