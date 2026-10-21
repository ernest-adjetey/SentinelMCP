# Safe Replay

## Scope

Phase 9 adds an operator-controlled replay workflow for exact historical requests. Replay is a
new execution under current policy, not a continuation of the original execution and not a way
to reproduce an external side effect. The default policy accepts only registered read tools that
declare the `replay-safe` capability and are currently bound to an in-process
`SyntheticToolAdapter`.

The original request and execution identifiers are retained as historical correlation fields in
the record and replay-attempt audit event. The replayed executor invocation creates its own live
execution context.

## Record contract

`ReplayRecord` is immutable and currently uses schema version `1`. It contains:

- a UUID record ID and timezone-aware capture time;
- the verified principal issuer and subject from the captured execution;
- the exact `ToolRef` and its historical SHA-256 definition fingerprint;
- the original trusted request and execution IDs;
- the registered read or mutation operation class; and
- a detached JSON object containing the validated arguments.

Arguments are limited to 65,536 canonical JSON bytes. Recursive field-name checks reject common
credential names such as `authorization`, `password`, `secret`, `token`, and normalized variants.
The schema rejects missing, extra, malformed, oversized, duplicate-key, or unsupported fields.

This is a defensive minimum, not data-loss prevention. A secret under an innocuous field name
cannot be identified reliably. Operators must record only synthetic, non-sensitive requests, and
tool authors must not mark tools replay-safe when arguments can contain credentials or private
data.

## Local file store

The CLI uses `FileReplayStore`, with one append-only JSON file per UUID under
`.sentinelmcp/replays` by default. `SENTINELMCP_REPLAY_DIR` or `--store` selects another directory.
Creation uses exclusive file creation, flushes and synchronizes each record, and requests mode
`0600` where the platform honors POSIX file modes. Re-appending identical content is idempotent;
conflicting content for an existing ID is an integrity error. Reads are limited to 131,072 bytes
and reject invalid UTF-8, duplicate JSON keys, malformed filenames, or schema corruption.

The file store is intended for a single trusted host. It does not provide encryption, signing,
cross-process transaction coordination, retention, archival, access-control management, or
tamper-proof storage. Directory permissions, backup, deletion, and privacy retention remain
operator responsibilities.

## Replay decision

Before a dry run or replay, `ReplayService`:

1. loads the strict record by ID;
2. resolves the exact current definition and binding;
3. requires a read tool, the `replay-safe` capability, and a synthetic adapter;
4. recomputes the current definition fingerprint and rejects drift;
5. writes a `replay_attempted` audit intent; and
6. runs current input validation and authorization through `ToolExecutor`.

An audit failure denies the replay before dispatch. A dry run stops after current validation and
authorization and returns a plan. A real replay supplies the target policy as an executor binding
guard. The executor evaluates that guard after its final binding lookup and immediately before
dispatch, so replacing an approved synthetic binding with an HTTP binding cannot win a
check-to-use race.

Historical mutation records can be represented and inspected, but the default target policy
cannot execute them. Extending `ReplayTargetPolicy` is a privileged embedding decision and must
preserve current authorization, pre-dispatch audit, and target identity guarantees.

## Audit limitation

The standalone demo CLI composes `DurableAuditRecorder` with `InMemoryAuditStore`. Replay attempts
are therefore audited for the lifetime of that command process but are not durable across CLI
invocations. An embedded or hosted deployment requiring durable operator evidence must inject the
PostgreSQL audit store. Replay never silently proceeds when its configured audit recorder fails.

## CLI workflow

```bash
sentinel execute demo.math.add@1.0.0 --arguments '{"a":2,"b":3}' --record
sentinel inspect RECORD_ID
sentinel replay RECORD_ID --dry-run
sentinel replay RECORD_ID
```

See [CLI reference](CLI.md) for command and output details.
