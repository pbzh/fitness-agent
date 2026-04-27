"""User approval and admin flags

Revision ID: 0006_user_approval_admin
Revises: 0005_privacy_i18n
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_user_approval_admin"
down_revision: str | None = "0005_privacy_i18n"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "user",
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("user", sa.Column("approved_at", sa.DateTime(), nullable=True))

    op.execute(
        'UPDATE "user" '
        "SET is_approved = true, approved_at = COALESCE(approved_at, created_at)"
    )
    op.execute(
        'UPDATE "user" SET is_admin = true '
        'WHERE id = (SELECT id FROM "user" ORDER BY created_at ASC LIMIT 1)'
    )


def downgrade() -> None:
    op.drop_column("user", "approved_at")
    op.drop_column("user", "is_approved")
    op.drop_column("user", "is_admin")
