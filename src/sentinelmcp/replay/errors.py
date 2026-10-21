"""Stable replay errors without payload or endpoint disclosure."""

from __future__ import annotations

from uuid import UUID


class ReplayError(Exception):
    """Base class for replay workflow failures."""

    code = "replay_error"


class ReplayNotFoundError(ReplayError):
    code = "replay_record_not_found"

    def __init__(self, record_id: UUID) -> None:
        self.record_id = record_id
        super().__init__("Replay record was not found.")


class ReplayIntegrityError(ReplayError):
    code = "replay_integrity_error"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ReplayStoreUnavailableError(ReplayError):
    code = "replay_store_unavailable"

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Replay storage is unavailable during {operation}.")


class ReplayUnsafeError(ReplayError):
    code = "replay_target_unsafe"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ReplayContractDriftError(ReplayError):
    code = "replay_contract_drift"

    def __init__(self) -> None:
        super().__init__("The current exact tool contract does not match the replay record.")


class ReplayAuditUnavailableError(ReplayError):
    code = "replay_audit_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "Replay was not dispatched because its audit attempt could not be recorded."
        )
