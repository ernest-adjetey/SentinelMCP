# Reliability Controls

## Scope

Phase 7 adds bounded reliability behavior to the transport-independent executor. The controls
are injected behind protocols so production composition can choose its coordination backend
without changing request semantics. The built-in implementations are thread-safe and
process-local; they do not coordinate multiple replicas.

## Request lifecycle

After exact-contract lookup, input validation, and authorization, the executor:

1. applies one rate-limit decision keyed by verified issuer, subject, and exact tool;
2. creates one absolute monotonic deadline from the registered timeout policy;
3. resolves the exact runtime binding;
4. acquires a circuit permit for each adapter attempt;
5. bounds that attempt by the remaining request-wide deadline;
6. classifies the result and updates circuit health;
7. retries only eligible reads when attempts, deadline, circuit, and retry budget allow.

Every retry receives a new execution ID but retains the caller-visible request ID. Backoff uses
capped exponential full jitter. Sleep time and adapter time consume the same deadline; no attempt
gets a fresh timeout budget.

## Retry policy

Automatic retry is limited to reads and these normalized failures:

- upstream rate limiting;
- upstream server error;
- upstream timeout;
- upstream unavailability.

Authentication, authorization, validation, malformed response, invalid media type, output-schema,
and other deterministic failures are not retried. A configured mutation remains limited to one
adapter dispatch even when its contract declares more attempts. SentinelMCP does not yet propagate
an idempotency key to a downstream tool, so retrying a mutation would overstate its guarantees.

The retry budget is separate from a request's attempt count. Each retry consumes one token scoped
to the exact tool version, bounding aggregate amplification within the process. Budget exhaustion
returns the most recent authoritative adapter result rather than manufacturing a new failure.

## Deadlines and timeouts

`Deadline` stores an absolute monotonic expiry. Before each attempt and delay, the executor checks
the remaining budget. Each adapter await is wrapped in `asyncio.timeout` with that remainder. A
timeout raised by the wrapper is normalized to `upstream_timeout`; exhaustion before dispatch or
backoff completion is `deadline_exceeded`.

Caller cancellation is not converted because `asyncio.CancelledError` is outside the ordinary
exception path. Adapter code should still clean up its own resources when cancelled.

## Circuit breaker

The built-in breaker has `closed`, `open`, and `half_open` states keyed by exact tool version.
Consecutive timeout, unavailable, and server-error outcomes count as dependency-health failures.
Caller and contract failures are neutral. After cooldown, at most the configured number of
half-open probes may run. Permits carry a generation so a late result from an older generation
cannot close a newer open circuit.

The key is the immutable `ToolRef`, not a physical endpoint. Runtime bindings are currently
process-local and do not expose a stable endpoint identity. Tools sharing one dependency therefore
do not share a circuit unless a future binding model introduces an explicit dependency key.

An optional dependency-neutral observer reports aggregate circuit creation and finite state
transitions. The Prometheus implementation maintains per-state gauges and transition counters
without exposing exact tool references as labels. Observer failure cannot change circuit behavior.

## Rate limiting

The in-memory limiter uses continuously refilled token buckets keyed by verified principal and
exact tool version. Rejections return bounded retry guidance, exposed as `retry_after_ms` by the
REST adapter. Caller-provided request data never becomes a limiter key before authentication.

## Configuration and failure behavior

`ReliabilityControls` defaults to unlimited/no-op implementations so the library does not silently
impose deployment policy. A production composition must explicitly inject the rate limiter, retry
budget, and circuit breaker it requires. Admission and circuit-acquire failures fail the request;
retry-budget failures suppress another retry; post-dispatch circuit-recording failures do not
replace an already authoritative tool outcome.

The in-memory implementations retain one record per observed scope for the process lifetime. A
deployment with an unbounded principal or tool population must provide a bounded implementation.

## Distributed limitation

No Redis service or multi-replica runtime is part of the current executable deployment, so Phase 7
does not add a Redis dependency merely to mirror local state. Across replicas, rate limits, retry
budgets, and circuits are independent and therefore provide no global bound. Before horizontal
deployment, implement these protocols with atomic shared state, define outage behavior, and add
real multi-process/service tests. Durable mutation correctness continues to live in PostgreSQL and
does not depend on the process-local controls.

## Evidence

Deterministic unit tests cover deadline accounting, retry classification, jitter, retry budgets,
timeouts, circuit transitions, control failures, and mutation non-retry. Thirty-two-worker race
tests cover token capacity and half-open admission. Property tests cover deadline and jitter
bounds. The complete PostgreSQL-backed suite is the release gate; current counts belong in
`MASTER_PROGRESS.md`, never in this durable design contract.
