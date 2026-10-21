# Design Decisions

Architecture decisions are recorded before implementation when they affect compatibility,
trust boundaries, consistency, or operations. A decision may be superseded, but its history is
not rewritten.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](adr/0001-modular-monolith.md) | Begin with a modular monolith | Accepted |
| [0002](adr/0002-persisted-confirmations.md) | Persist opaque, single-use mutation confirmations | Accepted |
| [0003](adr/0003-delivery-semantics.md) | Claim idempotent acceptance, not universal exactly-once effects | Accepted |
| [0004](adr/0004-immutable-versioned-tool-contracts.md) | Make exact tool versions immutable and content-addressable | Accepted |
| [0005](adr/0005-contract-identity-runtime-binding.md) | Separate contract identity from runtime binding | Accepted |
| [0006](adr/0006-postgresql-durable-tool-contract-store.md) | Use PostgreSQL for durable immutable tool contracts | Accepted |
| [0007](adr/0007-signed-jwt-and-exact-policy.md) | Verify signed JWTs and require exact default-deny authorization | Accepted |

## Decision checklist

Every future ADR should answer:

1. Which invariant or operational problem requires a decision?
2. What alternatives were considered?
3. What fails, and what is the recovery path?
4. What changes at 10x traffic?
5. Which new security risk is introduced?
6. How will the decision be tested and observed?
