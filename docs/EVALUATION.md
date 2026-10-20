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

Each versioned scenario will declare:

- stable scenario ID and description;
- fixture seed and initial synthetic service state;
- actor and agent identity claims;
- available tool registry snapshot;
- requested call or deterministic agent decision input;
- fault plan, if any;
- expected tool, policy decision, error category, state transition, and audit facts;
- maximum execution budget;
- cleanup and state-isolation rules.

Scenarios are data, not executable arbitrary Python. The runner validates their schema before
use and records the scenario version with every result.

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

## Safety assertions

The harness checks negative evidence as first-class behavior:

- rejected calls generated no upstream request;
- preview generated no mutation;
- an expired or changed confirmation generated no mutation;
- concurrent duplicate idempotency keys produced at most one accepted dispatch;
- raw secrets did not appear in audit, logs, traces, or result artifacts;
- retry and circuit behavior stayed within configured bounds.

## LLM-backed evaluations

LLM routing experiments are optional and isolated from deterministic infrastructure tests.
They record provider/model identifiers, prompt version, sampling configuration, and raw model
decision artifacts subject to the data policy. Model variability never changes the expected
behavior of policy, mutation safety, or error classification tests.
