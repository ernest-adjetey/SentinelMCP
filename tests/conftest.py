"""Shared fixtures for real PostgreSQL persistence tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from sentinelmcp.registry import PostgresToolRegistry, create_postgres_engine
from sentinelmcp.registry.postgres import clear_tool_definitions

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_ENV = "SENTINELMCP_TEST_DATABASE_URL"


def migration_config(database_url: str) -> Config:
    """Build an Alembic configuration without persisting credentials."""

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    database_url = os.environ.get(TEST_DATABASE_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_ENV} is not configured")
    return database_url


@pytest.fixture
def postgres_engine(postgres_database_url: str) -> Iterator[Engine]:
    command.upgrade(migration_config(postgres_database_url), "head")
    engine = create_postgres_engine(postgres_database_url)
    clear_tool_definitions(engine)
    try:
        yield engine
    finally:
        clear_tool_definitions(engine)
        engine.dispose()


@pytest.fixture
def postgres_registry(postgres_engine: Engine) -> PostgresToolRegistry:
    return PostgresToolRegistry(postgres_engine)
