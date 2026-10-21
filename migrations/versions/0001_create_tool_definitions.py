"""Create immutable tool definition storage.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_definitions",
        sa.Column("namespace", sa.String(length=191), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("canonical_definition", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "namespace",
            "name",
            "version",
            name="uq_tool_definitions_exact_ref",
        ),
    )
    op.create_index(
        "ix_tool_definitions_identity",
        "tool_definitions",
        ["namespace", "name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tool_definitions_identity", table_name="tool_definitions")
    op.drop_table("tool_definitions")
