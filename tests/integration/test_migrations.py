"""Migration-level verification against a real PostgreSQL database."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from conftest import migration_config
from sentinelmcp.domain import RegistryUnavailableError
from sentinelmcp.registry import PostgresToolRegistry

pytestmark = pytest.mark.postgres


def test_fresh_upgrade_creates_expected_constraints_and_indexes(
    postgres_database_url: str,
) -> None:
    config = migration_config(postgres_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(postgres_database_url)
    try:
        inspector = inspect(engine)
        assert {
            "mutation_challenges",
            "mutation_idempotency",
            "tool_definitions",
        } <= set(inspector.get_table_names())
        constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("tool_definitions")
        }
        indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("tool_definitions")
        }
        assert constraints["uq_tool_definitions_exact_ref"] == (
            "namespace",
            "name",
            "version",
        )
        assert indexes["ix_tool_definitions_identity"] == ("namespace", "name")

        mutation_constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("mutation_idempotency")
        }
        assert mutation_constraints["uq_mutation_idempotency_scope"] == (
            "principal_issuer",
            "principal_subject",
            "namespace",
            "name",
            "version",
            "idempotency_key",
        )
        challenge_constraints = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("mutation_challenges")
        }
        assert challenge_constraints["uq_mutation_challenges_token_digest"] == ("token_digest",)

        command.downgrade(config, "base")
        assert "tool_definitions" not in inspect(engine).get_table_names()
        with pytest.raises(RegistryUnavailableError):
            PostgresToolRegistry(engine).list_tools()
        command.upgrade(config, "head")
        assert "tool_definitions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
