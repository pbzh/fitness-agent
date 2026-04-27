"""Inner Team profile settings

Revision ID: 0008_inner_team
Revises: 0007_admin_audit_log
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_inner_team"
down_revision: str | None = "0007_admin_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "userprofile",
        sa.Column(
            "inner_team",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("userprofile", "inner_team")

