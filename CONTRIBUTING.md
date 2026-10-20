# Contributing

SentinelMCP is being built in small, reviewable phases. Contributions should preserve the
current phase boundaries and must not claim untested behavior.

## Before opening a change

1. Read `docs/ARCHITECTURE.md`, `docs/DESIGN_DECISIONS.md`, and `docs/THREAT_MODEL.md`.
2. Open an issue for changes that alter trust boundaries, delivery semantics, public schemas,
   persistence guarantees, or dependency choices.
3. Keep changes scoped. Do not combine infrastructure, behavior, and unrelated cleanup.
4. Add tests at the lowest useful layer and an integration test when a boundary changes.
5. Update the relevant design record when a tradeoff changes.

## Quality gates

```bash
ruff format --check .
ruff check .
mypy
pytest
```

Tests that require PostgreSQL, Redis, or synthetic services will be explicitly marked once
those dependencies are introduced. A skipped test must state which external prerequisite is
missing; product behavior must not be silently skipped.

## Commit guidance

Use focused commits with imperative subjects. Include the operational consequence of a
change in the body when it affects safety, compatibility, or recovery behavior.
