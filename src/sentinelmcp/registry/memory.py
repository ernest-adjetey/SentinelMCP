"""Thread-safe in-memory tool registry used by tests and local development."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from typing import final

from sentinelmcp.domain.errors import ImmutableToolVersionConflict, ToolNotFoundError
from sentinelmcp.domain.tools import ToolDefinition, ToolIdentity, ToolRef, ToolVersion
from sentinelmcp.registry.fingerprint import canonicalize_tool_definition
from sentinelmcp.registry.protocol import (
    RegistrationResult,
    RegistrationStatus,
    ToolRegistry,
)
from sentinelmcp.registry.schema import ToolContractValidator, validate_tool_definition_schemas


@dataclass(frozen=True, slots=True)
class _RegistryRecord:
    definition: ToolDefinition
    canonical_definition: bytes
    fingerprint: str
    validator: ToolContractValidator


@final
class InMemoryToolRegistry(ToolRegistry):
    """Atomic process-local implementation of the immutable registry contract."""

    __slots__ = ("_lock", "_records")

    def __init__(self) -> None:
        self._records: dict[ToolRef, _RegistryRecord] = {}
        self._lock = RLock()

    def register(self, definition: ToolDefinition) -> RegistrationResult:
        validator = validate_tool_definition_schemas(definition)
        canonical = canonicalize_tool_definition(definition)
        fingerprint = sha256(canonical).hexdigest()

        with self._lock:
            existing = self._records.get(definition.ref)
            if existing is not None:
                if existing.canonical_definition == canonical:
                    return RegistrationResult(
                        status=RegistrationStatus.ALREADY_REGISTERED,
                        definition=existing.definition,
                        fingerprint=existing.fingerprint,
                    )
                raise ImmutableToolVersionConflict(
                    tool_ref=definition.ref,
                    existing_fingerprint=existing.fingerprint,
                    proposed_fingerprint=fingerprint,
                )

            self._records[definition.ref] = _RegistryRecord(
                definition=definition,
                canonical_definition=canonical,
                fingerprint=fingerprint,
                validator=validator,
            )
            return RegistrationResult(
                status=RegistrationStatus.REGISTERED,
                definition=definition,
                fingerprint=fingerprint,
            )

    def get(self, tool_ref: ToolRef) -> ToolDefinition:
        with self._lock:
            record = self._records.get(tool_ref)
            if record is None:
                raise ToolNotFoundError(tool_ref)
            return record.definition

    def exists(self, tool_ref: ToolRef) -> bool:
        with self._lock:
            return tool_ref in self._records

    def list_tools(self) -> tuple[ToolIdentity, ...]:
        with self._lock:
            return tuple(sorted({tool_ref.identity for tool_ref in self._records}))

    def list_versions(self, identity: ToolIdentity) -> tuple[ToolVersion, ...]:
        with self._lock:
            return tuple(
                sorted(
                    tool_ref.version for tool_ref in self._records if tool_ref.identity == identity
                )
            )

    def latest(self, identity: ToolIdentity) -> ToolDefinition:
        with self._lock:
            matching = [
                record
                for tool_ref, record in self._records.items()
                if tool_ref.identity == identity
            ]
            if not matching:
                raise ToolNotFoundError(identity)
            return max(matching, key=lambda record: record.definition.version).definition

    def validator_for(self, tool_ref: ToolRef) -> ToolContractValidator:
        """Return the validator compiled at registration for runtime enforcement."""

        with self._lock:
            record = self._records.get(tool_ref)
            if record is None:
                raise ToolNotFoundError(tool_ref)
            return record.validator
