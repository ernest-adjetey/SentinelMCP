"""Safe failures for audit persistence and reconstruction."""

from sentinelmcp.domain import SentinelMCPError


class AuditUnavailableError(SentinelMCPError):
    """Raised when required audit state cannot be persisted or read."""

    code = "audit_unavailable"

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Audit storage is unavailable during {operation}")


class AuditIntegrityError(SentinelMCPError):
    """Raised when persisted audit state violates the event contract."""

    code = "audit_integrity_error"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Persisted audit state is invalid: {reason}")
