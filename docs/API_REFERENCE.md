# API Reference

SentinelMCP exposes REST and MCP adapters over one transport-independent executor. Every tool call
selects one exact `namespace.name@version`; no execution endpoint resolves `latest`.

## REST endpoints

### `POST /v1/execute`

Accepts an exact read tool and arguments:

```json
{
  "tool": {"namespace": "demo.math", "name": "add", "version": "1.0.0"},
  "arguments": {"a": 2, "b": 3}
}
```

Success is HTTP 200 with request/execution IDs, exact tool, validated result, and duration. A
registered mutation returns `mutation_not_supported`; use the two mutation endpoints instead.

### `POST /v1/mutations/preview`

Accepts the same `tool` and `arguments` envelope. Success is HTTP 201 with a challenge ID,
single-use confirmation token, definition fingerprint, expiry, normalized arguments, and generic
warning. It performs zero adapter dispatches.

### `POST /v1/mutations/confirm`

Adds `confirmation_token` and `idempotency_key` to the exact previewed envelope. Success is HTTP
200. Reuse with the same semantic request recovers durable state; conflicting reuse is rejected.

### `GET /metrics`

Present only when `PrometheusMetrics` is supplied to `create_execution_app`. Returns Prometheus
text exposition without payloads, identities, exact tools, URLs, or unbounded labels.

## REST status mapping

| Status | Representative normalized categories |
| ---: | --- |
| 400 | malformed `Content-Length` |
| 401 | `authentication_failed` |
| 403 | `authorization_failed` |
| 404 | `tool_not_found` |
| 409 | mutation confirmation/idempotency conflicts |
| 413 | request body exceeds configured limit |
| 422 | invalid envelope, reference, or arguments |
| 429 | `rate_limited` |
| 500 | `internal_execution_error` |
| 502 | adapter, outbound-policy, upstream contract/status failures |
| 503 | missing binding, open circuit, unavailable audit/state/dependency |
| 504 | deadline or upstream timeout |

The normalized error code is authoritative; HTTP status is an adapter mapping. Error envelopes do
not include raw arguments, upstream bodies, tokens, exception text, or stack traces.

## MCP tools

`tools/list` exposes exact versions as reversible names of the form
`s.<namespace>.<name>_<version>`. Reads accept the registered input schema. Mutations accept an
action envelope with `preview` or `confirm` and use the same `MutationService` as REST. See
[MCP integration](MCP.md) for authentication, transports, cancellation, and the 128-character
name limit.

## Python composition surfaces

- `sentinelmcp.application.ToolExecutor`
- `sentinelmcp.application.MutationService`
- `sentinelmcp.adapters.rest.create_execution_app`
- `sentinelmcp.adapters.mcp.create_mcp_server`
- `sentinelmcp.adapters.mcp.create_authenticated_streamable_http_app`
- `sentinelmcp.execution.OutboundHTTPPolicy`
- `sentinelmcp.execution.ReliabilityControls`

These are library composition points, not a managed configuration service. Read the
[execution lifecycle](EXECUTION_LIFECYCLE.md) and [configuration reference](CONFIGURATION.md)
before embedding them.
