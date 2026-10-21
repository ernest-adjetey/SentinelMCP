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

Endpoint bindings, activation state, authorization metadata, sensitivity annotations, compatible
version ranges, and durable persistence remain deferred. They require the security and storage
semantics introduced in later phases.

## Phase 3: Gateway and execution orchestration

**Deliverables**

- FastAPI REST adapter and MCP adapter using one application orchestrator
- Request context, stable response envelope, request-size limits, and deadline parsing
- HTTP execution port with deterministic fake implementation
- Initial CLI for tool discovery and calls

**Tests**

- REST/MCP contract parity
- API validation and cancellation behavior
- No upstream call after pre-execution rejection

## Phase 4: PostgreSQL and Redis

**Deliverables**

- SQLAlchemy async persistence, Alembic migrations, and asyncpg
- PostgreSQL registry and audit repositories
- Redis client lifecycle and namespaced key conventions
- Docker Compose for local stateful dependencies with health checks

**Tests**

- Migration up/down verification on disposable databases
- Repository contract suites against in-memory and PostgreSQL implementations
- Connection loss, pool exhaustion, and transaction rollback cases

## Phase 5: Authentication and authorization

**Deliverables**

- Validated identity context with issuer/audience/expiry/algorithm checks
- RBAC policy model with explicit actor, agent, tool, and operation permissions
- Versioned policy decisions and denial reason codes
- Administrative separation for tool registration and replay

**Tests**

- Authentication and authorization matrix
- Confused-deputy and claim-tampering cases
- Expired, malformed, and wrong-audience credentials

## Phase 6: Mutation preview, confirmation, and idempotency

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

**Deliverables**

- Request-wide deadline budgets
- Exponential backoff with bounded full jitter and retry budgets
- Redis-backed `CLOSED`, `OPEN`, and `HALF_OPEN` circuit breaker with probe leases
- Safe upstream decoder and complete normalized error hierarchy

**Tests**

- Deterministic clock/randomness unit tests
- Multi-process circuit transition and probe-concurrency tests
- Retry matrix for reads and idempotent/non-idempotent mutations
- Malformed media type, body, status, encoding, and schema cases

## Phase 8: Observability

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

## Phase 9: Synthetic tools and fault injection

**Deliverables**

- Asset catalog, ownership, metadata, notification, and admin mutation endpoints
- Deterministic fault profiles for status codes, malformed bodies, latency, dropped connections,
  duplicates, and partial outages
- Startup guard preventing fault injection in production mode

**Tests**

- Synthetic service contract tests
- End-to-end classification for every fault profile
- Fault-plan isolation under concurrent requests

## Phase 10: Replay

**Deliverables**

- Safe replay record schema and redacted argument reconstruction
- `sentinel replay` and `sentinel inspect` commands
- Synthetic-target allowlist, dry-run mode, reauthorization, and replay audit event

**Tests**

- Attempts to replay to arbitrary endpoints or with stale permissions
- Deterministic result reproduction
- Missing/redacted required field behavior

## Phase 11: Evaluation framework

**Deliverables**

- Versioned scenario schema, seeded runner, attribution model, and result artifacts
- Deterministic baseline dataset covering success and every failure category
- `sentinel eval run` command and generated report

**Tests**

- Numerator/denominator correctness
- Attribution exclusivity
- Mutation-safety negative evidence
- Scenario state isolation

## Phase 12: Benchmark framework

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

## Phase 13: Optional React dashboard

Build only if operator workflows are awkward in the CLI and API. The dashboard may visualize
registry state, traces, circuit health, and redacted audit records; it must not become a second
policy or execution implementation.

**Decision gate:** documented usability need, stable API, and no impact on core milestones.

## Phase 14: Test and CI hardening

**Deliverables**

- Unit, API, integration, concurrency, fault, property, and mutation-safety test suites
- Coverage policy based on risk, not line count alone
- GitHub Actions jobs for lint, types, unit/integration tests, migration checks, Docker builds,
  image scanning, and coverage artifacts
- Repeated flaky/concurrency test lane

## Phase 15: Documentation and release polish

**Deliverables**

- Deterministic demo of the twelve required reliability scenarios
- Final diagrams, screenshots, measured benchmark/evaluation results, and operator runbook
- API reference, configuration reference, release notes, and first tagged pre-release
- Verification that no proprietary names, data, credentials, or unsupported claims remain

## Cross-phase technical risks

| Risk | Mitigation and proof |
| --- | --- |
| False exactly-once claim | ADR 0003, unknown-outcome state, lost-response tests |
| Unsafe retry | Registry-declared semantics plus retry matrix tests |
| Confirmation replay/race | Durable atomic consume and concurrent tests |
| Circuit races across replicas | Redis state machine and multi-process tests |
| Leaking payloads | Schema sensitivity annotations and redaction regression corpus |
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
