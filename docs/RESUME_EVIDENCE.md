# Resume Evidence

This is the source of truth for public SentinelMCP claims. A claim is `VERIFIED` only when the
implementation, tests, retained evidence, reproduction command, measurement date, and limitations
are all identified here. CI on the public commit remains the publication authority.

## Control-plane implementation

- **Status:** VERIFIED
- **Exact claim:** Built a Python control plane that mediates exact-version REST and official MCP
  tool execution through authentication, schema validation, default-deny authorization, durable
  mutation confirmation and idempotency, bounded reliability controls, output validation, audit,
  metrics, and traces.
- **Source implementation:** [`application/executor.py`](../src/sentinelmcp/application/executor.py),
  [`application/mutations.py`](../src/sentinelmcp/application/mutations.py),
  [`adapters/rest/app.py`](../src/sentinelmcp/adapters/rest/app.py), and
  [`adapters/mcp/server.py`](../src/sentinelmcp/adapters/mcp/server.py).
- **Supporting tests:** [`tests/api`](../tests/api/), [`tests/mcp`](../tests/mcp/),
  [`tests/integration`](../tests/integration/), and [`tests/concurrency`](../tests/concurrency/).
- **Evidence:** The most recent complete local gate passed 617 tests with 95.64% branch-aware
  coverage on Windows 11, Python 3.12, and PostgreSQL 17.11. Ruff formatting and lint passed;
  strict mypy passed across 128 source, test, and release-smoke files.
- **Reproduction:** `ruff format --check .`, `ruff check .`, `mypy`, and `pytest` with
  `SENTINELMCP_TEST_DATABASE_URL` set to a migrated PostgreSQL 17 database.
- **Measured:** 2026-10-22.
- **Limitations:** The gate is local evidence. Reliability coordination and runtime bindings are
  process-local, and the project is not a hosted production deployment.

## Security and mutation safety

- **Status:** VERIFIED
- **Exact claim:** Implemented Ed25519 JWT verification, issuer/audience/time validation,
  default-deny exact-tool authorization, HTTPS-by-default outbound target validation and IP
  pinning, bounded REST ingress, and PostgreSQL-backed single-use mutation confirmations with
  scoped idempotency.
- **Source implementation:** [`policy/authentication.py`](../src/sentinelmcp/policy/authentication.py),
  [`policy/authorization.py`](../src/sentinelmcp/policy/authorization.py),
  [`execution/http.py`](../src/sentinelmcp/execution/http.py), and
  [`mutation_guard/postgres.py`](../src/sentinelmcp/mutation_guard/postgres.py).
- **Supporting tests:** [`test_authentication.py`](../tests/unit/test_authentication.py),
  [`test_executor_authorization.py`](../tests/unit/test_executor_authorization.py),
  [`test_http_adapter.py`](../tests/unit/test_http_adapter.py),
  [`test_gateway_security.py`](../tests/api/test_gateway_security.py), and the mutation and
  concurrency suites under [`tests`](../tests/).
- **Evidence:** The complete gate includes real PostgreSQL registry, mutation, audit, migration,
  restart, corruption, rollback, and concurrency lanes.
- **Reproduction:** `pytest tests/unit/test_authentication.py tests/unit/test_authorization.py
  tests/unit/test_http_adapter.py tests/api/test_gateway_security.py tests/api/test_mutation_gateway.py
  tests/concurrency` with the PostgreSQL test URL configured.
- **Measured:** 2026-10-22.
- **Limitations:** IP pinning protects the direct HTTP path, not a deployment-supplied proxy.
  Mutation delivery is not universal exactly-once; post-dispatch failures can require operator
  reconciliation.

## Reliability evaluation

- **Status:** VERIFIED
- **Exact claim:** Verified 23 of 23 deterministic fault scenarios against expected normalized
  error, attribution, and dispatch behavior: 8 tool errors, 11 infrastructure errors, 1 policy
  rejection, and 3 success/recovery cases.
- **Source implementation:** [`evaluation`](../src/sentinelmcp/evaluation/) and
  [`fault_injection`](../src/sentinelmcp/fault_injection/).
