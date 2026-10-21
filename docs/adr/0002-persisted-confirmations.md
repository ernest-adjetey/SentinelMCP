# ADR 0002: Persist opaque, single-use mutation confirmations

- Status: Accepted
- Date: 2026-10-20

## Context

A stateless signed token can prove integrity and expiry, but it cannot prevent replay without
additional state. Mutation confirmation requires exact argument binding, expiry, revocation,
and one-time consumption across concurrent gateway replicas.

## Decision

Generate a cryptographically random opaque token and store only its hash in PostgreSQL. Bind
the record to verified principal issuer and subject, exact immutable tool version, definition
fingerprint, canonical argument digest, and expiry. Atomically transition the record from
active to consumed in the same transaction that claims the mutation's idempotency record.

## Alternatives considered

- **Signed JWT only:** rejected because replay prevention and revocation still require state.
- **Redis-only token:** rejected because durable safety evidence must survive cache loss.
- **Reusable approval session:** rejected because it weakens exact-request binding.

## Consequences

- Confirmation requires a durable database round trip.
- Database unavailability blocks confirmed mutations, which is the intended fail-closed mode.
- Canonical JSON behavior becomes part of the compatibility contract and requires test vectors.
- Raw confirmation tokens must never be logged or persisted.
- Current authorization is re-evaluated at confirmation; policy configuration itself is not
  versioned or persisted in this phase.
