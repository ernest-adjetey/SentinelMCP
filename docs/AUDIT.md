# Audit Contract

## Scope

Phase 8 adds versioned, payload-free audit facts and an append-oriented PostgreSQL store. Audit
records answer who requested which exact registered tool version, which trusted execution IDs were
assigned, which finite lifecycle event occurred, and what normalized outcome was observed. They do
not record request arguments or response payloads.

Audit storage is evidence, not a transaction log for replay and not a cryptographic tamper-proof
ledger. A database administrator can alter or remove rows outside SentinelMCP. Deployments needing
WORM retention, signing, external notarization, or independent custody must add those controls.

## Versioned event

`AuditEvent` is immutable and currently uses schema version `1`. It contains only:

- event ID and timezone-aware recording time;
- finite event kind and outcome;
- bounded verified principal issuer and subject;
- exact namespace, tool name, and version;
- immutable definition fingerprint when resolution succeeded;
- trusted request and execution IDs;
- read or mutation operation class when known;
- finite normalized error code, duration, and attempt count for terminal events.

Event kinds are `execution_completed`, `mutation_previewed`, `mutation_dispatch_intent`,
`mutation_execution_completed`, and the reserved `replay_attempted`. Outcomes are finite:
`attempted`, `succeeded`, or `failed`. Strict serialization rejects missing, extra, malformed,
non-finite, oversized, or unsupported fields.

The schema has no place for raw arguments, raw results, bearer tokens, confirmation tokens,
idempotency keys, SQL text, stack traces, arbitrary exception messages, or upstream bodies. Those
values are excluded rather than stored and later redacted.

## Persistence

Alembic revision `0003` creates `audit_events` with check constraints for schema version, finite
enums, terminal shape, non-negative duration, and bounded attempt count. Request and execution
lookups are indexed and deterministically ordered by recording time and event ID.

`AuditStore` exposes only append and request/execution lookup. It has no ordinary update or delete
operation. An identical repeated event ID is idempotent; the same ID with different content is an
integrity error. The test-only cleanup helper is not part of the store protocol.

The PostgreSQL implementation translates connectivity and statement failures to
`AuditUnavailableError` and malformed persisted rows to `AuditIntegrityError`. Tests cover restart
persistence, concurrent distinct and identical appends, conflicting IDs, corrupted rows, ordered
lookup, and an unavailable database.

## Read policy

Read execution records one terminal `execution_completed` fact after the normalized result exists.
This includes admission and dispatch failures when a verified principal is available. If that append
fails, the original read result is preserved. The trace receives a finite audit-failure event and the
Prometheus audit-failure counter increments when telemetry is available.

This policy favors read availability. It means a successful read can be returned without a durable
audit row during an audit-store outage. Operators requiring mandatory read audit must place an
admission dependency before execution or change this policy explicitly.

## Mutation policy

Mutation audit is fail-closed before dispatch and honest about ambiguity after dispatch:

1. Preview persists the challenge, then appends `mutation_previewed` before returning the raw
   confirmation token. If audit append fails, no token is returned; the unreachable challenge
   remains until expiry.
2. Confirmation atomically wins a durable idempotency claim.
3. SentinelMCP appends `mutation_dispatch_intent` before calling the adapter. If this append fails,
   the adapter is not called and the idempotency row becomes terminal `failed` with
   `audit_unavailable`.
4. After dispatch, SentinelMCP appends `mutation_execution_completed` before terminal outcome
   persistence.
5. If the completion event cannot be appended, SentinelMCP cannot claim that the external effect
   failed. It returns and persists `mutation_outcome_unknown`.

The pre-dispatch intent proves only that SentinelMCP was ready to dispatch; it does not prove that an
external service received or committed the operation. Likewise, a missing completion event does not
prove that no effect occurred. Reconciliation remains an explicit later operator workflow.

## Operational limits

- Append-oriented application APIs do not prevent privileged database mutation.
- Audit writes are synchronous with the current request path and can add database latency.
- There is no retention, partitioning, archival, signing, or external export policy yet.
- Principal issuer and subject are intentionally retained audit identifiers; access to this table
  must follow the deployment's privacy and retention rules.
- Audit contains normalized metadata only and cannot reconstruct arbitrary request payloads.

See [Mutation safety](MUTATION_SAFETY.md), [Failure model](FAILURE_MODEL.md), and
[Observability](OBSERVABILITY.md) for adjacent behavior.
