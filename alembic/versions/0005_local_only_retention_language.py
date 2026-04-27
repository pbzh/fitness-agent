"""Local-only mode, chat retention, preferred language

Revision ID: 0005_local_only_retention_language
Revises: 0004_user_llm_overrides
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_privacy_i18n"
down_revision: str | None = "0004_user_llm_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "userprofile",
        sa.Column(
            "local_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "userprofile",
        sa.Column("chat_retention_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "userprofile",
        sa.Column("preferred_language", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("userprofile", "preferred_language")
    op.drop_column("userprofile", "chat_retention_days")
    op.drop_column("userprofile", "local_only")
