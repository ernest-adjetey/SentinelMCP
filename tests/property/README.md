# Property Tests

Current generated tests cover canonical JSON stability, definition isolation from caller
mutation, registration idempotency, immutable conflict handling, version isolation, and
accepted/rejected runtime values. Later phases add redaction, retry-budget, and distributed
state-machine properties. Phase 3 also proves that generated invalid arguments never dispatch,
contract-invalid outputs never become success, and caller mutation cannot alter an execution
request after construction.
