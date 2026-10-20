"""Persistence-independent tool registry contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sentinelmcp.domain.tools import (
    ToolDefinition,
    ToolIdentity,
    ToolRef,
    ToolVersion,
)


class RegistrationStatus(StrEnum):
    """The two successful outcomes of immutable registration."""

    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """A successful registration result with the authoritative stored definition."""

    status: RegistrationStatus
    definition: ToolDefinition
    fingerprint: str


@runtime_checkable
class ToolRegistry(Protocol):
    """Storage-agnostic operations for immutable versioned tool definitions."""

    def register(self, definition: ToolDefinition) -> RegistrationResult:
        """Register or idempotently resolve one exact immutable definition."""

    def get(self, tool_ref: ToolRef) -> ToolDefinition:
        """Return one exact version or raise ``ToolNotFoundError``."""

    def exists(self, tool_ref: ToolRef) -> bool:
        """Report whether one exact version exists."""

    def list_tools(self) -> tuple[ToolIdentity, ...]:
        """Return all identities in deterministic lexical order."""

    def list_versions(self, identity: ToolIdentity) -> tuple[ToolVersion, ...]:
        """Return versions in ascending SemVer order, or an empty tuple."""

    def latest(self, identity: ToolIdentity) -> ToolDefinition:
        """Return the greatest registered SemVer version for an identity."""
