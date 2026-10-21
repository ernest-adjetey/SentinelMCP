# SentinelMCP Master Progress

This file is the recovery checkpoint for long-running autonomous development. Read it before
changing code after any interruption, then inspect Git status, log, unstaged diff, and staged
diff. Update it after every significant milestone and commit.

## Current state

- **Current phase:** Phase 7 reliability control design
- **Completed phases:** 1, 2, 3, 4, 5, 6
- **Completed Phase 6 HEAD:** `30b4e35` (`main`, Document Phase 6 mutation safety)
- **Uncommitted work:** This Phase 7 checkpoint transition only
- **Staged work:** None
- **Remote:** None configured

## Last verified evidence

- Complete suite: 377 passed, 0 skipped, 0 warnings
- Branch coverage: 95.76%
- Real PostgreSQL lane: 45 collected and passed in the complete suite
- Ruff format and lint: passed
- Strict mypy: passed (78 source files)
- Alembic CLI upgrade: passed
- Docker Compose v5.6.0 configuration validation: passed
- Phase 6 wheel and source distribution: built and contents inspected
- Artifact Apache-2.0 license files and metadata: verified
- Isolated installed-wheel dependency check: passed
- Isolated installed-wheel signed-JWT mutation preview/confirm/reuse smoke: passed with one
  PostgreSQL-backed adapter dispatch
- PostgreSQL: project-local PostgreSQL 17.11 running on `127.0.0.1:54329`
- Test URL: `postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test`
- Benchmark status: not started; no performance claims exist

## Known limitations

- Phase 4 uses synchronous SQLAlchemy behind a synchronous registry protocol.
- Runtime bindings remain process-local.
- Mutation safety does not provide universal exactly-once effects. A crash before dispatch and
  a crash after an external effect but before terminal persistence can both leave `pending`;
  there is no automatic redrive or reconciliation command yet.
- Downstream idempotency-key propagation, reliability controls, observability, audit, replay,
  MCP transport, evaluation, and benchmarks are not implemented yet.
- Authentication uses static configured verification keys; OIDC/JWKS networking, revocation,
  delegated-agent identity, and persisted policy are not implemented.
- Docker is unavailable on this host; Compose config validation requires a standalone Docker
  CLI/Compose plugin or another available validator. CI is configured to run PostgreSQL.

## Next exact steps

1. Define request-wide deadline, retry-decision, retry-budget, rate-limit,
   and circuit-breaker protocols around the existing single-attempt dispatcher.
2. Preserve mutation safety: no automatic mutation retry without explicit downstream
   idempotency support and propagation.
3. Add deterministic clock/randomness tests before introducing operational adapters.
4. Decide whether process-local rate and circuit state is sufficient for the first executable
   phase; add Redis only if a proven cross-replica invariant requires it.

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

- No known production-code defect is open at this checkpoint.
