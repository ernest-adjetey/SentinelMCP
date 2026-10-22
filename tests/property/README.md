# Property Tests

Current generated tests cover canonical JSON stability, definition isolation from caller
mutation, registration idempotency, immutable conflict handling, version isolation, and
accepted/rejected runtime values. Reliability properties cover jitter bounds, retryability,
token-bucket capacity, and circuit admission; deterministic unit and concurrency suites cover
redaction and state-machine races. Generated invalid arguments never dispatch, contract-invalid
outputs never become success, and caller mutation cannot alter an execution request after
construction. Distributed state-machine properties remain out of scope while the built-in
reliability controls are process-local.
