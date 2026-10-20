# Test Strategy

- `unit/`: deterministic behavior without network or external state
- `architecture/`: package boundaries and dependency-direction checks
- `api/`: public REST and MCP contracts
- `integration/`: PostgreSQL, Redis, and synthetic service boundaries
- `concurrency/`: race conditions, leases, idempotency, and cancellation
- `property/`: generated schema, canonicalization, and state-machine cases

Only unit and architecture tests exist in Phase 1. Additional suites are added with the phase
that introduces their behavior.
