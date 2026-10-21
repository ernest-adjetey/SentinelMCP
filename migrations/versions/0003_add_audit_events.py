"""Add append-oriented durable audit events.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_mutation_idempotency_state",
        "mutation_idempotency",
        type_="check",
    )
    op.create_check_constraint(
        "ck_mutation_idempotency_state",
        "mutation_idempotency",
        "state IN ('pending', 'failed', 'succeeded', 'outcome_unknown')",
    )
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("principal_issuer", sa.String(length=2048), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=191), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("definition_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("operation_class", sa.String(length=16), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.CheckConstraint("schema_version = 1", name="ck_audit_events_schema_version"),
        sa.CheckConstraint(
            "kind IN ('execution_completed', 'mutation_previewed', "
            "'mutation_dispatch_intent', 'mutation_execution_completed', 'replay_attempted')",
            name="ck_audit_events_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('attempted', 'succeeded', 'failed')",
            name="ck_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "operation_class IS NULL OR operation_class IN ('read', 'mutation')",
            name="ck_audit_events_operation_class",
        ),
        sa.CheckConstraint(
            "(kind IN ('mutation_dispatch_intent', 'replay_attempted') "
            "AND outcome = 'attempted') OR "
            "(kind = 'mutation_previewed' AND outcome = 'succeeded') OR "
            "(kind IN ('execution_completed', 'mutation_execution_completed') "
            "AND outcome IN ('succeeded', 'failed'))",
            name="ck_audit_events_kind_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'attempted' AND error_code IS NULL AND duration_ms IS NULL "
            "AND attempt_count IS NULL) OR "
            "(outcome = 'succeeded' AND error_code IS NULL AND duration_ms IS NOT NULL "
            "AND attempt_count IS NOT NULL) OR "
            "(outcome = 'failed' AND error_code IS NOT NULL AND duration_ms IS NOT NULL "
            "AND attempt_count IS NOT NULL)",
            name="ck_audit_events_terminal_shape",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_audit_events_duration",
        ),
        sa.CheckConstraint(
            "attempt_count IS NULL OR (attempt_count >= 0 AND attempt_count <= 1000)",
            name="ck_audit_events_attempt_count",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_audit_events_request",
        "audit_events",
        ["request_id", "recorded_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_execution",
        "audit_events",
        ["execution_id", "recorded_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_recorded_at",
        "audit_events",
        ["recorded_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_recorded_at", table_name="audit_events")
    op.drop_index("ix_audit_events_execution", table_name="audit_events")
    op.drop_index("ix_audit_events_request", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_constraint(
        "ck_mutation_idempotency_state",
        "mutation_idempotency",
        type_="check",
    )
    op.execute("UPDATE mutation_idempotency SET state = 'outcome_unknown' WHERE state = 'failed'")
    op.create_check_constraint(
        "ck_mutation_idempotency_state",
        "mutation_idempotency",
        "state IN ('pending', 'succeeded', 'outcome_unknown')",
    )
