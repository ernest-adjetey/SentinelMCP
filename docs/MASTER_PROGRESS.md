# SentinelMCP Master Progress

This file is the recovery checkpoint for long-running autonomous development. Read it before
changing code after any interruption, then inspect Git status, log, unstaged diff, and staged
diff. Update it after every significant milestone and commit.

## Current state

- **Current phase:** Phase 9 replay and debugging CLI
- **Completed phases:** 1, 2, 3, 4, 5, 6, 7, 8
- **Completed Phase 7 HEAD:** `ae4aefb` (`main`, Checkpoint Phase 7 completion)
- **Completed Phase 8 implementation HEAD:** `424496a` (`main`, Document and provision Phase 8
  operations); runtime and tests are in `76d09d3`
- **Uncommitted work:** This Phase 8 checkpoint transition only
- **Staged work:** None
- **Remote:** None configured

## Last verified evidence

- Complete suite: 472 passed, 0 skipped, 0 warnings
- Branch coverage: 95.76%
- Real PostgreSQL lane: 50 collected and passed in the complete suite
- Ruff format: passed (138 files); Ruff lint: passed
- Strict mypy: passed (98 files)
- Markdown-link and package-layout tests: 16 passed with coverage disabled for the focused lane
- Phase 8 source distribution and wheel: built from the source distribution and inspected
- Artifact audit/telemetry modules, migration, documentation, Apache-2.0 license files, and license
  metadata: verified
- Phase 8 sdist SHA-256: `0656a539c8abccd8db4ff6b7c9b5e3caa5a191e5036567feff12c7797a2977bc`
- Phase 8 wheel SHA-256: `388145642f8a877ae760186ab70a09bc431424ed03744f54b5b246b4e55a4e2d`
- Isolated installed-wheel dependency check: passed
- Installed-wheel PostgreSQL audit and Prometheus telemetry smoke: passed
- Alembic CLI upgrade, downgrade to base, and second upgrade to revision `0003` head: passed
- Docker Compose v5.6.0 configuration validation: passed
- Grafana dashboard JSON validation: passed (7 panels)
- Prometheus native validation: not run because `promtool` is unavailable; Compose validation and
  configuration review passed
- PostgreSQL: project-local PostgreSQL 17.11 verified on `127.0.0.1:54329`
- Test URL: `postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test`
- Benchmark status: not started; no performance claims exist

## Known limitations

- Phase 4 uses synchronous SQLAlchemy behind a synchronous registry protocol.
- Runtime bindings remain process-local.
- Mutation safety does not provide universal exactly-once effects. A crash before dispatch and
  a crash after an external effect but before terminal persistence can both leave `pending`;
  there is no automatic redrive or reconciliation command yet.
- Reliability controls are opt-in and process-local; they provide no global multi-replica rate,
  retry, or circuit bound and retain observed keys for the process lifetime.
- Downstream idempotency-key propagation, replay, MCP transport, evaluation, and benchmarks are not
  implemented yet.
- Audit APIs are append-oriented but are not cryptographically tamper-proof; a privileged database
  administrator can alter storage. There is no retention or external audit export policy.
- OpenTelemetry span delivery is not guaranteed, and process-local Prometheus samples can be lost
  before a scrape. Sensitive payloads are intentionally excluded, and metric labels are bounded;
  exact tool names are not metric labels.
- A post-dispatch mutation audit failure becomes a durable unknown outcome; automated
  reconciliation is not implemented.
- Authentication uses static configured verification keys; OIDC/JWKS networking, revocation,
  delegated-agent identity, and persisted policy are not implemented.
- Docker is unavailable on this host; Compose config validation requires a standalone Docker
  CLI/Compose plugin or another available validator. CI is configured to run PostgreSQL.

## Next exact steps

1. Define a versioned replay-safe execution record and contract-drift policy that references exact
   `ToolRef` and historical fingerprint without weakening Phase 8 payload-exclusion guarantees.
2. Add the `sentinel` CLI entry point with tools list/describe, execute, inspect, replay, eval run,
   and benchmark run commands over the existing application services.
3. Default replay to synthetic/replay-safe bindings, prove live mutation replay cannot occur
   silently, then document limitations and run the complete Phase 9 quality gate.

## Resume commands

```powershell
$env:PYTHONPATH = '.devtools'
$env:SENTINELMCP_TEST_DATABASE_URL = 'postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test'
$python = 'python'
& $python -m pytest
& $python -m mypy
.\.devtools\bin\ruff.exe format --check .
.\.devtools\bin\ruff.exe check .
```

If PostgreSQL is not running:

```powershell
.\.devtools\postgresql-17.11\pgsql\bin\pg_ctl.exe -D .devtools\pgdata -l .devtools\postgres.log -o "-p 54329 -h 127.0.0.1" start
```

## Unresolved defects

- No known production-code defect is open. A Windows sandbox restriction prevents `asyncio` from
  creating its localhost wakeup socket, so asynchronous tests must run outside that network
  restriction; the code path itself passes there.
