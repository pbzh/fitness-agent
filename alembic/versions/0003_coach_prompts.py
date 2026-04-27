"""Per-user coach prompt overrides

Revision ID: 0003_coach_prompts
Revises: 0002_files_and_extras
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_coach_prompts"
down_revision: str | None = "0002_files_and_extras"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "userprofile",
        sa.Column(
            "coach_prompts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("userprofile", "coach_prompts")
