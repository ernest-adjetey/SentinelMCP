"""Structured domain errors exposed by the tool registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from sentinelmcp.domain.tools import ToolRef

type PathComponent = str | int
type SchemaKind = Literal["input", "output"]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A stable, serializable view of one JSON Schema validation failure."""

    instance_path: tuple[PathComponent, ...]
    schema_path: tuple[PathComponent, ...]
    message: str
    validator: str | None


class SentinelMCPError(Exception):
    """Base class for expected SentinelMCP domain failures."""

    code: ClassVar[str] = "sentinelmcp_error"


class InvalidToolReferenceError(SentinelMCPError):
    """Raised when a tool identifier or version is malformed."""

    code = "invalid_tool_reference"

    def __init__(self, *, component: str, value: object, reason: str) -> None:
        self.component = component
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid {component}: {reason}")


class InvalidToolDefinitionError(SentinelMCPError):
    """Raised when immutable definition metadata violates a domain invariant."""

    code = "invalid_tool_definition"

    def __init__(self, *, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Invalid tool definition field {field!r}: {reason}")


class InvalidSchemaError(SentinelMCPError):
    """Raised when a declared input or output schema is unsafe or malformed."""

    code = "invalid_schema"

    def __init__(
        self,
        *,
        schema_kind: SchemaKind,
        reason: str,
        issues: tuple[ValidationIssue, ...] = (),
    ) -> None:
        self.schema_kind = schema_kind
        self.reason = reason
        self.issues = issues
        super().__init__(f"Invalid {schema_kind} schema: {reason}")


class ToolNotFoundError(SentinelMCPError):
    """Raised when an exact tool reference or tool identity is absent."""

    code = "tool_not_found"

    def __init__(self, tool: object) -> None:
        self.tool = tool
        super().__init__(f"Tool not found: {tool}")


class ToolAlreadyExistsError(SentinelMCPError):
    """Available to persistence adapters that cannot express idempotent insertion directly."""

    code = "tool_already_exists"

    def __init__(self, tool_ref: ToolRef) -> None:
        self.tool_ref = tool_ref
        super().__init__(f"Tool version already exists: {tool_ref}")


class ImmutableToolVersionConflict(SentinelMCPError):
    """Raised when the same exact reference is proposed with different content."""

    code = "immutable_tool_version_conflict"

    def __init__(
        self,
        *,
        tool_ref: ToolRef,
        existing_fingerprint: str,
        proposed_fingerprint: str,
    ) -> None:
        self.tool_ref = tool_ref
        self.existing_fingerprint = existing_fingerprint
        self.proposed_fingerprint = proposed_fingerprint
        super().__init__(
            f"Tool version is immutable and conflicts with registered content: {tool_ref}"
        )


class RegistryUnavailableError(SentinelMCPError):
    """Raised when durable registry storage cannot complete an operation."""

    code = "registry_unavailable"

    def __init__(self, *, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Tool registry is unavailable during {operation}")


class RegistryIntegrityError(SentinelMCPError):
    """Raised when a persisted registry record violates storage invariants."""

    code = "registry_integrity_error"

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Tool registry integrity check failed: {reason}")


class _RuntimeValidationError(SentinelMCPError):
    """Shared structure for argument and output validation errors."""

    def __init__(self, *, tool_ref: ToolRef, issues: tuple[ValidationIssue, ...]) -> None:
        self.tool_ref = tool_ref
        self.issues = issues
        super().__init__(f"Validation failed for {tool_ref} with {len(issues)} issue(s)")


class ArgumentValidationError(_RuntimeValidationError):
    """Raised when tool arguments do not satisfy the registered input schema."""

    code = "argument_validation_failed"


class OutputValidationError(_RuntimeValidationError):
    """Raised when a tool result does not satisfy the registered output schema."""

    code = "output_validation_failed"
