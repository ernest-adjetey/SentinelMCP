# ADR 0005: Separate contract identity from runtime binding

- Status: Accepted
- Date: 2026-10-20

## Context

Phase 2 made an exact `ToolDefinition` immutable and fingerprinted its schemas and semantic
execution metadata. Phase 3 must route that contract to an in-process handler or HTTP target.
Endpoints, deployments, credentials, and adapter configuration change for operational reasons
even when the promised input/output contract does not.

Putting runtime targets into `ToolDefinition` would make deployment changes look like semantic
contract changes, force needless version publication, and bind replay evidence to transient
infrastructure. It would also couple the registry to specific transports.

## Decision

Keep `ToolDefinition` endpoint-independent. Introduce `RuntimeToolBinding` as mutable operational
state mapping one exact `ToolRef` to a configured `ToolAdapter`.

The executor resolves the immutable definition first, validates arguments, and then resolves the
binding for that same exact reference. It never substitutes `latest`. The adapter protocol is
transport-neutral; Phase 3 implements synthetic and HTTP adapters without adding their targets
to the definition fingerprint.

## Consequences

- Contract fingerprints remain stable across deployment and routing changes.
- An endpoint can move without publishing a fake schema version.
- One contract can be tested through alternate adapters.
- Historical records can identify the exact semantic contract separately from the route used.
- Bindings need their own configuration validation, lifecycle, access controls, and eventual
  persistence or deployment source.
- Replacing a process-local binding is atomic only inside one application process.
- Later audit records should capture both the exact contract fingerprint and a safe binding or
  target revision identifier without persisting credentials.

## Alternatives considered

- **Store endpoints in `ToolDefinition`:** rejected because mutable deployment topology would
  alter semantic identity and fingerprinting.
- **Let adapters select versions:** rejected because a request for one exact version could run a
  different contract.
- **Hard-code routing in the executor:** rejected because it couples orchestration to HTTP,
  synthetic handlers, and future MCP/subprocess transports.
- **Use latest-version bindings only:** rejected because latest is not reproducible and may select
  prereleases or incompatible contracts.

## Security and limitations

The separation does not itself make runtime targets safe. Phase 3 rejects embedded URL
credentials and redirects but does not implement endpoint allowlists, DNS/IP restrictions,
credential injection, or administrative authorization. Those controls must wrap binding
administration and HTTP dispatch before untrusted targets are supported.

## Verification

- Tests prove exact-version lookup occurs before binding resolution.
- Missing bindings never dispatch.
- Replacing or removing a binding does not mutate the registered definition.
- Synthetic and HTTP adapters satisfy the same protocol.
- The package dependency graph keeps FastAPI and HTTPX outside the core executor.
