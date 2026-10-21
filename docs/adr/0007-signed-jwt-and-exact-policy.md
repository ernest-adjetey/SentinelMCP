# ADR 0007: Signed JWT Ingress and Exact Default-Deny Policy

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

The execution gateway must authenticate callers and enforce tool authorization without
becoming an identity provider. Trusting caller-supplied actor fields, accepting unsigned
tokens, or making policy optional would allow direct execution to bypass the intended
security lifecycle. The policy also needs to distinguish reads from mutations without
introducing a large policy language.

## Decision

Verify signed JWT bearer tokens at the REST boundary with PyJWT. Supply public keys through
application configuration and bind each key ID to one explicit algorithm. Require issuer,
audience, subject, issued-at, and expiry claims, verify optional not-before, use an explicit
algorithm allowlist, and reject tokens over 8,192 ASCII bytes.

Normalize only subject, issuer, roles, and scopes into an immutable `Principal`. Do not carry
the token or arbitrary claims beyond ingress.

Use a small rule model over tool identity and operation class with optional all-of roles and
scopes. Matching deny rules take precedence, and absence of a matching allow is deny. Require
an `AuthorizationPolicy` in every `ToolExecutor` composition. Evaluate it after exact tool
resolution and input validation but before mutation handling, binding lookup, or dispatch.

Keep token issuance, OIDC discovery, JWKS networking, user management, and policy persistence
outside this phase. Support multiple statically configured key IDs so deployments can perform
overlapping key rotation without token-controlled network access.

## Consequences

- Public execution cannot be composed without an authorization policy.
- Invalid credentials fail at ingress without reaching the executor; missing principals and
  denied decisions fail before adapter work.
- Algorithm confusion is constrained by both a global allowlist and per-key algorithm binding.
- The core executor and policy layer contain no FastAPI request or raw-token types.
- Rule evaluation is deterministic and inspectable but cannot express version-specific,
  attribute-based, tenant, or delegated-agent policy.
- Static key configuration avoids request-time identity-provider availability but makes key
  distribution, revocation, and refresh deployment concerns.
- The checked-in runnable ASGI module fails closed because it has no committed verification
  key.

## Alternatives considered

**API keys:** rejected as the primary mechanism because they do not provide signed,
issuer-bound claims and tend to mix credential identity with authorization policy.

**Opaque token introspection:** deferred because it introduces a network dependency and
identity-provider availability semantics without a current deployment requirement.

**Remote JWKS/OIDC discovery:** deferred because key caching, refresh, TLS trust, URL control,
and outage behavior deserve a concrete deployment design. No URL from a token is fetched.

**A general policy language or external policy engine:** rejected for now because exact tool,
operation, role, and scope rules satisfy the current invariant with substantially less
configuration and dependency surface.

**Optional executor policy:** rejected because a forgotten argument would silently create an
authorization bypass for direct application callers.
