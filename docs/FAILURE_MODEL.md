# Failure Model

## Principle

SentinelMCP exposes failures as stable categories with machine-readable details and retry
guidance. It preserves the distinction between caller, policy, infrastructure, and tool
failures. The gateway must not convert every failure into HTTP 500 or leak raw upstream data.

## Error taxonomy

| Code | Meaning | Default retry guidance |
| --- | --- | --- |
| `authentication_failed` | Credentials are missing or invalid | Do not retry unchanged |
| `authorization_failed` | Identity lacks permission | Do not retry unchanged |
| `input_validation_failed` | Arguments violate the exact contract | Do not retry unchanged |
| `rate_limited` | Trusted rate-limit scope is exhausted | Retry after server guidance |
| `tool_not_found` | Exact tool name or version is unknown | Do not retry unchanged |
| `mutation_not_supported` | Mutation used the ordinary read path | Follow preview/confirm workflow |
| `mutation_confirmation_invalid` | Challenge is missing, expired, consumed, or mismatched | Generate a new preview |
| `idempotency_conflict` | Key was reused for a different semantic request | Use the original request or a new key |
| `upstream_authentication_failed` | Tool rejected SentinelMCP credentials | Operator action; no caller retry |
| `upstream_authorization_failed` | Tool denied the operation | Operator/policy action |
| `upstream_rate_limited` | Tool returned a recognized throttle response | Retry only within budget |
| `upstream_timeout` | Upstream attempt timed out | Reads may retry within the request deadline |
| `upstream_unavailable` | Connection or selected transient 5xx failure | Policy-dependent bounded retry |
| `upstream_malformed_response` | Successful response is invalid UTF-8 or JSON | Do not retry by default |
| `outbound_target_rejected` | URL, host, or resolved address violates outbound policy | Correct trusted configuration; do not retry unchanged |
| `circuit_open` | Calls are blocked during dependency cooldown | Retry after cooldown guidance |
| `mutation_outcome_unknown` | Mutation may have committed but no durable outcome is known | Never blind-retry |
| `audit_unavailable` | Required audit persistence failed | Mutations fail closed |
| `internal_execution_error` | Unclassified gateway defect | Retry only if documented safe |

The public error envelope includes `code`, `message`, `request_id`, optional `retry_after_ms`,
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

The executor returns immutable successes or failures rather than raising transport exceptions through
the gateway. Execution failures carry a safe message, exact reference, request/execution IDs,
optional upstream status, and bounded validation issues. Categories distinguish lookup, input,
binding, adapter, upstream status, timeout/connection, media type, malformed/oversized body,
output-schema, blocked mutation, and unexpected internal failures.

Raw response bodies, credentials, exception representations, local paths, and stack traces are
not emitted in the public error or structured execution telemetry. A non-success HTTP status is classified before body decoding, so a
plain-text `401` remains an upstream authentication failure.

### Implemented mutation errors

Phase 6 adds `mutation_confirmation_invalid`, `idempotency_conflict`,
`idempotency_in_progress`, `mutation_state_unavailable`, and `mutation_outcome_unknown`.
Challenge rejection reasons are intentionally collapsed at the public boundary. State-store
failure before durable claim prevents dispatch; terminal-persistence failure after dispatch is
reported as unknown outcome because the external effect cannot be rolled back or inferred.

## Upstream decoding order

The executor processes a response in this order:

1. Read and classify the response status before interpreting a body.
2. Validate a successful response's JSON media type and UTF-8 charset policy.
3. Reject an oversized declared body and enforce the maximum while streaming.
4. Decode strict UTF-8 and parse JSON with duplicate-key rejection.
5. Convert the parsed value to bounded immutable JSON.
6. Validate the response against the exact output schema.
7. Convert it to a domain result or normalized error.

At no point does it assume that a successful HTTP status implies valid JSON.

## Retry matrix

Phase 7 implements this retry matrix in the transport-independent executor.

| Operation | Failure | Automatic retry? |
| --- | --- | --- |
| Read | Connect failure, selected 5xx, 429 | Yes, if attempts and deadline remain |
| Read | 401, 403, validation, malformed response | No |
| Mutation without idempotency support | Any dispatched request failure | No |
| Mutation with declared idempotency | Any failure | No; downstream key propagation is absent |
| Any | Circuit open or local validation failure | No upstream call |

Backoff uses exponential growth with bounded full jitter. The retry policy stores maximum
attempts, retryable categories, base delay, and cap; the request deadline is always the final
authority.

## Circuit breaker

The process-local circuit breaker has three states:

- **CLOSED:** calls flow and classified outcomes update a rolling failure window.
- **OPEN:** calls fail fast until the cooldown timestamp.
- **HALF_OPEN:** generation-bound permits admit a bounded probe set; success closes the circuit and
  failure reopens it.

Only dependency-health failures affect the breaker. Caller validation errors, authorization
rejections, and expected not-found results do not. Aggregate transition metrics use only finite
state labels and never expose exact tool references.

## Ambiguous mutation outcomes

If a connection drops after dispatch, SentinelMCP records `outcome_unknown` when it can persist
the normalized failure. If the process or database fails before terminal persistence, the
record remains `pending`. A duplicate caller receives the durable state rather than triggering
a new mutation. Reconciliation is an explicit operator action; an audited reconciliation
command remains later work.

## Storage degradation

| Dependency | Reads | Mutations |
| --- | --- | --- |
| PostgreSQL registry unavailable | Fail before execution | Fail before execution |
| PostgreSQL audit unavailable | Preserve the normalized read result and emit best-effort telemetry | Fail closed before dispatch; report unknown after dispatch |
| Shared reliability backend unavailable | Not implemented; built-ins remain process-local | Do not claim cross-replica bounds |
| Trace exporter unavailable | Continue and record exporter health locally | Continue if audit is durable |
| Prometheus scrape unavailable | Execution continues | Execution continues |

Audit behavior is executable and tested. Trace and metric export remain best effort and do not
guarantee delivery.

## Shutdown and cancellation

On shutdown, stop admitting new requests, allow in-flight requests a bounded drain period,
flush required audit writes, and then cancel remaining work. Cancellation does not prove that
an upstream mutation did not occur. Such requests follow the ambiguous-outcome rule.
