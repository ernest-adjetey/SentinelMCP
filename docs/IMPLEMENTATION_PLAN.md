# Implementation Plan

## Delivery rules

Each phase ends with:

1. behavior and architecture tests appropriate to the phase;
2. formatter, lint, and strict type checks;
3. updated docs and an explicit list of known limitations;
4. a focused commit only after the checks pass;
5. no benchmark, reliability, or security claim without a retained artifact that supports it.

## Phase 1: Architecture and repository structure

**Deliverables**

- Modular package layout and ownership boundaries
- Architecture, decision, threat, failure, benchmark, and evaluation documents
- Dependency policy, contribution guide, security policy, and Apache-2.0 license
- Baseline Ruff, mypy, pytest, coverage, and GitHub Actions configuration
- Architecture tests that make the package map executable

**Exit criteria**

- Package imports on Python 3.12
- All baseline quality gates pass
- README accurately distinguishes implemented and planned behavior

## Phase 2: Tool registry and schemas

**Status: Complete**

**Deliverables**

- Strict typed identifiers and full SemVer 2.0.0 parsing
- Deeply immutable `ToolDefinition` values with operation class, input/output schemas, timeout,
  retry, idempotency, and capability metadata
- RFC 8785 canonicalization and deterministic SHA-256 definition fingerprints
- Persistence-independent `ToolRegistry` protocol and thread-safe in-memory implementation
- Draft 2020-12 schema and runtime value validation with complexity and reference limits
- Exact registration, lookup, existence, list, version list, and latest use cases

**Tests**

- Schema validity, local reference, remote reference, and complexity fixtures
- Idempotent duplicate, immutable conflict, version isolation, latest, and concurrent registration
- Property tests for canonicalization, deep immutability, registration, and runtime validation

At the Phase 2 boundary, endpoint bindings, authorization, and durable persistence were deferred;
Phases 3 through 6 now own those concerns. Activation state, sensitivity annotations, and
compatible-version execution remain outside the 0.1.0 contract.

## Phase 3: Gateway and execution orchestration

**Status: Complete**

**Deliverables**

- Immutable execution request, context, result, identifier, and error values
- Persistence-neutral runtime binding resolver and transport-neutral adapter protocol
- One transport-independent application executor using exact tool references
- Deterministic read-only synthetic tools registered through the Phase 2 registry
- Single-attempt HTTP adapter with bounded status-first JSON response handling
- FastAPI `POST /v1/execute` adapter and stable response envelopes

**Tests**

- Unit coverage for models, bindings, executor ordering, HTTP normalization, and safe failures
- Integrated registry, binding, executor, and synthetic-tool pipeline
- FastAPI success and structured-failure contract tests
- Properties proving invalid input never dispatches, invalid output never succeeds, and request
  arguments remain isolated from caller mutation

Subsequent phases added MCP, CLI execution, cancellation behavior, request-wide deadlines,
retries, circuit breaking, authentication, authorization, mutation confirmation, persistence,
and telemetry without changing this transport-independent execution boundary.

## Phase 4: Durable PostgreSQL tool registry

**Status: Complete**

**Deliverables**

- Synchronous SQLAlchemy Core and psycopg PostgreSQL registry behind `ToolRegistry`
- Alembic-managed immutable definition schema with exact-reference uniqueness
- Transactional idempotent/conflicting registration under cross-process races
- Canonical reconstruction, fingerprint verification, and deterministic integrity errors
- Docker Compose PostgreSQL 17 service with health check and environment configuration
- GitHub Actions PostgreSQL service with migrations before the complete suite

**Tests**

- Shared registry contract suite against in-memory and PostgreSQL implementations
- Real 32-client identical race, conflicting race, independent versions, and completed readers
- Migration up/down/up inspection, unavailable database, and corrupted row handling
- Independent-engine restart persistence and PostgreSQL-backed gateway execution

Subsequent phases added audit storage, authentication, authorization, mutation confirmation, and
process-local reliability controls. Runtime-binding persistence and Redis-backed coordination are
not part of 0.1.0.

## Phase 5: Authentication and authorization

**Status: Complete**

**Deliverables**

- Bounded signed-JWT verification with explicit key IDs and algorithm allowlist
- Normalized immutable principal containing subject, issuer, roles, and scopes
- Required executor authorization boundary after validation and before dispatch
- Default-deny exact tool-identity and operation-class policy with deny precedence
- Stable authentication and authorization failure categories at the REST boundary

**Tests**

- Deterministic Ed25519 authentication matrix for expiry, not-before, issuer, audience,
  signature, key ID, algorithm, malformed claims, and token bounds
- Policy allow, deny precedence, all-of role/scope conditions, and default deny
- Executor ordering and no-dispatch checks for missing identity, denial, and policy failure
- End-to-end gateway proof that unauthenticated and unauthorized calls never invoke adapters

Token issuance, OIDC/JWKS networking, revocation, delegated-agent identity, policy persistence,
and administrative registration/replay permissions remain deferred until a concrete deployment
or owning feature requires them.

## Phase 6: Mutation preview, confirmation, and idempotency

**Status: Complete**

**Deliverables**

- Canonical request digest and test vectors
- Persisted hashed, expiring, single-use confirmation challenges
- Preview and confirmed-execution APIs
- Persistent idempotency state machine and conflict handling

