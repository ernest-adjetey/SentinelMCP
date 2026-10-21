# ADR 0006: PostgreSQL as Durable Tool Contract Store

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

The in-memory registry proves immutable registration inside one process with an `RLock`. It
cannot preserve definitions across restarts or coordinate gateway replicas. Phase 4 needs one
durable source of truth while preserving the storage-neutral `ToolRegistry` behavior from
Phase 2.

## Decision

Use PostgreSQL 17 through synchronous SQLAlchemy Core and psycopg 3. Manage schema exclusively
through Alembic. Store searchable exact identity columns, exact RFC 8785 canonical bytes, the
SHA-256 fingerprint, and database creation time.

Enforce `(namespace, name, version)` with a named unique constraint. Registration uses
`INSERT ... ON CONFLICT DO NOTHING`; a losing client reads the authoritative row and compares
canonical bytes. Identical content is idempotent, while different content raises
`ImmutableToolVersionConflict`. There is no update or delete method and no mutating upsert.

Use PostgreSQL's default `READ COMMITTED` isolation. Unique-index arbitration serializes races
for one exact reference, and the next statement's fresh snapshot sees the committed winner.
Transactions remain short and own their connections explicitly.

Compute `latest` in application code with the existing `ToolVersion` order. SQL lexical order
does not correctly implement numeric SemVer components, prerelease rules, or the established
build-metadata tie-break.

Keep runtime bindings in memory. Contract identity is durable semantic state; adapter routing
is operational state with a different lifecycle. Do not add Redis: no Phase 4 correctness
requirement needs a cache, lease, rate limiter, confirmation record, or circuit coordinator.

## Consequences

- Any number of gateway processes can safely race against the same uniqueness invariant.
- Retrying an identical registration after an ambiguous response is safe.
- Canonical bytes, rather than fingerprint equality alone, decide semantic identity; a
  theoretical hash collision cannot overwrite or equate different content.
- Reads detect malformed canonical content, identity disagreement, invalid schemas, and
  fingerprint mismatch as `RegistryIntegrityError`.
- The executor and gateway require no PostgreSQL-specific imports.
- Synchronous database calls may need thread isolation or an async registry adapter if future
  measurements show event-loop contention. That change must preserve this protocol and these
  transaction semantics.
- PostgreSQL availability is now required for the persistence lane in CI and for a complete
  local Phase 4 verification run.

## Alternatives considered

**Process locks:** rejected because they cannot coordinate separate processes or hosts.

**SELECT then INSERT:** rejected because two clients can both observe absence before either
insert, creating a check-then-act race.

**ON CONFLICT DO UPDATE:** rejected because replacing semantic content violates exact-version
immutability.

**Fingerprint uniqueness:** rejected because fingerprints are diagnostics and integrity
evidence, not collision-proof semantic identity.

**Redis:** rejected because its strengths do not match durable relational identity and
transactional uniqueness, and no current feature needs it.

**Async SQLAlchemy:** deferred because the established protocol is synchronous and operations
are short. It would add lifecycle and test complexity without evidence of a current bottleneck.