- **Supporting tests:** [`tests/evaluation`](../tests/evaluation/) and
  [`test_fault_injection.py`](../tests/unit/test_fault_injection.py).
- **Evidence:** [Phase 11 evaluation artifact](evidence/phase11-evaluation.json).
- **Reproduction:** `sentinel eval run --output phase11-evaluation.json` and
  `pytest tests/evaluation tests/unit/test_fault_injection.py tests/cli/test_cli.py`.
- **Measured:** 2026-10-21 America/Los_Angeles (2026-10-22 UTC).
- **Limitations:** The suite uses deterministic injected transports. It has zero model-routing
  scenarios and does not measure model accuracy, production failure rates, or service levels.

## Performance and profile-guided optimization

- **Status:** VERIFIED
- **Exact claim:** Profiled repeated immutable JSON Schema compilation, added a bounded validator
  cache, and measured concurrency-1 core throughput increasing from 624 to 5,051 requests/s while
  p95 latency fell from 2.443 ms to 0.251 ms.
- **Source implementation:** [`registry/schema.py`](../src/sentinelmcp/registry/schema.py) and
  [`benchmarking/runner.py`](../src/sentinelmcp/benchmarking/runner.py).
- **Supporting tests:** [`test_schema_validation.py`](../tests/unit/test_schema_validation.py),
  [`test_benchmarking.py`](../tests/unit/test_benchmarking.py), and
  [`test_benchmarking_postgres.py`](../tests/integration/test_benchmarking_postgres.py).
- **Evidence:** The final artifact contains 26,000 request samples over seven real composition
  paths, five trials per group, and no unexpected errors. At concurrency 1, authenticated
  in-process REST measured 1,041 requests/s at 1.160 ms p95, and PostgreSQL mutation
  preview/confirm measured 298 requests/s at 4.414 ms p95. See the
  [methodology](BENCHMARKS.md), [baseline](../benchmarks/results/phase12-windows-local-before-cache-20261022/),
  and [optimized evidence](../benchmarks/results/phase12-windows-local-after-cache-20261022/).
- **Reproduction:** `sentinel benchmark suite --output benchmark-results/local --trials 5
  --requests 200 --warmup 20 --concurrency 1,10,50,100 --postgres-concurrency 1,10` with the
  PostgreSQL test URL configured.
- **Measured:** 2026-10-22 UTC at commit `03b3ca06ae3c1eca0e1b2fb524179618fc71460d`.
- **Limitations:** Windows 11, Python 3.12.14, PostgreSQL 17.11, and a 22-logical-CPU Intel host.
  These are local synthetic measurements, not production capacity, external-network latency,
  multi-process scaling, or an SLO.

## Release artifact

- **Status:** VERIFIED
- **Exact claim:** Built SentinelMCP 0.1.0 as an sdist and wheel, validated distribution metadata,
  installed the wheel in an isolated environment, and exercised its console, core, REST, and
  official MCP paths without importing from the source checkout.
- **Source implementation:** [`pyproject.toml`](../pyproject.toml),
  [`ci.yml`](../.github/workflows/ci.yml), and
  [`installed_wheel_smoke.py`](../scripts/installed_wheel_smoke.py).
- **Supporting tests:** [`test_package_metadata.py`](../tests/unit/test_package_metadata.py) and the
  installed-wheel smoke script.
- **Evidence:** `pip check` passed; MCP negotiated protocol `2026-07-28`, discovered the three exact
  demo tools, and returned `{"sum": 5}`. Core and authenticated in-process REST returned the same
  result.
- **Reproduction:** `python -m build`, `python -m twine check dist/*`, install the wheel in a clean
  virtual environment, run `python -m pip check`, then run
  `python scripts/installed_wheel_smoke.py` with `PYTHONPATH` cleared.
- **Measured:** 2026-10-22.
- **Limitations:** This proves package integrity and representative local behavior, not a hosted
  deployment. Final artifact hashes are recorded only after the release commit is built.
