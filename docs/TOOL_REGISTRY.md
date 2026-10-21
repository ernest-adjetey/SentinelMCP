# Tool Registry and Schemas

## Status and scope

Phase 2 established immutable tool contracts and schema validation. Phase 4 adds durable
PostgreSQL storage behind the same registry protocol. The registry still does not resolve
endpoints, load credentials, authorize callers, or negotiate compatible versions. Those
boundaries remain explicit so a registry record cannot quietly become an execution or
secret-storage mechanism.

## Typed identifiers

Identifiers are value objects, not unchecked strings:

- `ToolNamespace` is one to sixteen dot-separated segments with at most 191 total characters.
- `ToolName` is one segment.
- Each segment is 1-63 lowercase ASCII letters, digits, or hyphens. It starts with a letter and
  ends with a letter or digit. Values are rejected rather than normalized.
- `ToolVersion` is a complete SemVer 2.0.0 value, including optional prerelease and build
  metadata.
- `ToolIdentity` combines namespace and name. `ToolRef` adds an exact version.

The external reference form is `namespace.name@version`, for example
`catalog.asset.lookup@1.0.0`. Internally, registry methods accept the typed objects.

## Immutable definition

`ToolDefinition` contains only declarative contract metadata:

| Field | Purpose |
| --- | --- |
| Namespace, name, version | Stable exact identity |
| Description | Human-readable discovery text |
| Input and output schemas | Machine-enforced call contract |
| Operation class | Exactly `read` or `mutation` |
| Timeout policy | Future whole-call execution budget ceiling |
| Retry policy | Future bounded attempt and delay metadata |
| Idempotency support | Required before a mutation may declare multiple attempts |
| Capabilities | Bounded discovery labels with the identifier character set |

The frozen dataclass alone is not treated as sufficient. Schema mappings are copied,
recursively converted to immutable mappings and tuples, and detached from caller-owned data.
Capabilities and policy objects are immutable too. Mutating an original dictionary or list
after construction cannot change the definition.

Credentials, tokens, endpoint authentication, and secret values have no definition fields.
Endpoint binding is deferred until execution routing can apply SSRF and redirect controls.

## Version and registration semantics

An exact `ToolRef` includes the full SemVer text, including build metadata. Registration has
three outcomes:

1. A previously unseen exact reference is stored with `registered` status.
2. Canonical-identical content for an existing exact reference returns the authoritative
   stored definition with `already_registered` status.
3. Different content for an existing exact reference raises
   `ImmutableToolVersionConflict`; the original remains unchanged.

A different version is a different immutable record. `list_versions` returns ascending SemVer
precedence. SemVer says build metadata does not affect precedence, so equal-precedence build
variants use canonical version text as a deterministic tie-break. `latest` returns the maximum
of that total order and does not exclude prereleases.

Compatible-version ranges, stable-only selection, activation/deactivation, aliases, and
deprecation policy are deferred. Callers that require reproducibility should request an exact
`ToolRef`, not `latest`.

## Definition fingerprint

SentinelMCP serializes every semantic definition field with RFC 8785 JSON Canonicalization
Scheme and hashes those bytes with SHA-256. Object insertion order and capability set order do
not matter; array order does. JCS also normalizes equivalent JSON number representations such
as `1` and `1.0`.

The result is a 64-character lowercase hexadecimal digest. It supports conflict diagnostics,
cache keys, audit correlation, and later persistence checks. It is not a digital signature,
proof of authorship, authorization decision, or substitute for trusted storage. The in-memory
registry compares canonical bytes as well as fingerprints when deciding idempotency, so its
correctness does not rely only on the practical collision resistance of SHA-256.

RFC 8785 operates on the I-JSON number domain. Integers outside the interoperable IEEE 754
range and non-finite floats are rejected rather than fingerprinted inconsistently.

## JSON Schema policy

Schemas are interpreted only as JSON Schema Draft 2020-12. The `$schema` keyword may be absent,
in which case Draft 2020-12 is still used, or it may name the official Draft 2020-12 dialect.
Tool contracts require object-form schemas; boolean root schemas are intentionally unsupported.

