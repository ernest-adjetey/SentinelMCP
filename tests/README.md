# Test Strategy

- `unit/`: deterministic behavior without network or external state
- `architecture/`: package boundaries and dependency-direction checks
- `api/`: public REST and MCP contracts
- `integration/`: PostgreSQL, Redis, and synthetic service boundaries
- `concurrency/`: race conditions, leases, idempotency, and cancellation
- `property/`: generated schema, canonicalization, and state-machine cases

Phase 3 adds unit coverage for execution models, bindings, adapters, and orchestration; an
integrated registry-to-synthetic-tool pipeline; FastAPI contract tests through HTTPX ASGI
transport; and generated execution invariants. External-state integration remains empty until
persistence is introduced.
