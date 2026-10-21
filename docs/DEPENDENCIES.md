# Dependency Policy

Every production dependency must solve a concrete problem that the standard library or an
existing project dependency cannot solve safely. New dependencies require a design note that
covers maintenance activity, license, transitive footprint, security posture, and removal cost.

## Current dependencies

| Dependency | Scope | Reason |
| --- | --- | --- |
| fastapi | Runtime | Minimal typed REST/ASGI boundary around the framework-neutral executor |
| httpx | Runtime | Async one-attempt HTTP transport and deterministic mock transport tests |
| jsonschema | Runtime | Standards-based Draft 2020-12 metaschema and instance validation |
| pydantic | Runtime | Strict REST request-envelope models used directly by the adapter |
| rfc8785 | Runtime | Correct JSON Canonicalization Scheme bytes without a custom number serializer |
| semver | Runtime | Strict SemVer 2.0.0 parsing and precedence instead of permissive package versions |
| SQLAlchemy | Runtime | Typed Core queries, explicit transactions, pooling, and PostgreSQL dialect support |
| psycopg | Runtime | Supported PostgreSQL 3 driver with binary wheels for local and CI environments |
| Alembic | Runtime tooling | Versioned, reviewable PostgreSQL schema upgrades and downgrades |
| PyJWT with crypto extra | Runtime | Standards-aware signed-JWT validation with asymmetric algorithm support |
| opentelemetry-api | Runtime | SDK-neutral tracing contracts and span instrumentation |
| prometheus-client | Runtime | Isolated metric registries and standards-compatible text exposition |
| Hatchling | Build only | Minimal PEP 517 backend for the `src` package layout |
| Hypothesis | Development | Generated invariant tests for canonicalization, registry state, and validation |
| Pytest | Development | Test discovery, fixtures, parametrization, and future async/plugin ecosystem |
| pytest-cov | Development | Coverage measurement with branch support and CI output |
| Ruff | Development | One fast formatter and linter replaces several overlapping tools |
| mypy | Development | Strict static checking for public contracts and async control flow |
| types-jsonschema | Development | PEP 561 stubs for the otherwise untyped jsonschema import boundary |
| opentelemetry-sdk | Development | In-memory span export and processor tests; deployment SDK remains host-configured |

### Phase 2 and 3 dependency review

Package license information below comes from the installed distribution metadata used for the
current quality run.

| Dependency | License | Footprint and security posture | Removal cost |
| --- | --- | --- | --- |
| fastapi | MIT | Pulls the ASGI and request-model stack; isolated to the REST adapter. | Medium: replace the edge adapter without changing the executor. |
| httpx | BSD-3-Clause | HTTP client used behind `HTTPToolAdapter`; redirects are disabled and tests use `MockTransport`. | Medium: another client must preserve streaming limits and failure classification. |
| jsonschema | MIT | Pulls `attrs`, `jsonschema-specifications`, `referencing`, and `rpds-py`; optional format extras are not installed. SentinelMCP pre-validates references and never delegates remote retrieval. | High: replacement must reproduce Draft 2020-12 behavior and error paths. |
| pydantic | MIT | Used only for strict public REST envelopes; semantic validation remains in domain/schema code. | Low to medium: replace request decoding at the edge. |
| rfc8785 | Apache-2.0 | Pure Python with no runtime dependencies. It rejects non-I-JSON numbers instead of silently producing unstable bytes. | Medium: any replacement needs cross-implementation canonicalization vectors. |
| semver | BSD-3-Clause | No mandatory runtime dependencies. It isolates precedence rules from package-manager version syntax. | Low to medium: a replacement must preserve exact parsing and prerelease ordering. |
| SQLAlchemy | MIT | Core-only database abstraction; PostgreSQL-specific insertion remains isolated to `registry.postgres`. | Medium: replacement must preserve transaction ownership, pooling, and dialect behavior. |
| psycopg | LGPL-3.0-only | PostgreSQL protocol driver; the `binary` extra supplies a prebuilt implementation for supported platforms. | Medium: another driver must preserve sync SQLAlchemy behavior and PostgreSQL error semantics. |
| Alembic | MIT | Migration runner built on SQLAlchemy; used by operators, CI, and migration tests rather than request paths. | Medium: replacing it requires an equivalent ordered schema history and downgrade policy. |
| PyJWT | MIT | Parses and verifies JWTs behind `JWTAuthenticator`; algorithms, issuer, audience, required claims, key IDs, and token size are constrained by SentinelMCP. | Medium: replacement must preserve the rejection matrix and avoid token-controlled key retrieval. |
| cryptography | Apache-2.0 OR BSD-3-Clause | Installed by PyJWT's `crypto` extra for Ed25519 verification; includes native components and transitively uses `cffi` and `pycparser` on this platform. | High: removing it requires another maintained asymmetric-signature implementation. |
| Hypothesis | MPL-2.0 | Development only; its required dependency is `sortedcontainers`. Generated examples are not shipped. | Low: example tests remain, but property coverage would be lost. |
| types-jsonschema | Apache-2.0 | Development only; contributes typing metadata and no runtime behavior. | Low: removal would require local protocols or targeted ignores. |

