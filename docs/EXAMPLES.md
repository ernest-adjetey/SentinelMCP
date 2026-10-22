# Examples

These examples describe implemented interfaces. Placeholder bearer tokens and confirmation
values are not credentials and must be replaced by deployment-owned values.

## REST execution

An authenticated gateway accepts one exact version and a JSON object of arguments:

```bash
curl --fail-with-body http://localhost:8000/v1/execute \
  -H "Authorization: Bearer $SENTINEL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool":{"namespace":"demo.math","name":"add","version":"1.0.0"},
    "arguments":{"a":2,"b":3}
  }'
```

The checked-in demo ASGI app denies every bearer token because no signing key is committed. An
embedding service must inject its own `Authenticator`, authorization rules, runtime bindings, TLS,
and trusted-proxy configuration. REST request bodies are bounded before JSON decoding.

## Official MCP SDK

The in-memory transport is useful for an embedding test with an explicitly trusted principal:

```python
import asyncio

from mcp import Client
from sentinelmcp.adapters.mcp import StaticMCPPrincipalResolver, create_mcp_server
from sentinelmcp.application import create_demo_runtime
from sentinelmcp.policy import Principal


async def main() -> None:
    runtime = create_demo_runtime()
    principal = Principal("local-example", "urn:sentinelmcp:example")
    server = create_mcp_server(
        runtime.registry,
        runtime.executor,
        principal_resolver=StaticMCPPrincipalResolver(principal),
    )
    async with Client(server) as client:
        result = await client.call_tool("s.demo.math.add_1.0.0", {"a": 2, "b": 3})
        print(result.structured_content)


asyncio.run(main())
```

Stdio uses the same composition-time principal trust. Streamable HTTP must use
`create_authenticated_streamable_http_app` with the existing bearer authenticator bridge. See
[MCP integration](MCP.md).

## Mutation preview and confirmation

Preview performs zero tool dispatches and returns a short-lived, request-bound token:

```bash
curl --fail-with-body http://localhost:8000/v1/mutations/preview \
  -H "Authorization: Bearer $SENTINEL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool":{"namespace":"account","name":"update","version":"1.0.0"},
    "arguments":{"display_name":"Ada"}
  }'
```

Confirm the same principal, exact version, fingerprint, and canonical arguments with a unique
idempotency key:

```bash
curl --fail-with-body http://localhost:8000/v1/mutations/confirm \
  -H "Authorization: Bearer $SENTINEL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool":{"namespace":"account","name":"update","version":"1.0.0"},
    "arguments":{"display_name":"Ada"},
    "confirmation_token":"TOKEN_FROM_PREVIEW",
    "idempotency_key":"caller-generated-operation-key"
  }'
```

This is at-most-one automatic local dispatch, not universal exactly-once delivery. Read
[Mutation safety](MUTATION_SAFETY.md) before binding a real side effect.

## Reliability controls

Built-in controls are explicit and process-local:

```python
from sentinelmcp.execution import (
    CircuitBreakerConfig,
    InMemoryCircuitBreaker,
    InMemoryRateLimiter,
    InMemoryRetryBudget,
    ReliabilityControls,
    TokenBucketConfig,
)

controls = ReliabilityControls(
    rate_limiter=InMemoryRateLimiter(TokenBucketConfig(100, 25.0)),
    retry_budget=InMemoryRetryBudget(TokenBucketConfig(20, 2.0)),
    circuit_breaker=InMemoryCircuitBreaker(
        CircuitBreakerConfig(failure_threshold=5, open_seconds=30.0)
    ),
)
```

Inject `controls` into `ToolExecutor`. Horizontal replicas do not share these limits. See
[Reliability controls](RELIABILITY.md).

## Observability

Inject `OpenTelemetryExecutionObservability`, `GatewayObservability`, and `PrometheusMetrics` into
the application composition. Expose the optional `/metrics` route through `create_execution_app`,
then start the checked-in local dashboard services:

```bash
docker compose up -d postgres prometheus grafana
```

Prometheus is available at `http://localhost:9090`; the local-only Grafana configuration is at
`http://localhost:3000`. Payloads, principals, exact tools, tokens, and request IDs are excluded
from metric labels. See [Observability](OBSERVABILITY.md) and [deployment assets](../deploy/README.md).

## Replay, evaluation, and benchmarks

```bash
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}' --record
sentinel replay RECORD_ID --dry-run
sentinel eval run --output phase11-evaluation.json
sentinel benchmark suite --output benchmark-results/local
```

The evaluation artifact and benchmark evidence are committed under `docs/evidence/` and
`benchmarks/results/`. Their numbers apply only to the documented deterministic scenarios and
recorded local environment.
