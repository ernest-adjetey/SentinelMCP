# Model Context Protocol

## Scope

SentinelMCP uses the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
v2 line through the bounded dependency `mcp>=2.3,<3`. Phase 10 was implemented and tested against
2.3.0 and protocol revision `2026-07-28`. The adapter uses the SDK's low-level `Server` so registry
schemas and exact tool versions remain explicit.

MCP is an ingress protocol, not a second execution engine:

```text
MCP client -> MCP SDK transport -> principal resolver -> exact ToolRef
           -> ToolExecutor or MutationService -> existing binding/adapter
```

Input validation, authorization, reliability, output validation, audit, metrics, and tracing all
remain owned by the existing application lifecycle.

## Discovery and exact versions

`tools/list` returns every registered exact version in deterministic identity and SemVer order.
Read tools expose their registered input and output schemas. Mutation tools expose an MCP action
wrapper around their registered argument schema. Runtime endpoint and binding details are never
included.

MCP tool names use one reversible canonical encoding:

```text
s.<namespace>.<name>_<version>
```

The only SemVer character outside MCP's portable name alphabet is build metadata `+`, encoded as
`__`. For example:

```text
demo.math.add@1.1.0-rc.1+build.7
s.demo.math.add_1.1.0-rc.1__build.7
```

Sentinel namespaces, names, and SemVer values cannot contain `_`, so decoding is unambiguous.
Decode always reparses a strict `ToolRef` and re-encodes it to reject noncanonical names. There is
no latest-version fallback, version substitution, prerelease guessing, or build-metadata loss.

The MCP protocol limits a tool name to 128 characters. SentinelMCP refuses discovery when an
encoded registered reference exceeds that limit. It does not truncate, hash, omit, or alias the
reference. This means the more permissive Sentinel identifier limits are not all representable
through MCP; registration remains valid, but that registry cannot be exposed through this adapter
until a shorter exact reference is registered.

## Authentication and principals

Three identities are deliberately separate:

- MCP protocol identity describes client/server implementations and is not authorization input.
- Transport or session identity describes a connection and is not a Sentinel principal.
- `Principal` is SentinelMCP's bounded subject, issuer, roles, and scopes used by authorization.

`create_mcp_server` denies all requests by default. In-memory tests and trusted local stdio
composition may inject a `StaticMCPPrincipalResolver`; that is a composition-time trust decision,
not protocol authentication.

`create_authenticated_streamable_http_app` uses the SDK bearer middleware and an
`AuthenticatorTokenVerifier` bridge. The bridge passes the raw bearer token only to Sentinel's
existing `Authenticator`, then carries only its normalized principal fields into the application.
The caller must provide explicit `AuthSettings` and should validate the resource indicator. TLS,
token issuance, key delivery, and reverse-proxy correctness remain deployment responsibilities.

## Read invocation and errors

A read call decodes one exact reference and submits an `ExecutionRequest` to `ToolExecutor`.
Success returns the contract-valid tool output as `structuredContent` and JSON text, with bounded
request/execution metadata under MCP `_meta`.

Expected Sentinel failures are tool results with `isError: true`. Their structured envelope uses
the same finite error codes and safe messages as the core lifecycle. Raw upstream bodies,
exceptions, tokens, database details, confirmation tokens, idempotency keys, and stack traces are
not copied into errors. Discovery failures use generic MCP protocol errors because no tool result
exists yet.

## Mutations

MCP does not bypass the durable Phase 6 workflow. One exact mutation tool supports two explicit
actions:

```json
{"action":"preview","arguments":{"name":"Ada"}}
```

Preview validates and authorizes through `MutationService`, persists a PostgreSQL-backed challenge,
and performs zero adapter calls. It returns the single-use confirmation token, challenge ID,
definition fingerprint, expiry, and warning without echoing raw arguments.

```json
{
  "action":"confirm",
  "arguments":{"name":"Ada"},
  "confirmation_token":"opaque value from preview",
  "idempotency_key":"caller-selected-key"
}
```

Confirmation uses the same exact-reference, fingerprint, principal, argument digest, challenge,
idempotency, pre-dispatch audit, and unknown-outcome rules as REST. There is no MCP-only mutation
state. A server composed without `MutationService` returns `mutation_not_supported`.

## Transports, sessions, and cancellation

- Official in-memory `Client(server)` is the integration-test transport.
- `run_stdio_server` runs a composed server over stdio. Stdio has no bearer-auth guarantee; the
  configured principal resolver remains authoritative and the default resolver denies all calls.
- `create_authenticated_streamable_http_app` provides stateless JSON Streamable HTTP at `/mcp` by
  default. It uses official bearer middleware and Sentinel authentication.
- Obsolete SSE-specific APIs are not implemented.

Stateless HTTP does not create application authorization state in an MCP session. Durable mutation
state lives in PostgreSQL, and reliability state remains where the shared executor defines it.

Client cancellation through the in-memory SDK cancels an in-process waiting adapter task.
Sentinel's request-wide deadline remains the execution authority for slow adapters. A transport
disconnect or cancellation cannot prove that an external mutation was stopped after dispatch;
durable mutation ambiguity and `mutation_outcome_unknown` remain authoritative.

## Evidence

Focused tests are in:

- `tests/unit/test_mcp_names.py`
- `tests/unit/test_mcp_auth.py`
- `tests/mcp/test_mcp_integration.py`
- `tests/mcp/test_mcp_mutations.py`

They use the official SDK client/server path and real PostgreSQL for mutation state. The complete
quality gate remains `pytest` with `SENTINELMCP_TEST_DATABASE_URL` configured.
