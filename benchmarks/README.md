# Benchmarks

Benchmark drivers live in `sentinelmcp.benchmarking`; committed evidence lives under `results/`.
Read [the benchmark methodology](../docs/BENCHMARKS.md) before adding a workload or publishing a
number.

Phase 12 preserves two complete runs:

- `phase12-windows-local-before-cache-20261022/`: profiled baseline at `d5e0c67`
- `phase12-windows-local-after-cache-20261022/`: final measurement at `03b3ca0`

Each directory includes the manifest, raw request samples, per-trial resource samples, profile,
and generated JSON/Markdown summaries. Reproduce the final run with:

```bash
sentinel benchmark suite \
  --output benchmark-results/phase12-local \
  --trials 5 --requests 200 --warmup 20 \
  --concurrency 1,10,50,100 --postgres-concurrency 1,10
```

Set `SENTINELMCP_TEST_DATABASE_URL` to include the PostgreSQL mutation workload. Otherwise the
suite completes and records that omission in its manifest.
