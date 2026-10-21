"""Real PostgreSQL race tests for immutable registration semantics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from sentinelmcp.domain import ImmutableToolVersionConflict, OperationClass, ToolDefinition
from sentinelmcp.domain.tools import ToolName, ToolNamespace, ToolVersion
from sentinelmcp.registry import PostgresToolRegistry, RegistrationStatus
from sentinelmcp.registry.postgres import tool_definitions

pytestmark = pytest.mark.postgres


def make_definition(
    version: str = "1.0.0",
    description: str = "Look up one catalog asset.",
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("catalog.asset"),
        name=ToolName("lookup"),
        version=ToolVersion(version),
        description=description,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        operation_class=OperationClass.READ,
    )


def durable_row_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(tool_definitions)).scalar_one()


def test_32_identical_registrations_have_one_logical_insert(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = make_definition()
    barrier = Barrier(32)

    def register() -> RegistrationStatus:
        barrier.wait()
        return postgres_registry.register(definition).status

    with ThreadPoolExecutor(max_workers=32) as executor:
        statuses = tuple(executor.map(lambda _: register(), range(32)))

    assert statuses.count(RegistrationStatus.REGISTERED) == 1
    assert statuses.count(RegistrationStatus.ALREADY_REGISTERED) == 31
    assert durable_row_count(postgres_engine) == 1


def test_conflicting_registrations_never_overwrite(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definitions = (
        make_definition(description="Definition A."),
        make_definition(description="Definition B."),
    )
    barrier = Barrier(2)

    def register(definition: ToolDefinition) -> tuple[str, ToolDefinition]:
        barrier.wait()
        try:
            result = postgres_registry.register(definition)
        except ImmutableToolVersionConflict:
            return ("conflict", definition)
        return (result.status.value, definition)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(register, definitions))

    assert sorted(outcome for outcome, _ in outcomes) == ["conflict", "registered"]
    winner = next(definition for outcome, definition in outcomes if outcome == "registered")
    assert postgres_registry.get(winner.ref) == winner
    assert durable_row_count(postgres_engine) == 1


def test_multiple_versions_register_independently(
    postgres_engine: Engine,
    postgres_registry: PostgresToolRegistry,
) -> None:
    definitions = tuple(make_definition(version) for version in ("1.0.0", "1.1.0", "2.0.0"))
    barrier = Barrier(3)

    def register(definition: ToolDefinition) -> RegistrationStatus:
        barrier.wait()
        return postgres_registry.register(definition).status

    with ThreadPoolExecutor(max_workers=3) as executor:
        statuses = tuple(executor.map(register, definitions))

    assert statuses == (RegistrationStatus.REGISTERED,) * 3
    assert postgres_registry.list_versions(definitions[0].identity) == tuple(
        definition.version for definition in definitions
    )
    assert durable_row_count(postgres_engine) == 3


def test_readers_observe_completed_registration(
    postgres_registry: PostgresToolRegistry,
) -> None:
    definition = make_definition()
    completed = Event()

    def register() -> None:
        postgres_registry.register(definition)
        completed.set()

    def read() -> ToolDefinition:
        assert completed.wait(timeout=5)
        return postgres_registry.get(definition.ref)

    with ThreadPoolExecutor(max_workers=9) as executor:
        writer = executor.submit(register)
        readers = tuple(executor.submit(read) for _ in range(8))
        writer.result()
        results = tuple(reader.result() for reader in readers)

    assert results == (definition,) * 8
