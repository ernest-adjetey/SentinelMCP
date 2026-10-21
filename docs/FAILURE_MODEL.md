# Failure Model

## Principle

SentinelMCP exposes failures as stable categories with machine-readable details and retry
guidance. It preserves the distinction between caller, policy, infrastructure, and tool
failures. The gateway must not convert every failure into HTTP 500 or leak raw upstream data.

## Error taxonomy

| Code | Meaning | Default retry guidance |
| --- | --- | --- |
| `AUTHENTICATION_ERROR` | Credentials are missing or invalid | Do not retry unchanged |
| `AUTHORIZATION_ERROR` | Identity lacks permission | Do not retry unchanged |
| `VALIDATION_ERROR` | Request or arguments violate the contract | Do not retry unchanged |
| `RATE_LIMIT_ERROR` | Trusted rate-limit scope is exhausted | Retry after server guidance |
| `TOOL_NOT_FOUND` | Tool name or version is unknown/inactive | Do not retry unchanged |
| `CONFIRMATION_REQUIRED` | Mutation needs preview and confirmation | Follow mutation workflow |
| `CONFIRMATION_EXPIRED` | Challenge is expired, consumed, or mismatched | Generate a new preview |
| `IDEMPOTENCY_CONFLICT` | Key was reused for different arguments | Use the original request or a new key |
| `UPSTREAM_AUTHENTICATION_ERROR` | Tool rejected SentinelMCP credentials | Operator action; no caller retry |
| `UPSTREAM_AUTHORIZATION_ERROR` | Tool denied the operation | Operator/policy action |
| `UPSTREAM_RATE_LIMITED` | Tool returned a recognized throttle response | Retry only within budget |
| `UPSTREAM_TIMEOUT` | Deadline elapsed before a valid response | Reads may retry within budget |
| `UPSTREAM_UNAVAILABLE` | Connection or transient 5xx failure | Policy-dependent bounded retry |
| `UPSTREAM_MALFORMED_RESPONSE` | Status/body/content type/schema is invalid | Do not retry by default |
| `CIRCUIT_OPEN` | Calls are blocked during dependency cooldown | Retry after cooldown guidance |
| `OUTCOME_UNKNOWN` | Mutation may have committed but no outcome is known | Never blind-retry |
| `AUDIT_UNAVAILABLE` | Required audit persistence failed | Mutations fail closed |
| `INTERNAL_ERROR` | Unclassified gateway defect | Retry only if documented safe |

The public error envelope will include `code`, `message`, `request_id`, optional `retry_after`,
and bounded structured details. Internal causes and stack traces remain in redacted telemetry.

### Implemented registry errors

Phase 2 exposes typed Python exceptions before a transport error envelope exists:

| Exception | Stable code | Structured context |
| --- | --- | --- |
| `InvalidToolReferenceError` | `invalid_tool_reference` | Component, rejected value, reason |
| `InvalidToolDefinitionError` | `invalid_tool_definition` | Field and bounded reason |
| `InvalidSchemaError` | `invalid_schema` | Input/output kind and validation issues |
| `ToolNotFoundError` | `tool_not_found` | Typed reference or identity |
| `ImmutableToolVersionConflict` | `immutable_tool_version_conflict` | Exact ref and both fingerprints |
| `ArgumentValidationError` | `argument_validation_failed` | Exact ref and bounded issues |
| `OutputValidationError` | `output_validation_failed` | Exact ref and bounded issues |

Each validation issue contains an instance path, schema path, generic message, and validator
keyword. Messages do not include raw argument or output values. `ToolAlreadyExistsError` is
available for adapters that need it, while the in-memory registry reports identical exact
registration as the successful `already_registered` status.

### Implemented execution errors

Phase 3 returns immutable successes or failures rather than raising transport exceptions through
the gateway. Execution failures carry a safe message, exact reference, request/execution IDs,
optional upstream status, and bounded validation issues. Categories distinguish lookup, input,
binding, adapter, upstream status, timeout/connection, media type, malformed/oversized body,
output-schema, blocked mutation, and unexpected internal failures.

Raw response bodies, credentials, exception representations, and stack traces are never fields
on the public error. A non-success HTTP status is classified before body decoding, so a
plain-text `401` remains an upstream authentication failure.

### Implemented mutation errors

Phase 6 adds `mutation_confirmation_invalid`, `idempotency_conflict`,
`idempotency_in_progress`, `mutation_state_unavailable`, and `mutation_outcome_unknown`.
Challenge rejection reasons are intentionally collapsed at the public boundary. State-store
failure before durable claim prevents dispatch; terminal-persistence failure after dispatch is
reported as unknown outcome because the external effect cannot be rolled back or inferred.

## Upstream decoding order

The executor processes a response in this order:

1. Read status and bounded headers.
2. Enforce maximum response bytes while streaming.
3. Classify status before interpreting a body.
4. Validate media type against the tool contract.
5. Decode bytes with the declared character set policy.
6. Parse JSON with bounded depth and explicit failure handling.
7. Validate the response schema.
8. Convert to a domain result or normalized error.

At no point does it assume that a successful HTTP status implies valid JSON.

## Retry matrix

Phase 3 performs exactly one adapter attempt and never retries. This matrix is the planned Phase
7 policy and is not executable behavior yet.

| Operation | Failure | Automatic retry? |
| --- | --- | --- |
| Read | Connect failure, selected 5xx, 429 | Yes, if attempts and deadline remain |
| Read | 401, 403, validation, malformed response | No |
| Mutation without idempotency support | Any dispatched request failure | No |
| Mutation with idempotency support | Pre-dispatch connect failure | Policy-dependent |
| Mutation with idempotency support | Lost response after dispatch | Only if upstream contract guarantees key semantics |
| Any | Circuit open or local validation failure | No upstream call |

Backoff uses exponential growth with bounded full jitter. The retry policy stores maximum
attempts, retryable categories, base delay, and cap; the request deadline is always the final
authority.

## Circuit breaker

The planned circuit breaker has three states:

- **CLOSED:** calls flow and classified outcomes update a rolling failure window.
- **OPEN:** calls fail fast until the cooldown timestamp.
- **HALF_OPEN:** a distributed lease admits a bounded probe set; success closes the circuit and
  failure reopens it.

Only dependency-health failures affect the breaker. Caller validation errors, authorization
rejections, and expected not-found results do not. State transitions emit audit-neutral
telemetry and low-cardinality metrics.

## Ambiguous mutation outcomes

If a connection drops after dispatch, SentinelMCP records `outcome_unknown` when it can persist
the normalized failure. If the process or database fails before terminal persistence, the
record remains `pending`. A duplicate caller receives the durable state rather than triggering
a new mutation. Reconciliation is an explicit operator action; an audited reconciliation
command remains later work.

## Storage degradation

| Dependency | Reads | Mutations |
| --- | --- | --- |
| PostgreSQL unavailable | Fail if registry/audit data cannot be served safely | Fail closed |
| Redis unavailable | Configurable degraded read policy; local fallback is not correctness-equivalent | Fail closed when rate/circuit coordination is required |
| Trace exporter unavailable | Continue and record exporter health locally | Continue if audit is durable |
| Prometheus scrape unavailable | Execution continues | Execution continues |

These policies will be executable configuration with tests, not implicit exception behavior.

## Shutdown and cancellation

On shutdown, stop admitting new requests, allow in-flight requests a bounded drain period,
flush required audit writes, and then cancel remaining work. Cancellation does not prove that
an upstream mutation did not occur. Such requests follow the ambiguous-outcome rule.
