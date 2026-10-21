"""Expected durable mutation-state failures without credential or payload contents."""

from __future__ import annotations

from enum import StrEnum

from sentinelmcp.domain.errors import SentinelMCPError


class ConfirmationRejectionReason(StrEnum):
    """Internal challenge rejection categories collapsed at public boundaries."""

    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    CONSUMED = "consumed"
    BINDING_MISMATCH = "binding_mismatch"


class ConfirmationRejectedError(SentinelMCPError):
    """Raised when a challenge cannot authorize the proposed exact mutation."""

    code = "confirmation_rejected"

    def __init__(self, reason: ConfirmationRejectionReason) -> None:
        self.reason = reason
        super().__init__("Mutation confirmation was rejected")


class IdempotencyConflictError(SentinelMCPError):
    """Raised when one scoped key is reused for a different semantic request."""

    code = "idempotency_conflict"

    def __init__(self) -> None:
        super().__init__("Idempotency key is already bound to a different request")


class MutationStateUnavailableError(SentinelMCPError):
    """Raised when durable mutation state cannot complete an operation."""

    code = "mutation_state_unavailable"

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Mutation state is unavailable during {operation}")


class MutationStateIntegrityError(SentinelMCPError):
    """Raised when persisted mutation state violates an internal invariant."""

    code = "mutation_state_integrity_error"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Mutation state integrity check failed: {reason}")


class InvalidMutationRequestError(SentinelMCPError):
    """Raised when a mutation request cannot be safely bound or persisted."""

    code = "invalid_mutation_request"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid mutation request: {reason}")
