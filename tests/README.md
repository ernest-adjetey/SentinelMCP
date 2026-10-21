# Test Strategy

- `unit/`: deterministic behavior without network or external state
- `architecture/`: package boundaries and dependency-direction checks
- `api/`: public REST and MCP contracts
- `integration/`: PostgreSQL, Redis, and synthetic service boundaries
- `concurrency/`: race conditions, leases, idempotency, and cancellation
- `property/`: generated schema, canonicalization, and state-machine cases

Phase 2 includes unit, architecture, property, and process-local concurrent registration
coverage. API and external-state integration suites remain empty until their owning phases.
