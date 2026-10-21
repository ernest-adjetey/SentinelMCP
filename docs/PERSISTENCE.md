# PostgreSQL Persistence

## Scope

Phase 4 makes immutable tool contracts durable without changing `ToolRegistry`; Phase 6 adds
durable mutation challenges and idempotency outcomes behind `MutationStateStore`; Phase 8 adds
payload-free audit events behind `AuditStore`. PostgreSQL adapters remain outside domain and
application contracts. In-memory implementations remain available for isolated tests and local
composition.

Runtime bindings are still process-local operational state. PostgreSQL stores what a tool
version means, while `InMemoryRuntimeBindingRegistry` decides where that exact version runs.
Redis is absent. PostgreSQL's transaction and uniqueness semantics provide the durable
confirmation and idempotency arbitration; no additional coordination store is justified yet.

## Storage model

Alembic revision `0001` creates `tool_definitions`:

| Column | Type | Purpose |
| --- | --- | --- |
| `namespace` | `varchar(191)` | Searchable exact namespace |
| `name` | `varchar(63)` | Searchable exact tool name |
| `version` | `varchar(128)` | Full SemVer text, including build metadata |
| `canonical_definition` | `bytea` | Exact RFC 8785 semantic definition bytes |
| `fingerprint` | `varchar(64)` | SHA-256 integrity and correlation value |
| `created_at` | `timestamptz` | Database-assigned registration time |

The named unique constraint `uq_tool_definitions_exact_ref` covers `(namespace, name,
version)`. The non-unique `ix_tool_definitions_identity` index covers `(namespace, name)` for
identity and version queries. Fingerprints are deliberately not unique: correctness cannot
assume hash collision impossibility.

Canonical content is stored as `bytea`, not `jsonb`, so PostgreSQL never rewrites the bytes
used for semantic equality. The separate identity columns support bounded queries and are
verified against reconstructed content on every definition read.

Alembic revision `0002` adds:

- `mutation_challenges`, keyed by challenge UUID with a unique token digest, exact principal
  and tool binding, definition and argument digests, expiry, and nullable consumption time;
- `mutation_idempotency`, keyed by record UUID with a unique principal/tool/caller-key scope,
  semantic request digest, request/execution identifiers, state, and bounded serialized outcome.

Revision `0003` extends mutation idempotency state with terminal `failed` for requests that are
known not to have dispatched, then creates `audit_events`. Audit rows contain a schema version,
finite kind and outcome, bounded principal identity, exact tool reference and fingerprint,
trusted request/execution IDs, operation class, finite error code, duration, and attempt count.
Database checks enforce allowed kind/outcome combinations and terminal shape; request and
execution indexes support deterministic ordered lookup.

Database checks permit only `pending`, `succeeded`, `failed`, and `outcome_unknown` mutation
states, and require an outcome exactly for terminal states. The raw confirmation token and raw
idempotency key are not represented in either mutation table. Audit rows intentionally exclude raw
arguments, results, credentials, tokens, arbitrary exceptions, and upstream bodies. Detailed claim
and crash semantics are in [Mutation safety](MUTATION_SAFETY.md), and the audit data contract is in
[Audit contract](AUDIT.md).

## Registration transaction

`PostgresToolRegistry` uses synchronous SQLAlchemy Core. The existing registry protocol and
executor are synchronous at the storage boundary, and each operation owns one short
connection or transaction. An `Engine` is injected; there is no hidden global session and the
caller owns engine disposal.

Registration uses PostgreSQL's default `READ COMMITTED` isolation:

1. Validate schemas, produce RFC 8785 bytes, and hash them before opening a transaction.
2. Run `INSERT ... ON CONFLICT ON CONSTRAINT uq_tool_definitions_exact_ref DO NOTHING`.
3. A returned row means this transaction registered the definition.
4. No returned row means the database unique constraint found the authoritative exact
   reference. Read that row in the same transaction.
5. Equal canonical bytes return `already_registered`; different bytes raise
   `ImmutableToolVersionConflict`.

PostgreSQL's unique-index arbitration waits for an in-flight conflicting inserter. Under
`READ COMMITTED`, the subsequent statement receives a fresh snapshot and can read the winner's
committed row. `DO NOTHING` avoids a failed transaction, rollback dance, or mutable upsert.
`ON CONFLICT DO UPDATE` is intentionally forbidden because exact versions are immutable.

If a commit succeeds but the client loses the response, retrying the same canonical definition
is safe: the retry returns `already_registered`. That is idempotent registration, not
exactly-once tool execution.

