# Test Strategy

- `unit/`: deterministic behavior without network or external state
- `architecture/`: package boundaries and dependency-direction checks
- `api/`: public REST and MCP contracts
- `contract/`: identical behavioral expectations for every storage implementation
- `integration/`: PostgreSQL migrations, durability, failures, and synthetic service boundaries
- `concurrency/`: race conditions, leases, idempotency, and cancellation
- `property/`: generated schema, canonicalization, and state-machine cases

Phase 4 runs the registry contract against memory and PostgreSQL and uses real PostgreSQL for
migration, restart, corruption, failure, gateway, and race tests. Set
`SENTINELMCP_TEST_DATABASE_URL` to run that lane. PostgreSQL cases skip with an explicit reason
when the variable is absent locally; CI always provides it and runs the complete suite.
