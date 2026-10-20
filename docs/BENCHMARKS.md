# Benchmark Methodology

## Current results

No performance results have been measured. This document defines the protocol that future
results must follow. Empty or estimated result tables are deliberately omitted.

## Questions

The suite will measure:

1. Fixed overhead of the SentinelMCP gateway relative to a direct synthetic tool call.
2. Incremental cost of audit persistence and trace export.
3. Behavior under controlled latency, throttling, malformed responses, and outages.
4. Throughput and tail latency as concurrency increases.
5. Resource usage and saturation points for gateway, PostgreSQL, and Redis.

## Workloads

Each benchmark run includes the same payload distribution and seeded request order for:

- Direct tool baseline
- Gateway with minimal execution path
- Gateway plus durable audit
- Gateway plus tracing
- Gateway under declared fault profiles
- Read-heavy and mutation-preview workloads

Concurrency levels are `1`, `10`, `50`, `100`, `250`, and `500`. A level may be omitted only
when the environment cannot sustain it, and the reason must appear in the result metadata.

## Metrics

- Completed requests per second
- p50, p95, and p99 end-to-end latency
- Error rate by normalized category
- Retry attempts per request
- Circuit-open rejections and recovery time
- Gateway CPU time, resident memory, and event-loop lag
- PostgreSQL connection-pool utilization and query latency
- Redis command latency

Percentiles are computed from raw request durations, not averages of worker percentiles.
Warm-up traffic is excluded using a fixed, declared interval.

## Experimental controls

- Pin image digests and dependency lockfile.
- Record operating system, CPU, memory, Python version, Git commit, Docker version, and service
  configuration.
- Disable unrelated workloads where possible.
- Run at least five measured trials after warm-up.
- Randomize scenario order or alternate baseline/gateway runs to reduce thermal drift.
- Report median trial results and variability; retain every raw trial.
- Use a local network for gateway-overhead claims unless network conditions are explicitly part
  of the experiment.
- Do not compare debug and optimized configurations.

## Result artifact contract

Each run writes an immutable directory under `benchmark-results/` containing:

```text
manifest.json       environment, commit, command, seed, configuration
requests.jsonl      one bounded record per completed request
resources.jsonl     timestamped process and dependency resource samples
summary.json        computed aggregates and confidence information
summary.md          human-readable table generated from summary.json
charts/             generated images derived only from committed raw data
```

The repository will commit curated result sets only when they include the manifest, raw data,
generator version, and reproduction command. Generated claims in the README must link to that
result set.

## Avoiding misleading claims

- A direct call and a gateway call must use the same synthetic tool and payload.
- Faulted requests are reported separately from healthy throughput.
- Client-side queueing is included in end-to-end latency.
- Timeouts count as errors, not censored samples.
- A faster failure is not reported as improved latency without the error rate beside it.
- No result is generalized beyond the measured hardware and configuration.
