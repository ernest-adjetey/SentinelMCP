# Benchmark Methodology

## Current results

Phase 12 measured commit `03b3ca06ae3c1eca0e1b2fb524179618fc71460d` on Windows 11,
Python 3.12.14, PostgreSQL 17.11, and a 22-logical-CPU Intel host. Each group used 20 warm-up
requests followed by five trials of 200 measured requests. The complete environment and command
are in the [result manifest](../benchmarks/results/phase12-windows-local-after-cache-20261022/manifest.json).

Selected concurrency-1 results from the final run:

| Workload | Median requests/s | p50 ms | p95 ms | p99 ms | Error rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct adapter | 139,909 | 0.004 | 0.004 | 0.006 | 0% |
| Executor core | 5,051 | 0.188 | 0.251 | 0.403 | 0% |
| Executor + audit + telemetry | 3,214 | 0.284 | 0.416 | 0.704 | 0% |
| Authenticated REST | 1,041 | 0.919 | 1.160 | 1.587 | 0% |
| Official SDK in-memory MCP | 1,883 | 53.539 | 100.982 | 106.797 | 0% |
| PostgreSQL mutation preview + confirm | 298 | 3.235 | 4.414 | 5.072 | 0% |
| Normalized executor failure | 5,606 | 0.162 | 0.285 | 0.425 | 100% expected |

The [complete generated summary](../benchmarks/results/phase12-windows-local-after-cache-20261022/summary.md)
contains every measured concurrency. Raw request and resource samples are committed beside it.
These are local synthetic measurements, not production capacity or service-level claims.

## Profile-guided optimization

The baseline profile attributed 0.70 of 0.84 seconds across 200 core calls to recompiling the
same immutable JSON Schemas. A bounded 1,024-entry cache keyed by the deeply immutable
`ToolDefinition` removed that repeated work. At concurrency 1, core throughput increased from
624 to 5,051 requests/s and core p95 latency fell from 2.443 ms to 0.251 ms. The complete
[baseline](../benchmarks/results/phase12-windows-local-before-cache-20261022/) and
[optimized](../benchmarks/results/phase12-windows-local-after-cache-20261022/) artifacts preserve
the profiles and raw samples. The post-change profile identifies fingerprint canonicalization as
the next core cost, but its measured magnitude did not justify another optimization.

## Workloads

Each benchmark run uses a fixed payload and deterministic order for:

- Direct adapter invocation
- Core executor invocation
- Executor invocation with in-memory audit recording, OpenTelemetry, and Prometheus
- Authenticated FastAPI REST invocation through ASGI transport
- Official MCP SDK invocation through its in-memory client transport
- Normalized executor failure
- PostgreSQL-backed mutation preview and confirmation

Default concurrency levels are `1`, `10`, `50`, and `100`; PostgreSQL defaults to `1` and `10`.
A dependency-backed path may be omitted only when unavailable, and the reason appears in the
manifest and summary.

## Metrics and controls

- Median per-trial completed requests per second
- p50, p95, and p99 over all raw request durations in a group
- Error count and rate by normalized category
- Process CPU time per trial
- Working set and process-lifetime peak working set on Windows
- Operating system, CPU, Python and key package versions, Git commit, command, and configuration
- At least five measured trials after a fixed warm-up
- Raw request and resource records retained for every trial

Percentiles use the nearest-rank method over raw durations, not averages of worker percentiles.
Warm-up traffic is excluded. Healthy and intentionally failed workloads are reported separately.

## Reproduction

```bash
sentinel benchmark suite \
  --output benchmark-results/phase12-local \
  --trials 5 --requests 200 --warmup 20 \
  --concurrency 1,10,50,100 --postgres-concurrency 1,10
```

Set `SENTINELMCP_TEST_DATABASE_URL` to include the PostgreSQL workload. The output directory must
not already exist.

## Result artifact contract

Each run writes an immutable directory under `benchmark-results/` containing:

```text
manifest.json       environment, commit, command, and configuration
requests.jsonl      one bounded record per completed request
resources.jsonl     one process-resource record per measured trial
profile.json        cProfile hot paths from the core executor before measured trials
summary.json        computed aggregates
summary.md          human-readable table generated from summary.json
```

A curated result is committed only with its manifest, raw samples, profile, summaries, and
reproduction command. README claims link to that result set.

## Interpretation limits

- A direct call and a gateway call use the same synthetic tool and payload.
- Client-side queueing is included in end-to-end latency. MCP throughput and latency must be
  interpreted together because the in-memory SDK transport serializes portions of request work.
- Timeouts count as errors; a faster failure is never reported as healthy latency.
- The direct adapter is intentionally tiny and primarily establishes timer/harness overhead.
- Process CPU resolution is coarse on this Windows host. Peak working set is process-lifetime
  state rather than an isolated per-workload maximum.
- The run does not measure external network latency, multi-process scaling, Redis, a remote audit
  exporter, or production service saturation.
- No result is generalized beyond the recorded hardware, software, and configuration.
