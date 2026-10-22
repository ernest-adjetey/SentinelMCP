# SentinelMCP

**A reliability, safety, and observability control plane for AI agent tool calls.**

SentinelMCP is an open-source infrastructure project for mediating calls from AI agents to
external tools. It is designed to make tool execution explicit, policy-controlled,
observable, replayable, and resilient to dependency failures.

> **Project status:** Phases 1 through 11 are complete. SentinelMCP can durably register exact
> immutable tool versions in PostgreSQL and execute authenticated, authorized reads through
> synthetic or HTTP adapters behind FastAPI. Mutations use durable preview, single-use
> confirmation, and idempotency state. Reads support request-wide deadlines, bounded retries,
> process-local rate limiting, retry budgets, and three-state circuit breaking. This is not
> production-ready software.

## Why this exists

Agent frameworks make it easy to invoke a function. Production systems must answer harder
questions: Who authorized the call? Was it a read or a mutation? Can it be retried? Did the
upstream return valid data? How do we prevent a stale confirmation from changing different
arguments? What failed, and can the request be reproduced safely?

SentinelMCP provides one execution path where those concerns can be enforced consistently.
It does not decide what an agent should think. It controls what an agent is allowed to do.

## Target architecture

```mermaid
flowchart LR
    A[AI agent or developer] -->|MCP or REST| G[Gateway adapters]
    G --> O[Tool-call orchestrator]
    O --> I[Identity and policy]
    O --> R[Tool registry]
    O --> M[Mutation guard]
    O --> E[Execution engine]
    E --> T[Synthetic tool services]
    E --> C[Retry, deadline, circuit breaker]
    O --> P[(PostgreSQL)]
    O --> D[(Redis)]
    O --> OT[OpenTelemetry]
    O --> PM[Prometheus]
    OT --> OC[OTel Collector]
    PM --> GR[Grafana]
```

The gateway begins as a **modular monolith**. Protocol adapters share one application
orchestrator, while registry, policy, mutation safety, execution, audit, and telemetry remain
separate modules with explicit ownership. This avoids distributed transactions inside the
control plane before scale requires service extraction.

See [Architecture](docs/ARCHITECTURE.md) and
[Design decisions](docs/DESIGN_DECISIONS.md) for the detailed request lifecycle and tradeoffs.

## Implemented capabilities

- Typed namespaces, names, exact SemVer versions, identities, and tool references
- Deeply immutable tool definitions with read or mutation operation classes
- RFC 8785 canonical definition bytes and deterministic SHA-256 fingerprints
- Draft 2020-12 input/output schema checks with bounded complexity and no remote references
- Structured runtime argument and output validation errors
- Persistence-independent registry protocol and thread-safe in-memory implementation
- Migration-managed PostgreSQL registry with transactional immutable registration
- Real PostgreSQL concurrency, restart-persistence, migration, and corruption checks
- Exact lookup, deterministic listing, SemVer ordering, and latest-version resolution
- Idempotent identical registration and immutable conflict rejection
- Immutable execution requests, contexts, structured successes, and structured failures
- Exact-version runtime bindings kept separate from semantic tool definitions
- Deterministic synthetic tools and a single-attempt bounded HTTP adapter
- Status-first upstream classification, safe JSON decoding, and output-schema enforcement
- Minimal `POST /v1/execute` FastAPI gateway with stable success and error envelopes
- Bounded signed-JWT verification with explicit key IDs, algorithm allowlist, issuer,
  audience, signature, and time validation
- Immutable normalized principals and required default-deny authorization before dispatch
- Exact tool-identity and read/mutation policy with role/scope conditions and deny precedence
- PostgreSQL-backed mutation preview with hashed, expiring, exact-request-bound confirmations
- Atomic single-use challenge consumption and durable scoped idempotency records
- Terminal outcome recovery, conflict detection, fail-closed storage behavior, and real
  32-client race tests
- Request-wide monotonic deadlines with cancellation of overdue adapter attempts
- Read-only transient retries with capped exponential full jitter and aggregate retry budgets
- Thread-safe token-bucket admission and generation-safe three-state circuit breaking
- Stable local-rate-limit, circuit-open, and deadline-exhausted transport errors
- Versioned payload-free audit events with append-oriented PostgreSQL persistence and deterministic
  request/execution lookup
- Mutation pre-dispatch audit intent with fail-closed behavior and honest post-dispatch unknown
  outcomes
- OpenTelemetry API lifecycle spans, bounded structured JSON logs, and low-cardinality Prometheus
  metrics exposed through an optional `/metrics` endpoint
- Provisioned local Prometheus and Grafana configuration with a signal-focused dashboard
- Versioned bounded replay records with strict local append-only persistence and drift detection
- Synthetic-only replay by default with current reauthorization, pre-dispatch audit, dry run, and
  a final binding guard against target replacement
- JSON-emitting `sentinel` commands for tool discovery, execution, record inspection, replay,
  evaluation smoke checks, and bounded diagnostic benchmarks
- Official MCP Python SDK v2 discovery and invocation over the same registry, executor,
  authorization, reliability, audit, and telemetry lifecycle as REST
