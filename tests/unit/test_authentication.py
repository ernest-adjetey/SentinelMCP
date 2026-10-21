"""Signed-JWT authentication tests with deterministic local Ed25519 keys."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sentinelmcp.policy import (
    AuthenticationError,
    AuthenticationFailureReason,
    DenyAllAuthenticator,
    JWTAuthenticator,
    JWTVerificationKey,
    Principal,
)

ISSUER = "https://issuer.sentinelmcp.test"
AUDIENCE = "sentinelmcp-api"
KEY_ID = "test-ed25519-1"
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
OTHER_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


def authenticator() -> JWTAuthenticator:
    return JWTAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        keys=(JWTVerificationKey(KEY_ID, "EdDSA", PRIVATE_KEY.public_key()),),
    )


def make_token(
    *,
    private_key: Ed25519PrivateKey = PRIVATE_KEY,
    key_id: str | None = KEY_ID,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_delta: timedelta = timedelta(minutes=5),
    not_before_delta: timedelta | None = None,
    roles: object = None,
    scope: object = "tools:execute tools:inspect",
    omit: frozenset[str] = frozenset(),
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "aud": audience,
        "exp": now + expires_delta,
        "iat": now,
        "iss": issuer,
        "roles": ["developer", "operator"] if roles is None else roles,
        "scope": scope,
        "sub": "user-123",
    }
    if not_before_delta is not None:
        claims["nbf"] = now + not_before_delta
    for claim in omit:
        claims.pop(claim, None)
    headers = {"typ": "JWT"}
    if key_id is not None:
        headers["kid"] = key_id
    return jwt.encode(
        claims,
        private_key,
        algorithm="EdDSA",
        headers=headers,
    )


def test_valid_token_becomes_normalized_principal() -> None:
    principal = authenticator().authenticate(make_token())

    assert principal.subject == "user-123"
    assert principal.issuer == ISSUER
    assert principal.roles == frozenset({"developer", "operator"})
    assert principal.scopes == frozenset({"tools:execute", "tools:inspect"})


@pytest.mark.parametrize(
    "subject",
    [cast(str, None), "", "x" * 256, "has space", "control\x00character"],
)
def test_principal_rejects_invalid_subject(subject: str) -> None:
    with pytest.raises(ValueError, match="subject"):
        Principal(subject, ISSUER)


@pytest.mark.parametrize("issuer", [cast(str, None), "", " issuer", "x" * 2_049])
def test_principal_rejects_invalid_issuer(issuer: str) -> None:
    with pytest.raises(ValueError, match="issuer"):
        Principal("subject", issuer)


@pytest.mark.parametrize(
    "roles",
    [
        cast(frozenset[str], "developer"),
        frozenset(f"role-{index}" for index in range(65)),
        frozenset({"bad role"}),
    ],
)
def test_principal_rejects_malformed_or_excessive_claim_values(
    roles: frozenset[str],
) -> None:
    with pytest.raises(ValueError, match="roles"):
        Principal("subject", ISSUER, roles=roles)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(make_token(expires_delta=timedelta(seconds=-1)), id="expired"),
        pytest.param(make_token(not_before_delta=timedelta(minutes=5)), id="not-before"),
        pytest.param(make_token(issuer="https://wrong.test"), id="wrong-issuer"),
        pytest.param(make_token(audience="wrong-api"), id="wrong-audience"),
        pytest.param(make_token(private_key=OTHER_PRIVATE_KEY), id="invalid-signature"),
        pytest.param(make_token(key_id="unknown-key"), id="unknown-key-id"),
        pytest.param(make_token(omit=frozenset({"exp"})), id="missing-expiry"),
        pytest.param(make_token(roles="developer"), id="malformed-roles"),
        pytest.param(make_token(roles=["bad role"]), id="invalid-role-value"),
        pytest.param(
            make_token(roles=[f"role-{index}" for index in range(65)]),
            id="excessive-roles",
        ),
        pytest.param(make_token(scope=["tools:execute"]), id="malformed-scope"),
        pytest.param(make_token(scope="bad$scope"), id="invalid-scope-value"),
    ],
)
def test_invalid_tokens_fail_without_library_or_claim_details(token: str) -> None:
    with pytest.raises(AuthenticationError) as captured:
        authenticator().authenticate(token)

    assert captured.value.reason is AuthenticationFailureReason.INVALID
    assert str(captured.value) == "Caller authentication failed"
    assert "jwt" not in str(captured.value).casefold()


@pytest.mark.parametrize("token", ["", " token", "not-a-jwt", "x" * 8_193, "nonascii-\u00e9"])
def test_malformed_or_oversized_tokens_are_bounded(token: str) -> None:
    with pytest.raises(AuthenticationError) as captured:
        authenticator().authenticate(token)

    assert captured.value.reason in {
        AuthenticationFailureReason.MALFORMED,
        AuthenticationFailureReason.INVALID,
    }


def test_algorithm_allowlist_rejects_header_before_key_use() -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"aud": AUDIENCE, "exp": now + timedelta(minutes=5), "iat": now, "iss": ISSUER, "sub": "u"},
        "a-long-test-only-hmac-secret-that-is-not-a-public-key",
        algorithm="HS256",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(AuthenticationError) as captured:
        authenticator().authenticate(token)

    assert captured.value.reason is AuthenticationFailureReason.INVALID


def test_authenticator_configuration_rejects_duplicate_or_unallowed_keys() -> None:
    key = JWTVerificationKey(KEY_ID, "EdDSA", PRIVATE_KEY.public_key())
    with pytest.raises(ValueError, match="duplicate"):
        JWTAuthenticator(issuer=ISSUER, audience=AUDIENCE, keys=(key, key))
    with pytest.raises(ValueError, match="allowlist"):
        JWTAuthenticator(
            issuer=ISSUER,
            audience=AUDIENCE,
            keys=(JWTVerificationKey(KEY_ID, "RS256", PRIVATE_KEY.public_key()),),
        )


@pytest.mark.parametrize(
    "key_id",
    [cast(str, None), "", "x" * 129],
)
def test_verification_key_requires_bounded_string_id(key_id: str) -> None:
    with pytest.raises(ValueError, match="key ID"):
        JWTVerificationKey(key_id, "EdDSA", PRIVATE_KEY.public_key())


@pytest.mark.parametrize("algorithm", [cast(str, None), ""])
def test_verification_key_requires_explicit_algorithm(algorithm: str) -> None:
    with pytest.raises(ValueError, match="algorithm"):
        JWTVerificationKey(KEY_ID, algorithm, PRIVATE_KEY.public_key())


def test_authenticator_rejects_invalid_security_configuration() -> None:
    key = JWTVerificationKey(KEY_ID, "EdDSA", PRIVATE_KEY.public_key())
    for issuer, audience in (("", AUDIENCE), (ISSUER, " audience"), ("x" * 2_049, AUDIENCE)):
        with pytest.raises(ValueError, match="issuer and audience"):
            JWTAuthenticator(issuer=issuer, audience=audience, keys=(key,))
    with pytest.raises(ValueError, match="algorithm"):
        JWTAuthenticator(
            issuer=ISSUER,
            audience=AUDIENCE,
            keys=(key,),
            allowed_algorithms=frozenset(),
        )
    for leeway in (cast(int, True), cast(int, 1.5), -1, 301):
        with pytest.raises(ValueError, match="leeway"):
            JWTAuthenticator(
                issuer=ISSUER,
                audience=AUDIENCE,
                keys=(key,),
                leeway_seconds=leeway,
            )
    with pytest.raises(ValueError, match="verification key"):
        JWTAuthenticator(issuer=ISSUER, audience=AUDIENCE, keys=())


def test_missing_key_id_and_deny_all_authenticator_fail_closed() -> None:
    token = make_token(key_id=None)

    with pytest.raises(AuthenticationError):
        authenticator().authenticate(token)
    with pytest.raises(AuthenticationError) as captured:
        DenyAllAuthenticator().authenticate("opaque")

    assert captured.value.reason is AuthenticationFailureReason.INVALID
