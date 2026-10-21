# PostgreSQL Persistence

## Scope

Phase 4 makes immutable tool contracts durable without changing `ToolRegistry`. PostgreSQL is
an adapter behind that protocol; `ToolExecutor` and the REST gateway continue to consume only
domain objects. The in-memory implementation remains available for isolated tests and local
composition.

Runtime bindings are still process-local operational state. PostgreSQL stores what a tool
version means, while `InMemoryRuntimeBindingRegistry` decides where that exact version runs.
Redis is absent because this phase has no distributed rate-limit, circuit-breaker, lease,
confirmation, or cache requirement.

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

PowerShell uses `$env:SENTINELMCP_DATABASE_URL = '...'`. Revision `0001` supports downgrade to
`base`; the migration test exercises fresh upgrade, schema inspection, downgrade, and a second
upgrade.

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
completed commit, and one durable row assertion. The same behavioral contract suite runs
against `InMemoryToolRegistry` and `PostgresToolRegistry`.

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
- Runtime bindings, audit records, policy state, confirmations, and execution state are not
  persisted in this phase.
- Redis, retries, circuit breakers, authentication, authorization, and telemetry remain later
  work.