**Tests**

- Concurrent confirmation consumption
- Argument, identity, policy, and tool-version mismatch
- Concurrent identical and conflicting idempotency keys
- Lost-response and unknown-outcome behavior

## Phase 7: Reliability execution controls

**Status: Complete**

**Deliverables**

- Request-wide deadline budgets
- Exponential backoff with bounded full jitter and retry budgets
- Protocol-backed `CLOSED`, `OPEN`, and `HALF_OPEN` circuit breaker with bounded probes
- Safe upstream decoder and complete normalized error hierarchy

**Tests**

- Deterministic clock/randomness unit tests
- Threaded circuit transition, stale-generation, and probe-concurrency tests
- Retry matrix for reads and idempotent/non-idempotent mutations
- Malformed media type, body, status, encoding, and schema cases

The executable deployment is single-process, so the first implementation is thread-safe and
process-local. Redis was not added without a real cross-replica deployment requirement. Shared
implementations and multi-process service tests are required before claiming global rate, retry,
or circuit bounds. Mutations remain single-attempt because no downstream idempotency contract is
propagated.

## Phase 8: Observability

**Status: Complete**

**Deliverables**

- OpenTelemetry spans and context propagation
- Prometheus counters, histograms, and circuit gauges with bounded labels
- Structured redacted logs
- OTel Collector, Prometheus, and checked-in Grafana provisioning

**Tests**

- Span relationships and required attributes
- Metric label cardinality allowlist
- Secret-redaction regression suite
- Exporter outage behavior

## Phase 9: Replay and debugging CLI

**Status: Complete**

**Deliverables**

- Safe replay record schema and bounded argument reconstruction
- `sentinel` tools, execute, inspect, replay, eval, and benchmark commands
- Synthetic-target allowlist, dry-run mode, current reauthorization, and replay audit event
- Exact-contract drift detection and final binding guard

**Tests**

- Attempts to replay HTTP bindings, mutations, changed contracts, or stale permissions
- Binding replacement race with proof of zero outbound calls
- Strict file persistence, corruption, unavailable-store, and command tests

## Phase 10: Official MCP integration

**Status: Complete**

**Deliverables**

- Official current MCP Python SDK integration
- Tool discovery and invocation mapped to exact SentinelMCP contracts
- Stable MCP-to-core error translation without a second execution path

**Tests**

- SDK transport and protocol conformance
- Discovery and exact-version invocation
- Authentication, authorization, validation, and normalized failure parity

## Phase 11: Fault injection and evaluation framework

**Status: Complete**

**Deliverables**

- Deterministic synthetic fault profiles isolated from production configuration
- Versioned scenario schema, seeded runner, attribution model, and result artifacts
- Deterministic baseline dataset covering success and every failure category
- `sentinel eval run` command and generated report

**Tests**

- Numerator/denominator correctness
- Attribution exclusivity
- Mutation-safety negative evidence
- Scenario state isolation
- Startup refusal for fault injection in production mode

## Phase 12: Benchmark framework

**Status: Complete**

**Deliverables**

- Load generator and resource sampler
- Direct baseline, feature-toggle, failure, and concurrency workloads
- Machine-readable manifests, raw results, summaries, and chart generation
- `sentinel benchmark run` command

**Tests**

- Summary calculations against fixed datasets
- Percentile and error accounting
- Reproduction manifest completeness

No result enters the README until the full protocol in `BENCHMARKS.md` has run.

## Phase 13: Security, test, and release polish

**Status: Complete**

**Deliverables**

- SSRF-safe outbound target policy and focused adversarial tests
- Unit, API, integration, concurrency, fault, property, and mutation-safety test suites
- CI, packaging, dependency, migration, and license verification
- Deterministic evaluation of 23 reliability scenarios
- Final diagrams, screenshots, measured benchmark/evaluation results, and operator runbook
- API reference, configuration reference, release notes, and first tagged pre-release
- Verification that no proprietary names, data, credentials, or unsupported claims remain

## Cross-phase technical risks

| Risk | Mitigation and proof |
| --- | --- |
| False exactly-once claim | ADR 0003, unknown-outcome state, lost-response tests |
| Unsafe retry | Registry-declared semantics plus retry matrix tests |
| Confirmation replay/race | Durable atomic consume and concurrent tests |
| Circuit races across replicas | Process-local scope is explicit; a future shared implementation requires atomic multi-process tests |
| Leaking payloads | Payload-free audit/metrics, allowlisted traces, bounded log redaction, and regression tests |
| Telemetry overload | Bounded labels, sampling policy, exporter-failure tests |
| Replay bypasses policy | Reauthorization, synthetic allowlist, replay audit |
| Framework-driven architecture | Domain contracts remain framework-independent |
| Benchmark marketing bias | Raw artifacts, environment manifest, repeated trials |

## Strongest systems-engineering signals

1. Correct mutation semantics under concurrency, retries, and lost responses.
2. Shared three-state circuit breaker with bounded half-open probes.
3. Deadline and retry-budget propagation rather than per-attempt timeouts.
4. Error normalization that safely handles adversarial upstream responses.
5. Audit/replay design that balances debuggability with secret minimization.
6. Low-cardinality observability tied to deterministic failure injection.
7. Reproducible benchmarks that quantify the cost of each reliability feature.
