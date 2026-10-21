# API Tests

Phase 3 exercises the FastAPI `/v1/execute` contract through HTTPX `ASGITransport`, including
success, exact-version not found, input validation, missing binding, malformed envelopes,
internally generated IDs, and hidden unexpected exceptions. MCP parity is deferred until an MCP
boundary is implemented.
