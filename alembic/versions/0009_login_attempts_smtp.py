"""LoginAttempt and SystemConfig tables

Revision ID: 0009_login_attempts_smtp
Revises: 0008_inner_team
Create Date: 2026-04-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_login_attempts_smtp"
down_revision: str | None = "0008_inner_team"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loginattempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_loginattempt_email", "loginattempt", ["email"])
    op.create_index("ix_loginattempt_user_id", "loginattempt", ["user_id"])
    op.create_index("ix_loginattempt_success", "loginattempt", ["success"])
    op.create_index("ix_loginattempt_created_at", "loginattempt", ["created_at"])

    op.create_table(
        "systemconfig",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("systemconfig")
    op.drop_index("ix_loginattempt_created_at", "loginattempt")
    op.drop_index("ix_loginattempt_success", "loginattempt")
    op.drop_index("ix_loginattempt_user_id", "loginattempt")
    op.drop_index("ix_loginattempt_email", "loginattempt")
    op.drop_table("loginattempt")
