# Changelog

All notable changes to SentinelMCP are documented here. The project follows Semantic Versioning.

## [0.1.1] - 2026-10-22

### Fixed

- Kept Windows benchmark working-set measurement compatible with strict mypy analysis on Linux
  without changing the Win32 counters or unsupported-platform behavior.
- Normalized replay append storage failures across operating systems when an existing path cannot
  be read as a replay record.

## [0.1.0] - 2026-10-22

First public alpha release.

### Added

- Immutable exact-version tool contracts, Draft 2020-12 validation, RFC 8785 fingerprints, and
  PostgreSQL registration with migration and concurrency coverage.
- Signed-JWT authentication, default-deny authorization, bounded REST ingress, and an
  HTTPS-by-default outbound target policy with DNS/IP validation and connection pinning.
- Durable mutation preview, single-use confirmation, scoped idempotency, terminal recovery, and
  explicit unknown-outcome semantics.
- Request-wide deadlines, bounded read retries with full jitter, retry budgets, process-local rate
  limiting, and a generation-safe three-state circuit breaker.
- Payload-free PostgreSQL audit records, OpenTelemetry spans, Prometheus metrics, and a provisioned
  Grafana dashboard.
- Constrained replay, an operator CLI, official MCP SDK integration, deterministic fault
  evaluation, and reproducible profile-first benchmarks with committed raw evidence.

### Evidence

- 23 of 23 deterministic fault scenarios matched expected attribution and dispatch behavior.
- The committed Phase 12 Windows/Python 3.12 benchmark contains 26,000 request samples. The final
  concurrency-1 core result was 5,051 requests/s at 0.251 ms p95; this is a local synthetic
  measurement, not a production capacity claim.

### Limitations

- Reliability coordination and runtime bindings are process-local.
- Mutation delivery is not universal exactly-once; ambiguous external outcomes require operator
  reconciliation.
- No hosted deployment, OIDC/JWKS integration, distributed rate/circuit state, or production SLO
  is included in this release.

[0.1.1]: https://github.com/ernest-adjetey/SentinelMCP/releases/tag/v0.1.1
[0.1.0]: https://github.com/ernest-adjetey/SentinelMCP/releases/tag/v0.1.0
