# SentinelMCP Master Progress

This file is the recovery checkpoint for long-running autonomous development. Read it before
changing code after any interruption, then inspect Git status, log, unstaged diff, and staged
diff. Update it after every significant milestone and commit.

## Current state

- **Current phase:** Complete through public 0.1.1 release and portfolio deployment
- **Completed phases:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
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
- **Completed Phase 13 security HEAD:** `37dad2e` (`main`, Harden HTTP network boundaries)
- **Phase 13 release implementation checkpoint:** `f251c23` (`main`, Prepare SentinelMCP 0.1.0
  release), after the authorized history rewrite
- **Published release:** `v0.1.0` remains the immutable, validated first public alpha at commit
  `3c9da5f`; its artifacts are unchanged
- **Patch release target:** `v0.1.1` contains the Linux CI portability repair and cross-platform
  replay append error normalization; `v0.1.0` will not be moved
- **Published patch release:** `v0.1.1` is immutable at
  `34159097d64bdb9cb0527798c9cf77044b2bf34a`; its release artifacts match the locally validated
  distributions
- **Portfolio deployment:** `ernest-adjetey/ernest-adjetey.github.io` commit
  `286d5976c2fc7842d409217176e92f54e0c4d0a7` is live with the SentinelMCP flagship card, case
  study, and functional accessible contact flow
- **Working tree target:** Clean after the final publication cross-link checkpoint
- **Remote:** `origin` -> `https://github.com/ernest-adjetey/SentinelMCP.git`

## Last verified evidence

- Complete Python 3.12.14 suite: 617 passed in 49.36 seconds, 0 skipped, 0 warnings
- Branch coverage: 95.64% (5,344 statements and 1,300 branches measured)
- Real PostgreSQL lane: 58 tests collected and passed in the complete suite
- Focused MCP lane: 42 passed; MCP adapter local coverage was 91% for names, 93% for principal,
  and 88% for server
- Phase 11 focused evaluation/fault/CLI lane: 33 passed
- Ruff 0.17.2 format: passed (180 Python files); Ruff lint: passed
- Strict mypy 1.20.2: passed (128 files)
- Markdown-link, package-layout, and package-metadata checks: passed in the complete suite; focused
  Markdown-link/package-metadata lane: 3 passed
- Phase 10 source distribution built and inspected; wheel built from the extracted source
  distribution and inspected
- MCP modules/tests/documentation, Apache-2.0 license files, and license metadata: verified in both
  clean archives; build exclusions reject development tools, caches, artifacts, and prior builds
- Phase 10 sdist SHA-256: `7b1b089b9f65dd8b6454b878475930da38bc9d02a12a00b2858dd6300f47e944`
- Phase 10 wheel SHA-256: `07f6084b56f55b6f1050ff92b4b02c6be435268d8206a64b14c6c4ab60c60b3f`
- Isolated installed-wheel dependency check: passed (`pip check` reported no broken requirements)
- Installed-wheel official-client smoke: negotiated MCP `2026-07-28`, listed three exact-version
  demo tools, and returned `{'sum': 5}` for the demo addition tool
- Phase 13 installed-wheel release smoke: imported version `0.1.0` from isolated
  `site-packages` with `PYTHONPATH` cleared, passed `pip check`, ran the `sentinel` entry point,
  negotiated MCP `2026-07-28`, discovered exactly `s.demo.catalog.lookup_1.0.0`,
  `s.demo.math.add_1.0.0`, and `s.demo.profile.get_1.0.0`, and returned `{'sum': 5}` through MCP,
  the core executor, and authenticated in-process REST
- CI-equivalent latest-compatible environment: FastAPI 0.161.0, SQLAlchemy 2.1.6, Alembic 1.20.1,
  pytest 9.1.1, and Ruff 0.17.2 passed `pip check`, strict third-party advisory audit, static
  analysis, and the same 617-test/95.64% gate
- Public Actions run `39842490068` passed dependency integrity, audit, formatting, and lint, then
  exposed a Linux-only strict-mypy portability issue in the Windows benchmark memory sampler. The
  structural repair preserves Win32 `GetProcessMemoryInfo` behavior and unsupported-platform null
  measurements; native and explicit Linux strict mypy now pass across all 128 checked files.
- Follow-up Actions run `39887057629` passed strict mypy and migrations, then exposed a Linux-only
  replay append error-classification difference. The focused 30-test replay lane now passes with a
  platform-independent regression for the normalized append error.
- Final release Actions run `39888033270` passed every configured dependency, audit, formatting,
  lint, typing, migration, test, package, and installed-wheel gate at exact release commit
  `34159097d64bdb9cb0527798c9cf77044b2bf34a`.
- Patch 0.1.1 local gate: 617 tests passed in 37.06 seconds with zero skips or warnings and 95.64%
  branch-aware coverage; 11 focused benchmark tests passed, including the PostgreSQL workload,
  and the 30-test focused replay lane passed.
- Phase 13 source distribution built successfully; the wheel was built from its extracted source
  tree; Twine 6.2.0 accepted both archives with Core Metadata 2.4
- Final 0.1.1 sdist SHA-256:
  `b3943cd9bd14196d3db3e9e06e48f1f3d5fa2277f41889467bc1423064a2b9fe`
- Final 0.1.1 wheel SHA-256:
  `bd68d9fbdab70e622c6bd2622f6329c7bd3afb20820bb1934258df604a9c005d`
- Archive inspection: sdist contained 199 expected source, docs, test, migration, license, and smoke
  files; wheel contained 83 package/metadata/license files; development tools, local artifacts,
  caches, and raw benchmark payloads were absent
- Dependency audit: no known vulnerabilities in the resolved third-party environment; the local
  unpublished editable package was excluded from the frozen audit input
- Publication scan: working tree and all 38 commits had zero matches for user-home paths, employer
  terms, private keys, GitHub/AWS/OpenAI token formats, or long bearer-token formats after the
  exact historical home-path rewrite; `git fsck --full --unreachable` reported no residual objects
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
- GitHub Pages run `39906909040` built and deployed portfolio commit
  `286d5976c2fc7842d409217176e92f54e0c4d0a7`; the live home and SentinelMCP case-study pages were
  verified at desktop and mobile widths with no broken resources, console warnings, or horizontal
  overflow.

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

1. Keep the complete release gate green for future code changes.
2. Treat multi-process reliability coordination, identity-provider integration, and operator
   reconciliation as future design work rather than current capabilities.
3. Publish a later patch or minor release only after new code passes the same package and evidence
   gates; do not move the existing public tags.

## Resume commands

```powershell
$env:PYTHONPATH = '.devtools;.devtools\win32;.devtools\win32\lib'
$env:SENTINELMCP_TEST_DATABASE_URL = 'postgresql+psycopg://sentinelmcp_test@127.0.0.1:54329/sentinelmcp_test'
$python = 'python'
& $python -m pytest
& $python -m mypy
ruff format --check .
ruff check .
```

If PostgreSQL is not running:

```powershell
.\.devtools\postgresql-17.11\pgsql\bin\pg_ctl.exe -D .devtools\pgdata -l .devtools\postgres.log -o "-p 54329 -h 127.0.0.1" start
```

## Unresolved defects

- No known production-code defect is open. The public Actions runs found Linux-only strict-mypy
  and replay error-classification differences; both are repaired and the final release run is
  green. A Windows sandbox restriction prevents `asyncio` from creating its localhost wakeup
  socket, so asynchronous tests must run outside that network restriction; the code path itself
  passes there.
