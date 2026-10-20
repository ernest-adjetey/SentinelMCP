"""Framework-independent domain types and invariants."""

from sentinelmcp.domain.errors import (
    ArgumentValidationError,
    ImmutableToolVersionConflict,
    InvalidSchemaError,
    InvalidToolDefinitionError,
    InvalidToolReferenceError,
    OutputValidationError,
    SentinelMCPError,
    ToolAlreadyExistsError,
    ToolNotFoundError,
    ValidationIssue,
)
from sentinelmcp.domain.tools import (
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)

__all__ = [
    "ArgumentValidationError",
    "ImmutableToolVersionConflict",
    "InvalidSchemaError",
    "InvalidToolDefinitionError",
    "InvalidToolReferenceError",
    "OperationClass",
    "OutputValidationError",
    "RetryPolicy",
    "SentinelMCPError",
    "TimeoutPolicy",
    "ToolAlreadyExistsError",
    "ToolDefinition",
    "ToolIdentity",
    "ToolName",
    "ToolNamespace",
    "ToolNotFoundError",
    "ToolRef",
    "ToolVersion",
    "ValidationIssue",
]
