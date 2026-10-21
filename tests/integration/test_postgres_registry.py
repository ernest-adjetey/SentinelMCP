"""Durability, corruption, and failure tests for PostgreSQL storage."""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import text, update
from sqlalchemy.engine import Engine

from sentinelmcp.domain import (
    OperationClass,
    RegistryIntegrityError,
    RegistryUnavailableError,
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.registry import PostgresToolRegistry, ToolRegistry, create_postgres_engine
from sentinelmcp.registry.postgres import tool_definitions

pytestmark = pytest.mark.postgres


def make_definition(version: str = "1.0.0") -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion(version),
        description="Look up one catalog asset.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
    )


def test_definitions_survive_engine_and_registry_restart(
    postgres_database_url: str,
    postgres_engine: Engine,
) -> None:
    first = make_definition("1.0.0")
    latest = make_definition("2.0.0")
    registry_a = PostgresToolRegistry(postgres_engine)
    registry_a.register(first)
    registry_a.register(latest)
    postgres_engine.dispose()

    independent_engine = create_postgres_engine(postgres_database_url)
    try:
        registry_b = PostgresToolRegistry(independent_engine)
        assert registry_b.get(first.ref) == first
        assert registry_b.get(latest.ref) == latest
        assert registry_b.list_versions(first.identity) == (
            ToolVersion("1.0.0"),
            ToolVersion("2.0.0"),
        )
        assert registry_b.latest(first.identity) == latest
    finally:
        independent_engine.dispose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fingerprint", "0" * 64),
        ("canonical_definition", b"not-json"),
    ],
)
def test_corrupted_rows_raise_integrity_error(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
    field: str,
    value: object,
) -> None:
    definition = make_definition()
    postgres_registry.register(definition)
    with postgres_engine.begin() as connection:
        connection.execute(
            update(tool_definitions)
            .where(tool_definitions.c.version == "1.0.0")
            .values({field: value})
        )

    with pytest.raises(RegistryIntegrityError):
        postgres_registry.get(definition.ref)


def test_corrupted_existing_row_is_rejected_during_reregistration(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = make_definition()
    postgres_registry.register(definition)
    with postgres_engine.begin() as connection:
        connection.execute(update(tool_definitions).values(fingerprint="0" * 64))

    with pytest.raises(RegistryIntegrityError):
        postgres_registry.register(definition)


@pytest.mark.parametrize("field", ["namespace", "name"])
def test_corrupted_identity_is_rejected_during_listing(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
    field: str,
) -> None:
    definition = make_definition()
    postgres_registry.register(definition)
    with postgres_engine.begin() as connection:
        connection.execute(update(tool_definitions).values({field: "INVALID"}))

    with pytest.raises(RegistryIntegrityError):
        postgres_registry.list_tools()


def test_corrupted_version_is_rejected_during_version_listing(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = make_definition()
    postgres_registry.register(definition)
    with postgres_engine.begin() as connection:
        connection.execute(update(tool_definitions).values(version="not-semver"))

    with pytest.raises(RegistryIntegrityError):
        postgres_registry.list_versions(definition.identity)


def test_pool_pre_ping_recovers_from_terminated_connection(
    postgres_database_url: str,
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = make_definition()
    postgres_registry.register(definition)
    postgres_engine.dispose()
    with postgres_engine.connect() as stale_connection:
        backend_pid = cast(
            int,
            stale_connection.execute(text("select pg_backend_pid()")).scalar_one(),
        )
    terminator_engine = create_postgres_engine(postgres_database_url)
    try:
        with terminator_engine.connect() as terminator:
            terminated = cast(
                bool,
                terminator.execute(
                    text("select pg_terminate_backend(:backend_pid)"),
                    {"backend_pid": backend_pid},
                ).scalar_one(),
            )
            assert terminated is True
    finally:
        terminator_engine.dispose()

    assert postgres_registry.get(definition.ref) == definition


def invoke_registry_operation(registry: ToolRegistry, operation: str) -> object:
    reference = ToolRef.parse("catalog.asset.lookup@1.0.0")
    identity = ToolIdentity(reference.namespace, reference.name)
    if operation == "register":
        return registry.register(make_definition())
    if operation == "get":
        return registry.get(reference)
    if operation == "exists":
        return registry.exists(reference)
    if operation == "list_tools":
        return registry.list_tools()
    if operation == "list_versions":
        return registry.list_versions(identity)
    if operation == "latest":
        return registry.latest(identity)
    raise AssertionError(f"unknown test operation: {operation}")


@pytest.mark.parametrize(
    "operation",
    ["register", "get", "exists", "list_tools", "list_versions", "latest"],
)
def test_unavailable_database_is_translated_without_driver_details(operation: str) -> None:
    secret = "not-a-real-password"
    engine = create_postgres_engine(
        f"postgresql+psycopg://sentinel:{secret}@127.0.0.1:1/sentinel?connect_timeout=1"
    )
    registry = PostgresToolRegistry(engine)
    try:
        with pytest.raises(RegistryUnavailableError) as captured:
            invoke_registry_operation(registry, operation)
    finally:
        engine.dispose()

    assert captured.value.__cause__ is None
    assert secret not in str(captured.value)
    assert "sqlalchemy" not in str(captured.value).lower()
