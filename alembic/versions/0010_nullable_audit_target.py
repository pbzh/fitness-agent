"""Allow audit logs without a live target user

Revision ID: 0010_nullable_audit_target
Revises: 0009_login_attempts_smtp
Create Date: 2026-04-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_nullable_audit_target"
down_revision: str | None = "0009_login_attempts_smtp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "adminauditlog",
        "target_user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE adminauditlog SET target_user_id = actor_user_id "
        "WHERE target_user_id IS NULL"
    )
    op.alter_column(
        "adminauditlog",
        "target_user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
