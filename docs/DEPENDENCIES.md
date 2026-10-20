# Dependency Policy

Every production dependency must solve a concrete problem that the standard library or an
existing project dependency cannot solve safely. New dependencies require a design note that
covers maintenance activity, license, transitive footprint, security posture, and removal cost.

## Current dependencies

Phase 1 has no runtime dependencies. This is intentional: the repository currently contains
architecture contracts and package boundaries, not a gateway that pretends to run.

| Dependency | Scope | Reason |
| --- | --- | --- |
| Hatchling | Build only | Minimal PEP 517 backend for the `src` package layout |
| Pytest | Development | Test discovery, fixtures, parametrization, and future async/plugin ecosystem |
| pytest-cov | Development | Coverage measurement with branch support and CI output |
| Ruff | Development | One fast formatter and linter replaces several overlapping tools |
| mypy | Development | Strict static checking for public contracts and async control flow |

## Planned runtime dependencies

Runtime packages are added only in the phase that uses them. Expected choices include FastAPI
and Pydantic for the REST boundary, the official MCP SDK where it supports the required
transport, SQLAlchemy and asyncpg for PostgreSQL, Redis's supported Python client, HTTPX for
bounded async upstream calls, OpenTelemetry for traces, and prometheus-client for metrics.

The dependency list in the project brief is a design constraint, not permission to install
every package before code requires it.

## Versioning and reproducibility

Direct dependencies use bounded compatible ranges during development. A lockfile with hashes
will be introduced when the first runnable service exists. CI tests the minimum supported
Python version. Dependency updates must run the full test suite and any relevant benchmark
smoke tests.
