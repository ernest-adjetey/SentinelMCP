# ADR 0004: Make exact tool versions immutable and content-addressable

- Status: Accepted
- Date: 2026-10-20

## Context

Schema, operation class, timeout, and retry changes can alter whether a tool call is accepted,
authorized, retried, or interpreted successfully. If a registry row can be overwritten in
place, an audit event or mutation confirmation that names only a tool and version can silently
refer to different behavior later. The persistence implementation is not selected yet, but
these semantics must be fixed before gateway and storage work begins.

## Decision

Represent namespace, name, version, identity, and exact reference as validated value objects.
Use full SemVer 2.0.0 for versions. Make `ToolDefinition` deeply immutable and compute a
SHA-256 fingerprint over RFC 8785 canonical semantic content.

Register an exact reference once. Canonical-identical re-registration is an explicit idempotent
success; different content is an immutable-version conflict and never overwrites. Define the
registry as a persistence-neutral protocol and prove it first with a lock-protected in-memory
implementation.

Fix schema behavior to Draft 2020-12. Permit tested document-local references, reject remote or
relative retrieval, bound schema complexity, and return structured runtime validation issues.

## Alternatives considered

- **Mutable rows with update timestamps:** rejected because historical references would not
  identify the contract that governed an earlier call.
- **Content hash as the only identifier:** rejected because operators and clients still need a
  human-managed namespace and SemVer release vocabulary.
- **Permissive package version parser:** rejected because PEP 440 and loose parsers accept forms
  outside SemVer and can order prereleases differently.
- **Plain sorted JSON with a hand-written number encoder:** rejected because numeric and Unicode
  edge cases are correctness-sensitive and already standardized by RFC 8785.
- **Allow remote schema retrieval:** rejected because registration and runtime validation would
  gain SSRF, availability, cache-consistency, and time-of-check/time-of-use failure modes.
- **Database-first repository implementation:** rejected because storage details would define
  the domain contract before conflict and idempotency semantics were tested.

## Consequences

- Callers must publish a new version for every semantic change.
- Exact references remain suitable for later audit, replay, and confirmation bindings.
- Identical registration is naturally safe to retry.
- Fingerprints are stable across mapping insertion order and JSON number spelling, but they are
  not signatures or authorization evidence.
- Build metadata is part of exact identity but not SemVer precedence; lexical canonical text is
  used only to make equal-precedence ordering deterministic.
- A conservative local-reference subset rejects some otherwise valid Draft 2020-12 documents.
- PostgreSQL persistence must use a unique exact-reference key and transactional compare logic.
- In-memory locking protects threads in one process only; it offers no cross-process durability.

## Security and failure behavior

Schema validation performs no network I/O. Schema and runtime complexity limits reduce memory
and recursion abuse, and errors do not echo raw runtime values. Direct local-reference cycles
fail at registration. Regex backtracking and combinatorial schema evaluation remain documented
residual CPU risks until schema administration, deadlines, and execution isolation exist.

A SHA-256 collision is not used as proof that definitions are equal: the in-memory registry
compares canonical bytes. A future durable adapter should persist canonical content or an
equivalent collision-safe comparison path for exact-reference conflicts.

## Verification

- Example tests cover identifier grammar, SemVer precedence, fingerprints, references, schema
  limits, error paths, registration outcomes, version listing, and latest resolution.
- Property tests cover canonicalization order invariance, caller-mutation isolation,
  idempotency, version isolation, conflicts, and valid/invalid instances.
- A concurrent test verifies one insert and idempotent results for racing identical definitions.
- Ruff, strict mypy, warning-as-error pytest, branch coverage, package builds, and link checks are
  required before Phase 2 is complete.

## Interview-ready questions

1. **Why is a frozen dataclass insufficient?** Nested dictionaries and lists remain mutable, so
   SentinelMCP copies and recursively freezes them before computing identity or storing them.
2. **Why keep both SemVer and a fingerprint?** SemVer communicates release intent; the
   fingerprint detects exact semantic content. Neither replaces the other.
3. **Why is identical duplicate registration a success?** Registration can be retried after a
   lost response without forcing callers to guess whether the first request committed.
4. **Why compare canonical bytes after comparing hashes?** Hashes are compact diagnostics and
   indexes, but immutable correctness should not depend solely on collision resistance.
5. **Why reject remote `$ref` instead of caching it?** A cache still introduces retrieval trust,
   invalidation, SSRF, and reproducibility questions that are unnecessary for this phase.
6. **What database invariant preserves these semantics?** A unique exact-reference key plus a
   transaction that returns identical content or rejects a conflict without updating the row.
7. **What does `latest` not promise?** It does not mean latest stable, policy-approved, active,
   or backward-compatible; it is only the greatest deterministic SemVer ordering.
8. **What changes at 10x registry traffic?** Cache compiled validators and exact reads, measure
   canonicalization and database contention, and keep registration correctness in a unique
   transactional write path rather than weakening immutability.