- Reversible exact-version MCP names, trusted in-memory/stdio principal injection, authenticated
  Streamable HTTP composition, and PostgreSQL-backed MCP mutation preview/confirmation
- Versioned deterministic fault scenarios for HTTP status, body, transport, retry, policy,
  duplicate-mutation, and ambiguous-mutation behavior
- Machine-readable evaluation artifacts with expected/observed error, attribution, dispatch count,
  labels, and per-scenario timing

The full behavioral contract and current limits are in
[Tool registry and schemas](docs/TOOL_REGISTRY.md) and
[Execution lifecycle](docs/EXECUTION_LIFECYCLE.md). Durable storage is documented in
[PostgreSQL persistence](docs/PERSISTENCE.md), and the security boundary is documented in
[Security model](docs/SECURITY_MODEL.md). Mutation delivery limits are documented in
[Mutation safety](docs/MUTATION_SAFETY.md).
[Reliability controls](docs/RELIABILITY.md) defines the retry matrix and deployment limits.
[Audit contract](docs/AUDIT.md) and [Observability](docs/OBSERVABILITY.md) define the evidence,
telemetry, redaction, and failure policies. [Safe replay](docs/REPLAY.md) and the
[CLI reference](docs/CLI.md) document operator workflows and their security limits.
[MCP integration](docs/MCP.md) documents discovery, exact-version encoding, transports,
authentication assumptions, mutation actions, errors, and cancellation. [Reliability
evaluation](docs/EVALUATION.md) defines the Phase 11 scenario and attribution contract.

## Planned capabilities

- Reproducible performance benchmarks and profiling

## CLI quick start

```bash
sentinel tools list
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}' --record
sentinel inspect RECORD_ID
sentinel replay RECORD_ID --dry-run
sentinel replay RECORD_ID
sentinel eval run --output phase11-evaluation.json
```

Replay defaults to the local `.sentinelmcp/replays` directory and only dispatches to explicitly
marked synthetic read tools after current authorization and audit checks.

## Repository map

```text
src/sentinelmcp/
  adapters/          MCP and REST transport boundaries
  application/       Tool-call orchestration use cases
  domain/            Transport- and storage-independent types and errors
  registry/          Tool definitions, versions, and execution policies
  policy/            Authentication context and authorization decisions
  mutation_guard/    Preview, confirmation, replay protection, idempotency
  execution/         Runtime bindings, adapter protocol, bounded upstream normalization
  audit/             Redaction and append-only audit events
  telemetry/         Traces, metrics, and structured logging
  replay/            Safe reconstruction against synthetic services
  evaluation/        Scenario execution and reliability attribution
  fault_injection/   Controlled failure plans for test environments
  cli/               Developer and operator commands
services/mock_tools/ Synthetic APIs used by demos and tests
tests/                Unit, architecture, API, integration, concurrency, property tests
deploy/               Local infrastructure and observability configuration
benchmarks/           Reproducible load generation and result processing
scenarios/            Deterministic demo and evaluation fixtures
```

## Development setup

SentinelMCP requires Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the quality gates:

```bash
ruff format --check .
ruff check .
mypy
pytest
```

The complete suite needs PostgreSQL 17. Start the pinned local service, load the development
test URL from `.env.example`, and apply migrations before `pytest`:

```bash
docker compose up -d postgres
export SENTINELMCP_DATABASE_URL='postgresql+psycopg://sentinelmcp_test:sentinelmcp_dev_only@localhost:54329/sentinelmcp_test'
export SENTINELMCP_TEST_DATABASE_URL="$SENTINELMCP_DATABASE_URL"
python -m alembic upgrade head
pytest
```

## Implementation roadmap

Work is intentionally incremental. Each phase has tests and exit criteria before the next
phase begins. The complete plan is in [Implementation plan](docs/IMPLEMENTATION_PLAN.md).

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Architecture, repository structure, quality baseline | Complete |
| 2 | Tool registry and schemas | Complete |
| 3 | Gateway and execution orchestration | Complete |
| 4 | Durable PostgreSQL tool registry | Complete |
| 5 | Authentication and authorization | Complete |
| 6 | Mutation confirmation and idempotency | Complete |
| 7 | Retries, deadlines, circuit breakers, error normalization | Complete |
| 8 | Durable audit, OpenTelemetry, Prometheus, Grafana | Complete |
| 9 | Replay and debugging CLI | Complete |
| 10 | Official MCP integration | Complete |
| 11 | Fault injection and reliability evaluation | Complete |
| 12 | Performance benchmarking and profiling | Planned |
| 13 | Release, portfolio, security, and documentation polish | Planned |

## Benchmarks

No benchmark results have been measured yet. The benchmark methodology and anti-bias rules
are defined in [Benchmarks](docs/BENCHMARKS.md). Results will be committed only with the raw
machine-readable output, environment metadata, and reproduction command.

## Security

This project is not yet ready for production use. Read the
[Threat model](docs/THREAT_MODEL.md) and [Security policy](SECURITY.md) before experimenting
with sensitive data. Synthetic services and non-secret fixtures are the only supported test
targets during development.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
