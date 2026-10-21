# Test Strategy

- `unit/`: deterministic behavior without network or external state
- `architecture/`: package boundaries and dependency-direction checks
- `api/`: public REST and MCP contracts
- `contract/`: identical behavioral expectations for every storage implementation
- `integration/`: PostgreSQL migrations, durability, failures, and synthetic service boundaries
- `concurrency/`: race conditions, leases, idempotency, and cancellation
- `property/`: generated schema, canonicalization, and state-machine cases

Phases 4 and 6 use real PostgreSQL for registry and mutation migration, restart, corruption,
failure, gateway, rollback, and race tests. Set
`SENTINELMCP_TEST_DATABASE_URL` to run that lane. PostgreSQL cases skip with an explicit reason
when the variable is absent locally; CI always provides it and runs the complete suite.

Phase 7 adds deterministic clock/randomness tests for request-wide deadlines, full-jitter retries,
retry budgets, rate limiting, and circuit transitions. The concurrency lane uses 32 workers to
prove exact local token capacity and bounded half-open admission. These are thread/process-local
tests and do not claim distributed coordination.
