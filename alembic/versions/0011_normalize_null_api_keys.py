"""Normalize JSON null API-key blobs to SQL NULL

Revision ID: 0011_normalize_null_api_keys
Revises: 0010_nullable_audit_target
Create Date: 2026-05-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_normalize_null_api_keys"
down_revision: str | None = "0010_nullable_audit_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE userprofile "
        "SET api_keys_enc = NULL "
        "WHERE jsonb_typeof(api_keys_enc) = 'null'"
    )


def downgrade() -> None:
    # Normalization is intentionally one-way; SQL NULL and JSON null are both
    # treated as empty by the application.
    pass
