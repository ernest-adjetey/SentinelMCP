# Observability

## Scope

Phase 8 adds structured JSON logging, OpenTelemetry API instrumentation, a private Prometheus
registry, a bounded `/metrics` endpoint, and provisioned local Prometheus/Grafana assets. Telemetry
is diagnostic and best effort. It does not replace durable audit evidence.

Core domain types do not import OpenTelemetry. Application code depends on small observability
protocols, and the implementation uses only OpenTelemetry API types. A host application chooses and
configures an SDK, processors, samplers, and exporters at composition time.

## Traces

One execution root span can contain finite child stages for:

- exact registry lookup;
- input validation;
- authorization and rate-limit admission;
- mutation preview or confirmation;
- runtime binding;
- each execution attempt and retry;
- output validation; and
- audit recording.

The REST adapter adds a finite gateway route span and an authentication child span. Span names and
stage values are allowlisted. Execution traces may include bounded exact tool components and trusted
request/execution IDs for correlation. They never include request or response payloads, credentials,
confirmation tokens, idempotency keys, raw URLs, arbitrary exception text, or stack traces.

Automatic OpenTelemetry exception recording is disabled. Telemetry calls are wrapped so provider,
processor, exporter, metrics, or logger failures cannot change a tool result. OpenTelemetry does not
guarantee span delivery: sampling, process termination, queue limits, collector outages, and exporter
failures can all lose traces.

## Metrics

Each `PrometheusMetrics` instance owns an isolated collector registry, preventing global-registry
collisions in tests and embedded deployments. Current metric families cover:

- gateway requests by finite route and normalized outcome;
- execution requests and outcomes by read/mutation/unknown class;
- execution duration histogram and adapter attempts;
- mutation previews and idempotency claim dispositions;
- audit persistence failures by finite phase; and
- aggregate process-local circuit states and transitions.

Finite execution error codes provide deadline, rate-limit, authentication, authorization,
confirmation, idempotency-conflict, output-validation, and dependency-failure breakdowns without
free-form labels.

Prometheus labels never contain request ID, execution ID, principal, exact tool name, raw URL,
arbitrary error message, token, or idempotency key. Tool-level metrics are deliberately omitted to
prevent uncontrolled cardinality. Exact tool correlation belongs in sampled traces and bounded logs.

Metrics are process-local and can be lost before a scrape, during a restart, or when Prometheus is
unavailable. Counters and circuit gauges are not a global multi-replica view unless Prometheus queries
aggregate every replica correctly.

## Structured logs and redaction

`StructuredEventLogger` emits one compact JSON document per event. A recursive redaction policy
normalizes sensitive key names, replaces known secret fields, bounds nesting and collection size,
truncates long strings, rejects non-finite numbers, and never mutates caller input. Execution logs use
trusted IDs, exact bounded tool reference, operation class, finite outcome, duration, and attempt
count. Application code must still avoid passing arbitrary payloads to logging.

## Local stack

Start a metrics-enabled gateway on host port `8000`, then run:

```bash
docker compose up -d postgres prometheus grafana
```

Prometheus is available at `http://localhost:9090`; Grafana is available at
`http://localhost:3000`. Compose provisions the `SentinelMCP Overview` dashboard from
`deploy/grafana/dashboards/sentinelmcp-overview.json`. The dashboard shows execution and gateway
rates, p95 latency, audit failures, idempotency claims, adapter attempts, and circuit state.

The local Grafana service allows anonymous read-only access for developer convenience. That setting
is not suitable for a shared or production deployment. No OpenTelemetry Collector is claimed by the
current Compose stack; configure an SDK/exporter in the embedding service when external trace export
is required.

## Failure policy

- Tracing, structured logging, and metrics failures never replace the business outcome.
- Read audit failure is observable but preserves the read result.
- Mutation intent audit failure prevents dispatch.
- Mutation completion audit failure after dispatch becomes `mutation_outcome_unknown`.
- Prometheus scrape failure and Grafana outage do not stop execution.

See [Audit contract](AUDIT.md), [Reliability controls](RELIABILITY.md), and
[Failure model](FAILURE_MODEL.md).
