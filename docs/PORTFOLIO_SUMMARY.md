# Portfolio Summary

## SentinelMCP

Reliability, safety, security, and observability control plane for AI-agent tool execution.

SentinelMCP mediates REST and official MCP calls through one transport-independent lifecycle. It
is a personal open-source systems project and is not connected to, derived from, or deployed at an
employer.

## Architecture

```text
Agent / MCP Client
-> Authentication
-> Exact Versioned Contract
-> Input Validation
-> Authorization
-> Mutation Safety
-> Durable Idempotency
-> Reliability Controls
-> Tool Adapter
-> Output Validation
-> Audit / Metrics / Traces
```

Read calls bypass the mutation-only stages. PostgreSQL stores immutable contracts, confirmation
challenges, idempotency state, and audit events. Runtime bindings, rate limits, retry budgets, and
circuit state remain process-local in 0.1.x.

## Hard engineering decisions

- Exact SemVer contracts and RFC 8785 fingerprints reject silent schema drift and latest-version
  ambiguity.
- Operational bindings are separate from immutable tool contracts, keeping endpoints and
  credentials out of registry identity.
- Mutation confirmation binds principal, exact `ToolRef`, definition fingerprint, and canonical
  arguments before an atomic PostgreSQL consume-and-claim transaction.
- Ambiguous post-dispatch mutation failures become durable unknown outcomes instead of unsafe
  automatic retries or an exactly-once claim.
- Retries are limited to classified transient read failures and consume one request-wide deadline
  plus a separate retry budget.

## Authentication and authorization

REST authentication verifies bounded Ed25519 JWTs with an explicit algorithm allowlist, key ID,
issuer, audience, expiry, issued-at, and optional not-before checks. Only a normalized principal
reaches the executor. Authorization is default-deny, distinguishes read from mutation, supports
role/scope conditions, and gives explicit deny precedence.

## Mutation safety and idempotency

Preview performs zero adapter dispatches and returns a short-lived single-use confirmation.
Confirm revalidates the exact request and current authorization, atomically consumes the challenge,
and claims a principal/tool-scoped idempotency record. Completed duplicates recover the persisted
result; conflicts and pending or unknown states never blind-redispatch.

## Reliability, observability, and replay

The executor implements request-wide deadlines, bounded full-jitter read retries, retry budgets,
token-bucket admission, and a generation-safe three-state circuit breaker. Payload-free audit
events persist to PostgreSQL. OpenTelemetry spans, bounded structured logs, and Prometheus metrics
exclude request/response payloads and keep metric labels finite. Replay is synthetic-read-only by
default, fingerprint-checked, currently reauthorized, audited before dispatch, and protected by a
final binding guard.

## Official MCP integration

The official MCP Python SDK exposes reversible exact-version tool names over the same registry,
authorization, mutation, reliability, validation, audit, and telemetry services as REST. Trusted
in-memory and stdio composition requires an explicit principal; Streamable HTTP bridges the same
bearer authenticator and denies unconfigured callers.

## Evaluation and performance

- 23 of 23 deterministic fault scenarios matched expected attribution and dispatch behavior.
- The complete local gate passed 617 tests with 95.64% branch-aware coverage.
- A 26,000-sample local synthetic benchmark covered direct adapter, core, authenticated REST,
  official in-memory MCP, audit/telemetry, normalized failure, and PostgreSQL mutation paths.
- Profile-guided validator caching improved concurrency-1 core throughput from 624 to 5,051
  requests/s and reduced p95 from 2.443 ms to 0.251 ms.

The benchmark ran on Windows 11, Python 3.12.14, PostgreSQL 17.11, and a 22-logical-CPU Intel host.
It does not establish production capacity, external-network latency, or a service-level objective.

## Security

Outbound HTTP requires HTTPS by default, optionally enforces an exact host allowlist, validates
every DNS answer, rejects non-global and transition addresses, refuses redirects, and pins the
connection IP while preserving Host and TLS SNI. REST bodies are bounded before framework JSON
decoding. Schemas reject remote references and enforce structural limits. Public errors omit raw
upstream bodies, database URLs, exception details, stack traces, and local paths.

## Failures encountered

- Profiling showed repeated immutable JSON Schema compilation dominated the core path; a bounded
  cache removed the repeated work while preserving exact contract validation.
- The package builder initially emitted Core Metadata 2.5, which the selected Twine release did
  not accept; the build now explicitly emits supported Core Metadata 2.4.
- The first wheel smoke treated MCP's `ListToolsResult` wrapper as a list. The corrected gate reads
  its `.tools` collection and now runs in CI from an isolated wheel environment.
- Security review showed hostname validation alone was insufficient against DNS rebinding; the
  direct adapter now validates the complete answer set and connects to an approved pinned IP.

## Tradeoffs and limitations

- The project is a public alpha and reference control plane, not a hosted production deployment.
- Reliability coordination and runtime bindings are process-local.
- Mutation delivery is not universal exactly-once; a crash after an external effect can require
  operator reconciliation.
- Policy and signing keys are deployment configuration; OIDC/JWKS, revocation, tenant identity,
  and policy administration are not built in.
- Audit records are append-oriented but not cryptographically tamper-proof against a database
  administrator.

## Scaling discussion

At 10x traffic, first measure PostgreSQL pool pressure, audit write throughput, adapter saturation,
and telemetry export, then scale stateless gateway replicas. A shared atomic reliability backend is
required before claiming global rate or circuit bounds.

At 100x traffic, separate only measured bottlenecks. Plausible boundaries are durable audit
ingestion, evaluation workers, and shared reliability state, each with explicit queueing,
consistency, backpressure, and failure semantics. These are design paths, not implemented or
benchmarked claims.

## Interview talking points

- Why exact-version contracts and canonical fingerprints matter for agent tool safety.
- Why mutation confirmation and idempotency cannot create universal exactly-once effects.
- How deadline, retry-budget, rate-limit, and circuit-breaker state interact under failure.
- How payload-free audit differs from best-effort traces, logs, and metrics.
- Why MCP remains an ingress adapter instead of becoming a second execution engine.
- How profiling evidence justified one targeted cache and rejected speculative optimization.

## Technologies

Python 3.12, FastAPI, official MCP Python SDK, PostgreSQL, SQLAlchemy, Alembic, JSON Schema,
PyJWT/Ed25519, HTTPX, OpenTelemetry, Prometheus, Grafana, Docker Compose, Hypothesis, pytest, mypy,
and Ruff.

## Links

- Repository: <https://github.com/ernest-adjetey/SentinelMCP>
- Architecture: <https://github.com/ernest-adjetey/SentinelMCP/blob/main/docs/ARCHITECTURE.md>
- Evidence: <https://github.com/ernest-adjetey/SentinelMCP/blob/main/docs/RESUME_EVIDENCE.md>
