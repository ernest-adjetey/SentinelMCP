"""Add durable mutation confirmations and idempotency records.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mutation_challenges",
        sa.Column("challenge_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("principal_issuer", sa.String(length=2048), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=191), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("definition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("arguments_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("challenge_id"),
        sa.UniqueConstraint("token_digest", name="uq_mutation_challenges_token_digest"),
    )
    op.create_index(
        "ix_mutation_challenges_expires_at",
        "mutation_challenges",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "mutation_idempotency",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("principal_issuer", sa.String(length=2048), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=191), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'succeeded', 'outcome_unknown')",
            name="ck_mutation_idempotency_state",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND outcome IS NULL) OR "
            "(state <> 'pending' AND outcome IS NOT NULL)",
            name="ck_mutation_idempotency_outcome",
        ),
        sa.PrimaryKeyConstraint("record_id"),
        sa.UniqueConstraint(
            "principal_issuer",
            "principal_subject",
            "namespace",
            "name",
            "version",
            "idempotency_key",
            name="uq_mutation_idempotency_scope",
        ),
    )
    op.create_index(
        "ix_mutation_idempotency_state",
        "mutation_idempotency",
        ["state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mutation_idempotency_state", table_name="mutation_idempotency")
    op.drop_table("mutation_idempotency")
    op.drop_index("ix_mutation_challenges_expires_at", table_name="mutation_challenges")
    op.drop_table("mutation_challenges")
