# SentinelMCP Master Progress

This file is the recovery checkpoint for long-running autonomous development. Read it before
changing code after any interruption, then inspect Git status, log, unstaged diff, and staged
diff. Update it after every significant milestone and commit.

## Current state

- **Current phase:** Phase 13 release, portfolio, security, and documentation polish
- **Completed phases:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
- **Completed Phase 7 HEAD:** `ae4aefb` (`main`, Checkpoint Phase 7 completion)
- **Completed Phase 8 implementation HEAD:** `424496a` (`main`, Document and provision Phase 8
  operations); runtime and tests are in `76d09d3`
- **Completed Phase 9 implementation HEAD:** `3869d71` (`main`, Add constrained replay and
  operator CLI); documentation is in `d35b256`
- **Completed Phase 10 implementation HEAD:** `b1e304d` (`main`, Add official MCP integration);
  documentation is in `4b2a652`
- **Completed Phase 11 implementation HEAD:** `03a2d70` (`main`, Add deterministic fault
  evaluation); documentation and measured artifact are in `54ade21`
- **Completed Phase 12 implementation HEAD:** `d5e0c67` (`main`, Add reproducible benchmark
  harness); profile-guided schema-validator optimization is in `03b3ca0`; documentation and
  committed benchmark evidence are in `045c52f`
- **Uncommitted work:** This Phase 12 checkpoint transition only
- **Staged work:** None
- **Remote:** None configured

## Last verified evidence

- Complete suite: 597 passed, 0 skipped, 0 warnings
- Branch coverage: 96.14%
- Real PostgreSQL lane: included and passed in the complete suite
- Focused MCP lane: 42 passed; MCP adapter local coverage was 91% for names, 93% for principal,
  and 88% for server
- Phase 11 focused evaluation/fault/CLI lane: 33 passed
- Ruff format: passed (132 Python files); Ruff lint: passed
- Strict mypy: passed (127 files)
- Markdown-link and package-layout tests: 16 passed with coverage disabled for the focused lane
- Phase 10 source distribution built and inspected; wheel built from the extracted source
  distribution and inspected
- MCP modules/tests/documentation, Apache-2.0 license files, and license metadata: verified in both
  clean archives; build exclusions reject development tools, caches, artifacts, and prior builds
- Phase 10 sdist SHA-256: `7b1b089b9f65dd8b6454b878475930da38bc9d02a12a00b2858dd6300f47e944`
- Phase 10 wheel SHA-256: `07f6084b56f55b6f1050ff92b4b02c6be435268d8206a64b14c6c4ab60c60b3f`
- Isolated installed-wheel dependency check: passed (`pip check` reported no broken requirements)
- Installed-wheel official-client smoke: negotiated MCP `2026-07-28`, listed three exact-version
  demo tools, and returned `{'sum': 5}` for the demo addition tool
- Alembic CLI upgrade, downgrade to base, and second upgrade to revision `0003` head: passed
- Docker Compose v5.6.0 configuration validation: passed
- Grafana dashboard JSON validation: passed (7 panels)
- Prometheus native validation: not run because `promtool` is unavailable; Compose validation and
  configuration review passed
- PostgreSQL: project-local PostgreSQL 17.11 verified on `127.0.0.1:54329`
- Test URL: `postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test`
- MCP dependency: official Python SDK 2.3.0, constrained to compatible 2.x releases
- Phase 11 measured artifact: `docs/evidence/phase11-evaluation.json`, generated
  `2026-10-22T03:45:23.911107Z` (2026-10-21 America/Los_Angeles)
- Phase 11 results: 23/23 expectations matched: 8/8 tool errors, 11/11 infrastructure errors,
  1/1 policy rejection, and 3/3 success/recovery cases; 0 model scenarios and no model claim
- Phase 11 diagnostic suite runtime: 78.09 ms for one deterministic local run; explicitly not a
  benchmark or production latency claim
- Phase 12 final benchmark: 26,000 request samples across seven real composition paths, five trials
  per group, no unexpected errors, commit `03b3ca06ae3c1eca0e1b2fb524179618fc71460d`
- Phase 12 concurrency-1 results: core 5,051 requests/s at 0.251 ms p95; authenticated in-process
  REST 1,041 requests/s at 1.160 ms p95; PostgreSQL mutation preview/confirm 298 requests/s at
  4.414 ms p95
- Profile-guided result: bounded immutable schema-validator caching improved concurrency-1 core
  throughput from 624 to 5,051 requests/s and reduced p95 from 2.443 ms to 0.251 ms
- Phase 12 raw evidence: `benchmarks/results/phase12-windows-local-before-cache-20261022/` and
  `benchmarks/results/phase12-windows-local-after-cache-20261022/`

## Known limitations

- Phase 4 uses synchronous SQLAlchemy behind a synchronous registry protocol.
- Runtime bindings remain process-local.
- Mutation safety does not provide universal exactly-once effects. A crash before dispatch and
  a crash after an external effect but before terminal persistence can both leave `pending`;
  there is no automatic redrive or reconciliation command yet.
- Reliability controls are opt-in and process-local; they provide no global multi-replica rate,
  retry, or circuit bound and retain observed keys for the process lifetime.
- Downstream idempotency-key propagation is not implemented yet.
- MCP exposes only exact `ToolRef` encodings that fit the protocol's 128-character tool-name limit;
  it rejects longer references instead of truncating, hashing, or silently aliasing them.
- Stdio and in-memory MCP callers require an explicitly configured static principal resolver and
  therefore rely on composition-time trust. Streamable HTTP is stateless, denies unauthenticated
  callers by default, and still requires deployment-specific TLS and trusted-proxy configuration.
- Cancelling an MCP transport request cancels the adapter task, but does not prove an already
  dispatched external side effect stopped. Mutation confirmation remains governed by the durable
  mutation state machine and unknown-outcome handling.
- The MCP dependency is bounded to 2.x and was verified with SDK 2.3.0 and protocol `2026-07-28`;
  later compatible releases still require normal regression testing.
- Phase 11 uses deterministic injected transports and an evaluation-only mutation store. It proves
  classification and dispatch invariants, not real-network failure frequencies, service-level
  objectives, model routing accuracy, or production recovery rates.
- Phase 12 is a single-host local synthetic benchmark. It does not establish production capacity,
  external-network latency, multi-process scaling, Redis performance, or service-level objectives.
  In-memory MCP latency includes client-side transport queueing, and Windows process CPU sampling
  has coarse resolution.
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

1. Perform the final security review, including SSRF/network-target policy, redirect behavior,
   secrets, logs, generated artifacts, and repository history.
2. Complete release documentation, demos, portfolio evidence, packaging, and clean-install checks.
3. Run the final quality/security/publication gate, then create and verify the public GitHub
   repository, CI, optional `v0.1.0` release, and portfolio links.

## Resume commands

```powershell
$env:PYTHONPATH = '.devtools;.devtools\win32;.devtools\win32\lib'
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
