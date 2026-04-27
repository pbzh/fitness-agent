"""Per-user provider overrides + encrypted API keys

Revision ID: 0004_user_llm_overrides
Revises: 0003_coach_prompts
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_user_llm_overrides"
down_revision: str | None = "0003_coach_prompts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "userprofile",
        sa.Column(
            "coach_providers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "userprofile",
        sa.Column(
            "api_keys_enc",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("userprofile", "api_keys_enc")
    op.drop_column("userprofile", "coach_providers")
