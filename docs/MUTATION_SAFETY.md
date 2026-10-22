# Mutation Safety

## Scope

Phase 6 adds a durable two-step path for registered mutation tools. A client first previews an
exact request and then confirms that same request with an opaque token and an idempotency key.
Ordinary `ToolExecutor.execute` calls still reject mutations, so every supported mutation must
pass through `MutationService`.

This design prevents confirmation replay and duplicate dispatch inside one PostgreSQL-backed
SentinelMCP deployment. It does not claim exactly-once effects in an arbitrary external system.

## Preview

`POST /v1/mutations/preview` uses the shared executor preparation stages to:

1. resolve the exact immutable `ToolRef`;
2. validate and freeze arguments;
3. authorize the principal for that exact tool identity and mutation operation;
4. compute the definition fingerprint and RFC 8785 argument digest; and
5. persist a challenge before returning its raw token.

Preview does not resolve a runtime binding and cannot invoke an adapter. The response contains
the exact normalized arguments, one generic mutation warning, a challenge ID, the definition
fingerprint, expiry, and the raw confirmation token.

The token is generated with `secrets.token_urlsafe(32)`. Only its 32-byte SHA-256 digest is
stored. The raw token exists at the caller boundary and is never a database column.

## Confirmation binding

Each challenge is bound to:

- verified principal issuer and subject;
- exact namespace, name, and version;
- immutable definition fingerprint;
- SHA-256 digest of RFC 8785 canonical arguments; and
- an absolute expiry timestamp.

Confirmation re-runs definition lookup, input validation, and current authorization. A changed
principal, exact version, definition fingerprint, or semantic argument value fails with the
same non-diagnostic `mutation_confirmation_invalid` response as an unknown, expired, or already
consumed token. The default lifetime is five minutes and construction permits only 30 seconds
through 30 minutes.

## Atomic claim transaction

Alembic revision `0002` creates `mutation_challenges` and `mutation_idempotency`; revision `0003`
adds the terminal `failed` state used when required pre-dispatch audit cannot be persisted. Confirmation
uses one PostgreSQL transaction:

1. insert a `pending` idempotency record with `ON CONFLICT DO NOTHING` on the scoped key;
2. if this request owns the new record, conditionally mark the matching active challenge as
   consumed; and
3. commit both changes together.

If challenge consumption fails, the exception rolls the transaction back, including the new
idempotency row. Concurrent consumers of one token therefore produce one committed claim and
no orphan records from losing transactions.

The idempotency scope is principal issuer, principal subject, exact tool reference, and caller
key. Its request digest also binds the operation class, definition fingerprint, and canonical
arguments. Reusing the scope with a different digest is a deterministic
`idempotency_conflict`. Reusing it with the same digest returns the authoritative pending or
terminal state without dispatching again.

## Durable states

| State | Meaning | Duplicate behavior |
| --- | --- | --- |
| `pending` | SentinelMCP accepted the claim but has no durable terminal outcome | Return `idempotency_in_progress`; never auto-redrive |
| `failed` | A required pre-dispatch control failed and no adapter call occurred | Return the original normalized failure; never dispatch |
| `succeeded` | A schema-valid success was durably serialized | Return the original result and identifiers |
| `outcome_unknown` | Dispatch failed, returned a failure, or terminal persistence could not be proven | Return the stored failure when available; never auto-redrive |

Terminal outcomes contain only normalized execution context, validated result data, or bounded
safe error fields. They are strict UTF-8 JSON, reject non-finite values, and are capped at
10,000,000 bytes.

## Delivery boundary

SentinelMCP dispatches once after winning the durable claim. A process crash before dispatch can
leave `pending` even though no external effect happened. A crash after dispatch but before
terminal persistence can leave the same state after an external effect happened. Those cases
are deliberately indistinguishable without cooperation from the target system.

Phase 6 does not automatically retry or reclaim `pending` mutations and does not propagate the
gateway idempotency key to adapters. Operators must reconcile ambiguous records against a
target-specific status or idempotency API before any manual action. Reliability work may add
safe downstream propagation only where the registered contract and adapter explicitly support
it.

This is at-most-one automatic dispatch per accepted local idempotency record, not universal
exactly-once delivery.

The audit boundary strengthens but does not eliminate this ambiguity. SentinelMCP appends a
payload-free dispatch intent before invoking the adapter. Failure to append that intent marks the
claim `failed` and prevents dispatch. After dispatch it appends a normalized completion event;
failure to append that event is persisted and returned as `mutation_outcome_unknown`, never as a
claim that the mutation failed safely. See [Audit contract](AUDIT.md).

## Failure behavior

- PostgreSQL failure before the claim commits returns `mutation_state_unavailable` and no
  adapter is invoked.
- Invalid, expired, consumed, or mismatched confirmation returns
  `mutation_confirmation_invalid` and no adapter is invoked.
- A same-scope different request returns `idempotency_conflict` and no adapter is invoked.
- A concurrent pending duplicate returns `idempotency_in_progress` and no adapter is invoked.
- Failure to persist pre-dispatch audit intent returns `audit_unavailable`, durably marks the claim
  `failed`, and invokes no adapter.
- Failure to persist completion audit after dispatch returns `mutation_outcome_unknown`.
- Failure to persist a terminal outcome after dispatch returns `mutation_outcome_unknown`.

Public errors collapse token-rejection reasons and never include the token, arguments, SQL,
driver errors, or exception text.

## Evidence

The test suite uses real PostgreSQL for migration, restart recovery, token persistence,
rollback, expiry, binding, conflict, completion, and race behavior. Two 32-client tests prove
that one shared confirmation admits one durable claim and that identical idempotency claims
produce one adapter invocation with one durable record. API tests cover preview, confirmation,
terminal reuse, consumed-token rejection, principal/argument binding, and conflict handling.

Run the complete lane with `SENTINELMCP_TEST_DATABASE_URL` configured and `pytest`. Focused cases
live in `tests/integration/test_postgres_mutation_store.py`,
`tests/concurrency/test_mutation_state_races.py`,
`tests/concurrency/test_mutation_service_races.py`, and `tests/api/test_mutation_gateway.py`.

## MCP mutation actions

MCP exposes the same exact mutation as explicit `preview` and `confirm` actions under one
exact-version tool name. The wrapper is transport syntax only: `MutationService` remains the sole
owner of PostgreSQL challenges, principal/argument/version binding, single-use consumption,
idempotency arbitration, dispatch, audit intent, and unknown outcomes. Focused official-SDK tests
in `tests/mcp/test_mcp_mutations.py` cover expiry, wrong principal and arguments, unknown versions,
replay, conflicts, concurrent confirmation, authorization denial, and post-dispatch audit failure.