Schema documents are untrusted. Registration enforces:

- maximum encoded size of 1,000,000 bytes;
- maximum 10,000 JSON nodes and 64 nesting levels;
- no nested `$schema` or `$id` resources;
- only document-local `$ref` and `$dynamicRef` values beginning with `#`;
- resolvable JSON Pointer or anchor targets that identify schema objects or booleans, unique
  anchors, and no direct reference cycles;
- successful validation against the Draft 2020-12 metaschema.

Relative, HTTP(S), file, and other external references are rejected before validator creation.
No retrieval handler is installed, and schema validation performs no network I/O. Local JSON
Pointers, `$anchor`, and `$dynamicAnchor` are supported. Recursive data schemas are supported
when recursion occurs through a nested instance location; nonproductive reference-only cycles
are rejected.

The reference scan is deliberately conservative: any `$ref`-named key in the submitted JSON
must follow the local-only policy, including one nested in data for a keyword such as `const`.
This favors a small auditable subset over accepting ambiguous documents.

`format` remains an annotation because no `FormatChecker` is installed. The byte, node, depth,
and reference bounds reduce memory, recursion, and retrieval risk, but they do not impose a CPU
or memory ceiling on the in-process Python validator. Pathological regular expressions and
nested combinators can still produce expensive evaluation within those structural limits.
Future hardening may add process isolation, execution deadlines, worker resource limits,
keyword-aware schema complexity budgets, or a more restricted schema subset if operational
evidence justifies it. Phase 2 provides none of those resource-isolation guarantees.

## Runtime validation

Input and output validators are compiled during registration and cached by the in-memory
registry. The executor compiles a validator from definitions reconstructed by PostgreSQL.
Runtime values must be finite JSON-compatible data with at most 100,000 nodes and 128
nesting levels. At most 32 schema failures plus one omission marker are returned.

`ArgumentValidationError` and `OutputValidationError` contain the exact tool reference and a
tuple of issues. Each issue has an instance path, schema path, generic message, and validator
keyword. Generic messages intentionally avoid embedding raw values that may be large or
sensitive.

## Registry interface

The runtime-checkable `ToolRegistry` protocol defines:

- `register(definition)`
- `get(tool_ref)`
- `exists(tool_ref)`
- `list_tools()`
- `list_versions(identity)`
- `latest(identity)`

The protocol returns domain types and has no SQL, ORM, or framework types. Missing exact and
latest lookups raise `ToolNotFoundError`; listing an unknown identity returns an empty tuple.

`InMemoryToolRegistry` is protected by a reentrant lock. Expensive schema compilation and
canonicalization happen before the short atomic check-and-insert section. The implementation
is suitable for tests and local process use, not durability or coordination between replicas.

`PostgresToolRegistry` stores canonical bytes and fingerprints through an Alembic-managed
schema. PostgreSQL uniqueness arbitrates races for one exact reference; identical content is
idempotent and different content never overwrites the winner. Definitions are reconstructed,
schema-validated, re-canonicalized, and fingerprint-checked on reads. Both implementations run
the same contract suite. See [PostgreSQL persistence](PERSISTENCE.md) for transaction,
migration, configuration, and failure details.

## Minimal example

```python
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.registry import InMemoryToolRegistry

definition = ToolDefinition(
    namespace=ToolNamespace("catalog.asset"),
    name=ToolName("lookup"),
    version=ToolVersion("1.0.0"),
    description="Look up one catalog asset.",
    input_schema={
        "type": "object",
        "properties": {"asset_id": {"type": "string"}},
        "required": ["asset_id"],
    },
    output_schema={"type": "object"},
    operation_class=OperationClass.READ,
)

registry = InMemoryToolRegistry()
result = registry.register(definition)
registry.validator_for(result.definition.ref).validate_arguments({"asset_id": "asset-1"})
```

`validator_for` is an in-memory convenience used by the future execution composition root; it
is not part of the persistence-neutral registry protocol.
