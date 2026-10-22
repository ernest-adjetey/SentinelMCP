# Operator CLI

## Scope

The `sentinel` console script exposes the current in-process demo composition through the same
registry, authorization policy, runtime bindings, executor, validators, and replay service used by
the application modules. It is a development and operator interface, not a remote administration
client. Every completed command emits one compact JSON object to standard output; handled command
errors emit a stable JSON error object to standard error.

The demo identity defaults to issuer `urn:sentinelmcp:cli` and subject `local-operator`. Use
`--principal-issuer` and `--principal-subject` to exercise current authorization under another
local identity. These flags do not authenticate an external user and must not be interpreted as a
hosted identity boundary.

## Tool discovery

```bash
sentinel tools list
sentinel tools describe demo.math.add@1.0.0
```

`tools list` returns registered identities and exact versions. `tools describe` returns the
canonical semantic definition for one exact `ToolRef`.

## Execution and capture

```bash
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}'
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}' --record
```

`--arguments` must be a strict JSON object; duplicate object keys are rejected. Execution failures
use the same normalized error codes as `ToolExecutor` and exit with status `1`. A successful
`--record` execution appends a replay record and adds `replay_record_id` to the JSON response.

## Inspect and replay

```bash
sentinel inspect RECORD_ID
sentinel replay RECORD_ID --dry-run
sentinel replay RECORD_ID
```

The default store is `.sentinelmcp/replays`. Set `SENTINELMCP_REPLAY_DIR` or pass
`--store DIRECTORY` to every command that must share a different store. Inspection returns the
strict stored record. Dry-run replay checks the exact current contract, approved binding, audit,
input, and current authorization without dispatch. Replay executes only when all current controls
allow it. See [Safe replay](REPLAY.md).

## Evaluation and benchmark entry points

```bash
sentinel eval run --output phase11-evaluation.json
sentinel benchmark run --iterations 100
```

The Phase 11 evaluation command loads the packaged, versioned fault catalog and invokes the real
executor, HTTP adapter, authorization policy, retry controls, and mutation service. Standard output
contains the full JSON report. `--output PATH` additionally writes the evaluation object as
indented machine-readable JSON. It contains no raw upstream bodies, arguments, confirmation tokens,
or idempotency keys. A run returns exit status `1` when any observed outcome differs from the
catalog expectation while still emitting the complete report.

The Phase 9 benchmark command performs a short sequential measurement of the in-process synthetic
executor path after a warmup and reports throughput, p50/p95/p99 latency, and error rate.
Iterations are bounded from 1 through 10,000. Its output is diagnostic only: it does not capture
the environment or satisfy the publication protocol in [Benchmarks](BENCHMARKS.md), and no result
from this command should be presented as a project performance claim.

## Exit behavior

- `0`: command completed and the requested execution, if any, succeeded;
- `1`: an execution returned a normalized failure, or an unexpected CLI defect occurred;
- `2`: a known invalid command input, registry error, or replay error occurred.

Argument-parser syntax errors use the conventional `argparse` usage output and exit status `2`.
Known JSON errors never echo the supplied arguments, credential material, or exception details.
