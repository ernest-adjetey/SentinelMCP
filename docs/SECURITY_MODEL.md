# Security Model

## Implemented scope

Phase 5 adds authentication and authorization to the execution path. SentinelMCP verifies a
bounded signed JWT at the REST ingress, converts trusted claims into a small immutable
`Principal`, resolves and validates the exact tool request, and applies a deterministic policy
before mutation handling, runtime binding lookup, or adapter dispatch.

The implemented lifecycle is:

```text
REST envelope
-> bearer authentication
-> exact ToolRef resolution
-> argument validation
-> authorization
-> mutation guard
-> exact runtime binding
-> adapter execution
-> output validation
```

Authentication and authorization are separate decisions. A valid token proves only that the
configured issuer signed the claims for this audience. It does not grant tool access.

## Authentication boundary

`JWTAuthenticator` accepts verification keys supplied by application configuration. Each key
is bound to an explicit `kid` and algorithm. The default and currently tested algorithm is
EdDSA with Ed25519 keys. Verification requires:

- a signature from the selected configured public key;
- an algorithm on the explicit allowlist that also matches the selected key;
- exact configured issuer and audience;
- `sub`, `iss`, `aud`, `iat`, and `exp` claims;
- valid expiration, issued-at, and optional not-before times; and
- an ASCII token no larger than 8,192 bytes.

The implementation reads the unverified header only to select a preconfigured key and reject
unapproved algorithms. Unverified claims never select policy or identity. It does not accept
unsigned tokens, fetch keys from token-controlled URLs, perform OIDC discovery, or derive an
HMAC secret from asymmetric key material.

Multiple configured key IDs permit an overlap window during rotation. Configuration rejects
duplicate key IDs and keys whose declared algorithm is outside the allowlist. Key retrieval,
rotation scheduling, secure storage, and revocation are deployment responsibilities; changing
the configured set currently requires rebuilding or restarting the application composition.

## Principal normalization

Only these claims cross the ingress boundary:

- `sub` becomes the principal subject;
- the validated `iss` becomes the principal issuer;
- `roles` may be a JSON array of bounded role strings; and
- `scope` may be a space-separated string of bounded scopes.

Roles and scopes are deduplicated into immutable sets. Each collection is limited to 64
values, and values use a restricted identifier alphabet. The raw bearer token and arbitrary
claims are not carried into the executor or policy layer.

## Authorization policy

`RuleBasedAuthorizationPolicy` evaluates exact, immutable rules containing:

- `allow` or `deny` effect;
- tool identity (`namespace` plus `name`);
- operation class (`read` or `mutation`); and
- optional all-of role and scope requirements.

A rule matches only when its target and every required claim match. A matching deny takes
precedence over matching allows. With no matching allow, the result is deny. Read permission
does not imply mutation permission. Rules intentionally target tool identity rather than one
version, so a rule applies to every registered version under that identity; version-specific
policy is not implemented.

`ToolExecutor` requires an authorization policy when composed. There is no policy-free
execution constructor. A missing principal returns `authentication_failed`; a denied decision
returns `authorization_failed`; an unexpected policy exception fails closed as
`internal_execution_error`. All three paths stop before binding resolution and dispatch.

The checked-in ASGI demo uses `DenyAllAuthenticator` because no signing key is committed. Test
and embedding compositions inject deterministic or deployment-owned authenticators. The demo
tool policy grants only its three exact synthetic read identities to an already authenticated
principal.

## Public failure behavior

The REST boundary requires a single Bearer credential. Missing, malformed, expired,
not-yet-valid, wrong-issuer, wrong-audience, unknown-key, disallowed-algorithm, and
invalid-signature credentials return the same bounded `401 authentication_failed` envelope.
The response contains no token, claims, key ID, library exception, or signature detail.

An authenticated principal without permission receives `403 authorization_failed`. The
response does not reveal which role, scope, deny rule, or allow rule was missing. Internal
policy failures return a generic `500` and never dispatch.

## Verified invariants

Deterministic local Ed25519 tests establish that:

- unauthenticated and invalidly authenticated requests never invoke an adapter;
- wrong issuer, wrong audience, expired token, and invalid signature never invoke an adapter;
- an unauthorized principal never invokes an adapter;
- argument validation occurs before authorization;
- authorization occurs before mutation handling and adapter dispatch;
- explicit deny overrides allow; and
- read authorization does not grant mutation authorization.

These are code and test invariants, not a claim that a particular deployment, identity
provider, signing-key store, or transport is secure.

## Current limitations

- SentinelMCP does not issue tokens or manage users, passwords, sessions, consent, or MFA.
- There is no JWKS fetcher, OIDC discovery, token introspection, or revocation list.
- Policy is configured in process and is not yet persisted, versioned, or administratively
  managed.
- The principal has no delegated agent identity or tenant model.
- TLS termination and trusted signing-key delivery are deployment concerns.
- REST request bodies default to a 1,048,576-byte limit, enforced against both declared
  `Content-Length` and streamed bytes before framework JSON decoding. Deployments still need
  front-proxy connection, header, and aggregate-rate limits.
- MCP protocol and session identities are not treated as authenticated Sentinel principals.
  Trusted in-memory/stdio callers require explicit principal injection; Streamable HTTP uses the
  existing `Authenticator` through official SDK bearer middleware; unconfigured MCP denies all.
- Outbound HTTP requires HTTPS by default, optionally requires an exact normalized host allowlist,
  rejects any resolution containing a non-global or IPv4-transition address, and pins the request
  URL to one approved address while preserving the original Host header and TLS SNI. Redirects
  remain disabled. Explicit trusted-development configuration can permit plaintext or non-global
  targets; production compositions must not enable those exceptions.
- Direct connections are protected from DNS rebinding by IP pinning. A deployment-supplied proxy
  is an additional trust boundary and must enforce equivalent destination policy; proxy behavior
  is outside SentinelMCP's direct socket control.
- Mutation confirmation, durable audit, local reliability controls, constrained replay, bounded
  ingress bodies, and outbound target controls are implemented.

See [Threat model](THREAT_MODEL.md) for broader system threats and
[ADR 0007](adr/0007-signed-jwt-and-exact-policy.md) for the decision rationale. MCP-specific
assumptions are in [MCP integration](MCP.md).
