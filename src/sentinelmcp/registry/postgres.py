"""Synchronous PostgreSQL implementation of the immutable tool registry."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from typing import Any, final

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import func

from sentinelmcp.domain.errors import (
    ImmutableToolVersionConflict,
    RegistryIntegrityError,
    RegistryUnavailableError,
    SentinelMCPError,
    ToolNotFoundError,
)
from sentinelmcp.domain.tools import (
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.registry.fingerprint import canonicalize_tool_definition
from sentinelmcp.registry.protocol import RegistrationResult, RegistrationStatus, ToolRegistry
from sentinelmcp.registry.schema import validate_tool_definition_schemas
from sentinelmcp.registry.serialization import reconstruct_tool_definition

TOOL_DEFINITION_UNIQUE_CONSTRAINT = "uq_tool_definitions_exact_ref"
TOOL_DEFINITION_IDENTITY_INDEX = "ix_tool_definitions_identity"

registry_metadata = MetaData()
tool_definitions = Table(
    "tool_definitions",
    registry_metadata,
    Column("namespace", String(191), nullable=False),
    Column("name", String(63), nullable=False),
    Column("version", String(128), nullable=False),
    Column("canonical_definition", LargeBinary(), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "namespace",
        "name",
        "version",
        name=TOOL_DEFINITION_UNIQUE_CONSTRAINT,
    ),
)
Index(
    TOOL_DEFINITION_IDENTITY_INDEX,
    tool_definitions.c.namespace,
    tool_definitions.c.name,
)


def create_postgres_engine(database_url: str) -> Engine:
    """Create a credential-redacting synchronous SQLAlchemy engine."""

    return create_engine(database_url, hide_parameters=True, pool_pre_ping=True)


@final
class PostgresToolRegistry(ToolRegistry):
    """Durable immutable registry using PostgreSQL uniqueness for arbitration."""

    __slots__ = ("_engine",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(self, definition: ToolDefinition) -> RegistrationResult:
        validate_tool_definition_schemas(definition)
        canonical = canonicalize_tool_definition(definition)
        fingerprint = sha256(canonical).hexdigest()
        statement = (
            insert(tool_definitions)
            .values(
                namespace=str(definition.namespace),
                name=str(definition.name),
                version=str(definition.version),
                canonical_definition=canonical,
                fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(constraint=TOOL_DEFINITION_UNIQUE_CONSTRAINT)
            .returning(tool_definitions.c.namespace)
        )
        try:
            with self._engine.begin() as connection:
                inserted = connection.execute(statement).one_or_none()
                if inserted is not None:
                    return RegistrationResult(
                        status=RegistrationStatus.REGISTERED,
                        definition=definition,
                        fingerprint=fingerprint,
                    )

                row = connection.execute(_exact_definition_query(definition.ref)).mappings().one()
                authoritative = _definition_from_row(row)
                authoritative_canonical = bytes(row["canonical_definition"])
                authoritative_fingerprint = _row_string(row, "fingerprint")
                if authoritative_canonical == canonical:
                    return RegistrationResult(
                        status=RegistrationStatus.ALREADY_REGISTERED,
                        definition=authoritative,
                        fingerprint=authoritative_fingerprint,
                    )
                raise ImmutableToolVersionConflict(
                    tool_ref=definition.ref,
                    existing_fingerprint=authoritative_fingerprint,
                    proposed_fingerprint=fingerprint,
                )
        except (ImmutableToolVersionConflict, RegistryIntegrityError):
            raise
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="registration") from None

    def get(self, tool_ref: ToolRef) -> ToolDefinition:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(_exact_definition_query(tool_ref)).mappings().one_or_none()
            if row is None:
                raise ToolNotFoundError(tool_ref)
            return _definition_from_row(row)
        except (RegistryIntegrityError, ToolNotFoundError):
            raise
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="exact lookup") from None

    def exists(self, tool_ref: ToolRef) -> bool:
        statement = (
            select(tool_definitions.c.namespace)
            .where(
                tool_definitions.c.namespace == str(tool_ref.namespace),
                tool_definitions.c.name == str(tool_ref.name),
                tool_definitions.c.version == str(tool_ref.version),
            )
            .limit(1)
        )
        try:
            with self._engine.connect() as connection:
                return connection.execute(statement).first() is not None
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="existence check") from None

    def list_tools(self) -> tuple[ToolIdentity, ...]:
        statement = (
            select(tool_definitions.c.namespace, tool_definitions.c.name)
            .distinct()
            .order_by(tool_definitions.c.namespace, tool_definitions.c.name)
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            return tuple(
                _persisted_identity(_row_string(row, "namespace"), _row_string(row, "name"))
                for row in rows
            )
        except RegistryIntegrityError:
            raise
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="tool listing") from None

    def list_versions(self, identity: ToolIdentity) -> tuple[ToolVersion, ...]:
        statement = select(tool_definitions.c.version).where(
            tool_definitions.c.namespace == str(identity.namespace),
            tool_definitions.c.name == str(identity.name),
        )
        try:
            with self._engine.connect() as connection:
                values: Sequence[object] = connection.execute(statement).scalars().all()
            return tuple(sorted(_persisted_version(value) for value in values))
        except RegistryIntegrityError:
            raise
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="version listing") from None

    def latest(self, identity: ToolIdentity) -> ToolDefinition:
        statement = select(tool_definitions).where(
            tool_definitions.c.namespace == str(identity.namespace),
            tool_definitions.c.name == str(identity.name),
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            if not rows:
                raise ToolNotFoundError(identity)
            definitions = tuple(_definition_from_row(row) for row in rows)
            return max(definitions, key=lambda definition: definition.version)
        except (RegistryIntegrityError, ToolNotFoundError):
            raise
        except SQLAlchemyError:
            raise RegistryUnavailableError(operation="latest-version lookup") from None


def clear_tool_definitions(engine: Engine) -> None:
    """Delete registry rows for isolated tests; never exposed by ToolRegistry."""

    with engine.begin() as connection:
        connection.execute(delete(tool_definitions))


def _exact_definition_query(tool_ref: ToolRef) -> Any:
    return select(tool_definitions).where(
        tool_definitions.c.namespace == str(tool_ref.namespace),
        tool_definitions.c.name == str(tool_ref.name),
        tool_definitions.c.version == str(tool_ref.version),
    )


def _definition_from_row(row: RowMapping) -> ToolDefinition:
    try:
        canonical = bytes(row["canonical_definition"])
    except (KeyError, TypeError, ValueError) as error:
        raise RegistryIntegrityError(reason="persisted canonical content is not binary") from error
    return reconstruct_tool_definition(
        canonical,
        stored_fingerprint=_row_string(row, "fingerprint"),
        stored_namespace=_row_string(row, "namespace"),
        stored_name=_row_string(row, "name"),
        stored_version=_row_string(row, "version"),
    )


def _row_string(row: RowMapping, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise RegistryIntegrityError(reason=f"persisted {field} is not a string")
    return value


def _persisted_identity(namespace: str, name: str) -> ToolIdentity:
    try:
        return ToolIdentity(namespace=ToolNamespace(namespace), name=ToolName(name))
    except SentinelMCPError as error:
        raise RegistryIntegrityError(reason="persisted tool identity is invalid") from error


def _persisted_version(value: object) -> ToolVersion:
    if not isinstance(value, str):
        raise RegistryIntegrityError(reason="persisted version is not a string")
    try:
        return ToolVersion(value)
    except SentinelMCPError as error:
        raise RegistryIntegrityError(reason="persisted version is invalid") from error
