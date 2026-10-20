# ADR 0001: Begin with a modular monolith

- Status: Accepted
- Date: 2026-10-20

## Context

The gateway's policy, confirmation, idempotency, execution, audit, and telemetry stages form a
single synchronous request lifecycle. Splitting each concern into a service at project start
would add network failure modes and distributed transactions before workload data exists.

## Decision

Deploy the SentinelMCP gateway as one process with enforced package boundaries. Keep synthetic
tool APIs separately deployable because they represent upstream trust boundaries. Define
interfaces at persistence and execution edges so modules can be extracted later.

## Alternatives considered

- **Microservices from the start:** rejected because operational cost and partial-failure
  behavior would dominate the actual safety logic.
- **Single undifferentiated package:** rejected because it makes later extraction and isolated
  testing difficult.
- **Serverless functions per stage:** rejected because request-wide deadline and transaction
  semantics become harder to reason about.

## Consequences

- Lower latency and simpler local transactions.
- A gateway failure affects all synchronous concerns, so process health and graceful shutdown
  matter.
- CPU-heavy work must not block the event loop.
- Module dependencies need architecture tests because runtime boundaries do not enforce them.

## Revisit when

Measured resource isolation, deployment cadence, or scaling requirements cannot be met by
horizontal gateway replicas and asynchronous audit/evaluation workers.
