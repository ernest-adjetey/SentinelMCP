# Reliability Evaluation

## Purpose

The evaluation harness measures whether an agent/tool interaction produced the right kind of
outcome, not merely whether an HTTP request returned 200. Scenarios are deterministic by
default and use only synthetic tools.

## Outcome attribution

Every scenario terminates in exactly one primary attribution:

| Attribution | Definition |
| --- | --- |
| `MODEL_ERROR` | The agent selected the wrong tool or supplied semantically wrong arguments |
| `TOOL_ERROR` | The selected tool returned a validly classified domain or contract failure |
| `INFRASTRUCTURE_ERROR` | Gateway, storage, network, or dependency reliability failure |
| `POLICY_REJECTION` | SentinelMCP intentionally denied or gated the operation |
| `SUCCESS` | Expected result and safety invariants were satisfied |

Secondary labels capture retry recovery, circuit state, malformed responses, and ambiguous
mutation outcomes without double-counting the primary result.

## Scenario contract

Each Phase 11 scenario declares:

- stable ID, description, and behavior kind;
- an ordered bounded HTTP fault plan where applicable;
- expected normalized error or success;
- expected primary attribution and dispatch count; and
- whether a bounded retry is expected to recover.

Scenarios are data, not executable arbitrary Python. The packaged catalog is
`src/sentinelmcp/evaluation/scenarios/phase11.json`. Its strict loader rejects duplicate JSON keys,
unknown fields, duplicate IDs, invalid enums, and incoherent expected outcomes before dispatch. The
runner records the catalog suite and version with every report.

## Implemented Phase 11 matrix

The 23 deterministic scenarios cover:

- HTTP 400, 401, 403, 404, 409, 429, 500, 502, 503, and 504;
- malformed JSON, wrong content type, invalid UTF-8, and a bounded oversized response;
- transport timeout, bounded slow response, connection failure, and connection drop;
- a 503 followed by successful bounded retry;
- output-schema violation and default-deny policy rejection;
- completed idempotent mutation reuse without a second dispatch; and
- outcome-persistence failure after mutation dispatch, reported as ambiguous.

HTTP cases use `DeterministicHTTPTransport` behind the production `HTTPToolAdapter`. Read cases then
pass through `ToolExecutor`, including authorization, retries, response normalization, and output
validation. Mutation cases use `MutationService`; the evaluation-only in-memory state store exists
only to inject exact transitions and is not a durability substitute or production composition.

## Metrics and denominators

- **Tool execution success rate:** successful expected executions / execution-eligible cases
- **Correct tool routing rate:** expected tool selections / routing cases
- **Schema validation failure rate:** validation rejections / submitted calls
- **Authorization rejection rate:** denied decisions / authorization cases
- **Mutation safety violations:** mutations executed without all required invariants; target is 0
- **Error classification accuracy:** correct normalized category / fault-labeled cases
- **Recovery success rate:** recovered transient cases / intentionally recoverable cases
- **Latency percentiles:** reported by scenario family and outcome, never mixed blindly
- **Throughput:** completed scenarios / measured wall-clock interval with concurrency stated

Every percentage is accompanied by numerator and denominator. Small sample sets are not
presented as statistically conclusive.

## Reproduction and artifact

```bash
sentinel eval run --output docs/evidence/phase11-evaluation.json
pytest tests/evaluation tests/unit/test_fault_injection.py tests/cli/test_cli.py
```

The committed [Phase 11 result](evidence/phase11-evaluation.json) was measured on 2026-10-21
America/Los_Angeles (2026-10-22 UTC). All 23 declared outcomes matched: 8/8 tool-error cases,
11/11 infrastructure-error cases, 1/1 policy rejection, and 3/3 success/recovery cases. The suite
contains 0 model-routing cases, so it makes no model-accuracy claim. Its 78.09 ms wall-clock runtime
and per-scenario durations are diagnostic observations from one run, not benchmark results.

## Safety assertions

The Phase 11 harness checks negative evidence as first-class behavior:

- rejected calls generated no upstream request;
- a completed duplicate mutation generated no second adapter dispatch;
- the intermittent outage used exactly one bounded retry;
- post-dispatch persistence failure remained an explicit unknown outcome; and
- result artifacts contained normalized classifications rather than raw bodies or arguments.

Preview, changed/expired confirmation, PostgreSQL concurrency, audit, trace, and circuit invariants
remain covered by their dedicated unit, integration, concurrency, and MCP suites. They are not
double-counted as Phase 11 scenarios.

## LLM-backed evaluations

LLM routing experiments are optional and isolated from deterministic infrastructure tests.
They record provider/model identifiers, prompt version, sampling configuration, and raw model
decision artifacts subject to the data policy. Model variability never changes the expected
behavior of policy, mutation safety, or error classification tests.

## Limitations

- The suite proves deterministic classification and safety invariants, not real-network failure
  frequencies, service-level objectives, or production recovery rates.
- The slow-response case uses a bounded injected delay and does not emulate operating-system socket
  buffering or proxy behavior.
- PostgreSQL mutation races remain covered by the Phase 6 integration/concurrency lanes; the Phase
  11 duplicate and ambiguity cases isolate the application state transitions.
- Evaluation timing is intentionally excluded from performance claims. Phase 12 owns controlled
  warmup, concurrency, percentile, CPU, and memory measurements.