## Reconstruction and integrity

Reads decode UTF-8 JSON, require the exact semantic field set, rebuild the typed immutable
`ToolDefinition`, revalidate both schemas, and then verify:

- identity columns equal the reconstructed namespace, name, and full version;
- re-canonicalized bytes equal the persisted bytes; and
- SHA-256 of those bytes equals the persisted fingerprint.

Any violation raises `RegistryIntegrityError`. Connection and SQLAlchemy failures become
`RegistryUnavailableError` without chained driver errors, SQL, parameters, or connection
strings. Missing records and immutable conflicts retain their existing domain errors.

## SemVer and deterministic listing

SQL lexical ordering is not SemVer ordering: for example, `10.0.0` sorts before `2.0.0`
lexically, and prerelease precedence has additional rules. PostgreSQL retrieves candidate
version strings, and the existing `ToolVersion` implementation sorts them in application
code. Equal-precedence build variants use their exact version text as the established
deterministic tie-break. Tool identities use explicit SQL ordering by namespace and name;
no operation relies on physical row order.

## Migrations

Production paths never call `MetaData.create_all()`. Apply schema changes explicitly:

```bash
export SENTINELMCP_DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/sentinelmcp'
python -m alembic upgrade head
```

PowerShell uses `$env:SENTINELMCP_DATABASE_URL = '...'`. Revisions `0001` through `0003` support
downgrade to `base`; the migration test exercises fresh upgrade, schema inspection, downgrade,
and a second upgrade. The `0003` downgrade maps terminal `failed` mutation records to
`outcome_unknown` before restoring the earlier state constraint, preserving the fact that a
terminal record must not become retryable.

## Local PostgreSQL

The Compose service pins PostgreSQL major version 17 and includes a readiness health check.
Values in `.env.example` are disposable development credentials, not production secrets.

```bash
docker compose up -d postgres
cp .env.example .env
export SENTINELMCP_DATABASE_URL='postgresql+psycopg://sentinelmcp_test:sentinelmcp_dev_only@localhost:54329/sentinelmcp_test'
export SENTINELMCP_TEST_DATABASE_URL="$SENTINELMCP_DATABASE_URL"
python -m alembic upgrade head
pytest
```

When `SENTINELMCP_TEST_DATABASE_URL` is absent, locally collected PostgreSQL cases skip with an
explicit reason. CI always provides PostgreSQL, applies migrations first, and therefore runs
the contract, migration, restart, corruption, gateway, and real race tests. Production should
use TLS as required by its environment, a secrets manager or injected credentials, and a role
limited to the required schema and operations; the repository does not claim to configure
encryption at rest or production secret management.

## Concurrency proof

The real PostgreSQL suite covers 32 simultaneous identical registrations, two different
definitions racing for one exact reference, independent concurrent versions, readers after a
completed commit, and one durable row assertion. It also proves single-consumer mutation challenge
claims, single-dispatch idempotency arbitration, concurrent audit appends, restart persistence,
conflicting audit IDs, corruption detection, and unavailable-database translation. The same
registry behavioral contract suite runs against `InMemoryToolRegistry` and
`PostgresToolRegistry`.

```mermaid
sequenceDiagram
    participant A as Gateway A
    participant B as Gateway B
    participant P as PostgreSQL
    A->>P: INSERT exact reference DO NOTHING
    B->>P: INSERT exact reference DO NOTHING
    P-->>A: row returned (winner)
    P-->>B: no row returned after winner commits
    B->>P: SELECT authoritative row
    B->>B: compare canonical bytes
```

## Current limits

- Database access is synchronous and briefly occupies the calling thread. Reconsider an async
  adapter or explicit thread offload if measured gateway load shows event-loop contention.
- There is no update/delete operation in `ToolRegistry`; migration or administrative repair is
  separate from normal application behavior.
- Runtime bindings and policy state remain process-local or unimplemented. PostgreSQL audit records
  are durable, but the append-oriented application API is not cryptographic tamper protection and
  a privileged database administrator can alter rows.
- Mutation challenges and idempotency outcomes are durable, but there is no automated stale
  pending reconciliation or retention cleanup yet.
- Redis is absent. Retry budgets, rate limits, and circuit breakers are implemented but remain
  process-local and opt-in. OpenTelemetry, structured logs, Prometheus metrics, and local Grafana
  assets are implemented as best-effort diagnostics; trace delivery is not guaranteed and metrics
  can be lost before a scrape.
