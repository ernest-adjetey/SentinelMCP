# Configuration Reference

SentinelMCP is composed through typed Python constructors. Environment variables are limited to
development/operator paths; security policy is not silently loaded from ambient environment.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `SENTINELMCP_DATABASE_URL` | Alembic and application PostgreSQL URL in operator workflows |
| `SENTINELMCP_TEST_DATABASE_URL` | Enables PostgreSQL tests and the benchmark mutation workload |
| `SENTINELMCP_REPLAY_DIR` | Overrides the CLI's local replay directory |

`.env.example` contains disposable local values. No `.env` file, signing key, bearer token, or
production credential belongs in the repository.

## REST boundary

`create_execution_app` requires a `ToolExecutor` and `Authenticator`. Optional inputs are a
`MutationService`, `GatewayObservability`, `PrometheusMetrics`, and `max_request_bytes`. The body
limit defaults to 1,048,576 and accepts values from 1 through 10,000,000 bytes.

The host deployment owns TLS termination, trusted proxy/header behavior, connection limits,
aggregate rate limits, and signing-key delivery.

## Outbound HTTP

`HTTPToolTarget` sets one URL, timeout, and response-byte limit. `OutboundHTTPPolicy` defaults to:

- HTTPS required;
- no hostname allowlist, but every resolved address must be public;
- loopback/private/link-local/reserved/transition addresses rejected;
- at most 32 DNS answers; and
- per-invocation direct IP pinning with original Host and TLS SNI.

Set `allowed_hosts=frozenset({...})` for exact host restriction. The
`require_https=False` and `allow_non_global_addresses=True` options exist for explicit trusted
development or private-network compositions; enabling them changes the security boundary. A
deployment-supplied HTTP proxy must enforce equivalent destination policy.

## Authentication and authorization

`JWTAuthenticator` receives deployment-owned verification keys, exact issuer and audience, and an
algorithm allowlist. `RuleBasedAuthorizationPolicy` is default-deny with deny precedence. No OIDC,
JWKS, key rotation service, policy database, or delegated-agent identity is auto-configured.

## Mutations and storage

Use PostgreSQL migrations `0001` through `0003`, then compose `PostgresMutationStateStore` and
`PostgresAuditStore`. Confirmation lifetime defaults to five minutes and is bounded from 30
seconds through 30 minutes. Runtime bindings remain process-local.

## Reliability

`ReliabilityControls` defaults to unlimited/no-op implementations. Explicitly inject rate limiter,
retry budget, and circuit breaker implementations for enforced behavior. Built-in implementations
are thread-safe and process-local, not distributed.

## Telemetry

OpenTelemetry uses the API only; the embedding service owns provider, sampler, processor, and
exporter configuration. `PrometheusMetrics` uses an isolated registry. The optional REST
`/metrics` endpoint is added only when metrics are supplied.
