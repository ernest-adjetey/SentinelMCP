# Quickstart

SentinelMCP 0.1.1 is an alpha library and reference control plane, not a hosted production
service. The fastest safe path is the local synthetic CLI; it exercises the same contracts,
authorization, executor, validation, and replay services without external tools or credentials.

## Install from source

Python 3.12 or newer is required.

```bash
git clone https://github.com/ernest-adjetey/SentinelMCP.git
cd SentinelMCP
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Execute an exact tool version

```bash
sentinel tools list
sentinel tools describe demo.math.add@1.0.0
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}'
```

Every command emits one compact JSON object. Tool references are exact; there is no implicit
`latest` lookup in execution.

## Record and replay synthetic input

```bash
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}' --record
sentinel inspect RECORD_ID
sentinel replay RECORD_ID --dry-run
sentinel replay RECORD_ID
```

Replay is reauthorized, audited, fingerprint-checked, and synthetic-read-only by default. Never
record credentials or sensitive production arguments.

## Run reliability evaluation

```bash
sentinel eval run --output phase11-evaluation.json
```

The packaged suite uses deterministic transports and reports expected versus observed normalized
errors, attribution, and dispatch counts for 23 scenarios.

## Run the reproducible benchmark harness

```bash
sentinel benchmark suite --output benchmark-results/local
```

Without PostgreSQL, the suite records the mutation workload as omitted. Read
[Benchmark methodology](BENCHMARKS.md) before publishing any number.

## Enable the PostgreSQL lanes

The complete test, registry, audit, and mutation lanes require PostgreSQL 17.

```bash
docker compose up -d postgres
export SENTINELMCP_DATABASE_URL='postgresql+psycopg://sentinelmcp_test:sentinelmcp_dev_only@localhost:54329/sentinelmcp_test'
export SENTINELMCP_TEST_DATABASE_URL="$SENTINELMCP_DATABASE_URL"
python -m alembic upgrade head
pytest
```

The checked-in credentials are disposable local-development values only. Continue with the
[examples](EXAMPLES.md), [architecture](ARCHITECTURE.md), and [security model](SECURITY_MODEL.md).
