"""Admin audit log

Revision ID: 0007_admin_audit_log
Revises: 0006_user_approval_admin
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_admin_audit_log"
down_revision: str | None = "0006_user_approval_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "adminauditlog",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_adminauditlog_actor_user_id", "adminauditlog", ["actor_user_id"])
    op.create_index("ix_adminauditlog_target_user_id", "adminauditlog", ["target_user_id"])
    op.create_index("ix_adminauditlog_action", "adminauditlog", ["action"])
    op.create_index("ix_adminauditlog_created_at", "adminauditlog", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_adminauditlog_created_at", table_name="adminauditlog")
    op.drop_index("ix_adminauditlog_action", table_name="adminauditlog")
    op.drop_index("ix_adminauditlog_target_user_id", table_name="adminauditlog")
    op.drop_index("ix_adminauditlog_actor_user_id", table_name="adminauditlog")
    op.drop_table("adminauditlog")