The inspected `jsonschema`, `semver`, and Hypothesis distributions classify themselves as
production/stable; `rfc8785` classifies itself as beta. The latter is bounded below version 1,
has no runtime dependencies, and is covered by canonicalization properties and fixed digest
tests. `types-jsonschema` tracks the jsonschema API and is development-only. These metadata
signals do not replace upgrade review: every direct dependency update runs the complete suite,
artifact build, and schema/fingerprint regression tests.

FastAPI and Pydantic are confined to the REST boundary. HTTPX is confined to the HTTP adapter
and API tests; the application executor imports none of them. Phase 3 tests exercise the exact
installed versions through ASGI and mock transports with warnings treated as errors.

### Phase 4 dependency choice

Synchronous SQLAlchemy and psycopg match the established synchronous `ToolRegistry` protocol.
Operations are short, engines are injected, and each method explicitly owns its connection or
transaction. An async database stack would add a second registry shape or force async through
the domain boundary before load measurements justify it. Alembic owns schema evolution;
production paths never auto-create tables.

### Phase 5 dependency choice

PyJWT 2.15.1 is bounded below major version 3 and supplies maintained JWT claim and signature
verification rather than custom cryptographic parsing. The `crypto` extra brings cryptography
50.0.2 for Ed25519 support. `JWTAuthenticator` remains the isolation boundary, so policy and
application code do not import PyJWT or cryptography. The complete rejection matrix covers
algorithm confusion, signature failure, unknown keys, malformed claims, issuer/audience, and
time validation. Installed metadata reports MIT for PyJWT, Apache-2.0 OR BSD-3-Clause for
cryptography, MIT-0 for `cffi`, and BSD-3-Clause for `pycparser`.

### Phase 8 dependency choice

`opentelemetry-api` is the only OpenTelemetry runtime dependency. Application protocols and the
instrumentation implementation do not import SDK providers, processors, samplers, or exporters. The
SDK is development-only so tests can inspect completed spans with the official in-memory exporter;
an embedding service selects its deployment SDK and export pipeline.

`prometheus-client` owns metric primitives and text exposition. SentinelMCP uses a private injected
`CollectorRegistry` instead of process-global registration, and every label value comes from a
finite enum or normalized error code. Replacing it would require compatible counter, gauge,
histogram, and exposition semantics.

## Planned runtime dependencies

Additional runtime packages are added only in the phase that uses them. Expected future choices
include the official MCP SDK where it supports the required transport and Redis's supported Python
client if coordination requirements emerge.

The dependency list in the project brief is a design constraint, not permission to install
every package before code requires it.

## Versioning and reproducibility

Direct dependencies use bounded compatible ranges during development. A lockfile with hashes
will be introduced when the first runnable service exists. CI tests the minimum supported
Python version. Dependency updates must run the full test suite and any relevant benchmark
smoke tests.
