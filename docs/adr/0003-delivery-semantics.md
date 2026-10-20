# ADR 0003: Claim idempotent acceptance, not universal exactly-once effects

- Status: Accepted
- Date: 2026-10-20

## Context

If an upstream mutation commits and the connection drops before a response arrives, the
gateway cannot infer whether the effect occurred unless the upstream supports idempotency or a
status query. Exactly-once execution cannot be guaranteed across arbitrary HTTP services.

## Decision

SentinelMCP guarantees one accepted mutation record per idempotency scope and argument digest.
It forwards the idempotency key only to tools whose contract declares support. Ambiguous
upstream outcomes are recorded as `OUTCOME_UNKNOWN`; they are not automatically retried unless
the target contract makes the retry safe.

## Alternatives considered

- **Always retry on connection failure:** rejected because it can duplicate mutations.
- **Never retry mutations:** safe but leaves recoverable operations unnecessarily unavailable.
- **Claim exactly once:** rejected as technically false without participation from the target.

## Consequences

- Tool metadata must declare mutation and idempotency behavior.
- Idempotency records need a request digest to reject key reuse with different arguments.
- Operators need an explicit reconciliation path for ambiguous outcomes.
- Tests must cover concurrent duplicates and lost-response scenarios.
