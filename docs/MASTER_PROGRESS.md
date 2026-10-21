# SentinelMCP Master Progress

This file is the recovery checkpoint for long-running autonomous development. Read it before
changing code after any interruption, then inspect Git status, log, unstaged diff, and staged
diff. Update it after every significant milestone and commit.

## Current state

- **Current phase:** Phase 10 official MCP integration
- **Completed phases:** 1, 2, 3, 4, 5, 6, 7, 8, 9
- **Completed Phase 7 HEAD:** `ae4aefb` (`main`, Checkpoint Phase 7 completion)
- **Completed Phase 8 implementation HEAD:** `424496a` (`main`, Document and provision Phase 8
  operations); runtime and tests are in `76d09d3`
- **Completed Phase 9 implementation HEAD:** `3869d71` (`main`, Add constrained replay and
  operator CLI); documentation is in `d35b256`
- **Uncommitted work:** This Phase 9 checkpoint transition only
- **Staged work:** None
- **Remote:** None configured

## Last verified evidence

- Complete suite: 507 passed, 0 skipped, 0 warnings
- Branch coverage: 96.11%
- Real PostgreSQL lane: 50/507 tests collected and passed in the complete suite
- Ruff format: passed (151 files); Ruff lint: passed
- Strict mypy: passed (109 files)
- Markdown-link and package-layout tests: 16 passed with coverage disabled for the focused lane
- Phase 9 source distribution built and inspected; wheel built from the extracted source
  distribution and inspected
- Replay/CLI modules, documentation, Apache-2.0 license files, license metadata, and `sentinel`
  console entry point: verified
- Phase 9 sdist SHA-256: `8e900ff0da70ccb7bbe1486af7fc0be02105b01556b5d171cef2487ae9f1b916`
- Phase 9 wheel SHA-256: `06c4acae685a042acf51386630bc7b071983df67c981e10c85ecaa195c2df4b7`
- Isolated installed-wheel dependency check: passed with the workspace dependency runtime supplied
- Installed-wheel tools list, execute/capture, inspect, dry-run replay, replay, evaluation smoke,
  and bounded benchmark workflow: passed
- Alembic CLI upgrade, downgrade to base, and second upgrade to revision `0003` head: passed
- Docker Compose v5.6.0 configuration validation: passed
- Grafana dashboard JSON validation: passed (7 panels)
- Prometheus native validation: not run because `promtool` is unavailable; Compose validation and
  configuration review passed
- PostgreSQL: project-local PostgreSQL 17.11 verified on `127.0.0.1:54329`
- Test URL: `postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test`
- Benchmark status: only the bounded Phase 9 diagnostic command has run; no reproducible benchmark
  artifact or performance claim exists

## Known limitations

- Phase 4 uses synchronous SQLAlchemy behind a synchronous registry protocol.
- Runtime bindings remain process-local.
- Mutation safety does not provide universal exactly-once effects. A crash before dispatch and
  a crash after an external effect but before terminal persistence can both leave `pending`;
  there is no automatic redrive or reconciliation command yet.
- Reliability controls are opt-in and process-local; they provide no global multi-replica rate,
  retry, or circuit bound and retain observed keys for the process lifetime.
- Downstream idempotency-key propagation, MCP transport, fault evaluation, and reproducible
  benchmarks are not implemented yet.
- Replay defaults to explicitly marked synthetic read bindings, current reauthorization, and a
  final pre-dispatch binding guard. The standalone CLI audit store is process-local, and its local
  replay files are unencrypted and not tamper-proof. Sensitive-key rejection cannot detect a secret
  stored under an innocuous field name.
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

1. Verify the current official MCP Python SDK API and supported transport lifecycle from primary
   documentation before choosing the adapter boundary and dependency range.
2. Implement MCP tool discovery and invocation as a thin transport adapter over the existing exact
   registry, authentication/authorization context, and `ToolExecutor` path.
3. Prove exact-version naming, schema exposure, error translation, cancellation, and execution
   parity with focused protocol tests, then run the complete Phase 10 quality gate.

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
