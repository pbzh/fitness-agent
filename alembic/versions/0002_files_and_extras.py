"""Files table, scheduled_time, mental-health task, message persistence extras

Revision ID: 0002_files_and_extras
Revises: 0001_initial_schema
Create Date: 2026-04-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_files_and_extras"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create enum types up front. Columns below reference them with
    # create_type=False so op.create_table / op.add_column don't try again.
    filekind = postgresql.ENUM("UPLOAD", "GENERATED", name="filekind", create_type=False)
    workoutlocation = postgresql.ENUM(
        "HOME", "GYM", "OUTDOOR", "CLIMBING_GYM", "CRAG", "OTHER",
        name="workoutlocation", create_type=False,
    )
    bind = op.get_bind()
    bind.execute(sa.text(
        "DO $$ BEGIN CREATE TYPE filekind AS ENUM ('UPLOAD','GENERATED');"
        " EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))
    bind.execute(sa.text(
        "DO $$ BEGIN CREATE TYPE workoutlocation AS ENUM "
        "('HOME','GYM','OUTDOOR','CLIMBING_GYM','CRAG','OTHER');"
        " EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    ))

    filekind_col = filekind
    workoutlocation_col = workoutlocation

    # ── file ──
    op.create_table(
        "file",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", filekind_col, nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("prompt", sa.String(), nullable=True),
        sa.Column("linked_workout_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("linked_meal_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["linked_workout_plan_id"], ["workoutplan.id"]),
        sa.ForeignKeyConstraint(["linked_meal_plan_id"], ["mealplan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_user_id", "file", ["user_id"])
    op.create_index("ix_file_kind", "file", ["kind"])
    op.create_index("ix_file_created_at", "file", ["created_at"])

    # ── workoutsession enrichments ──
    op.add_column("workoutsession", sa.Column("scheduled_time", sa.Time(), nullable=True))
    op.add_column("workoutsession", sa.Column("target_rpe", sa.Integer(), nullable=True))
    op.add_column("workoutsession", sa.Column("location", workoutlocation_col, nullable=True))
    op.add_column("workoutsession", sa.Column("warmup", sa.String(), nullable=True))
    op.add_column("workoutsession", sa.Column("cooldown", sa.String(), nullable=True))
    op.add_column(
        "workoutsession", sa.Column("image_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_workoutsession_image", "workoutsession", "file", ["image_file_id"], ["id"]
    )

    # ── plannedmeal enrichments ──
    op.add_column("plannedmeal", sa.Column("scheduled_time", sa.Time(), nullable=True))
    op.add_column("plannedmeal", sa.Column("prep_time_min", sa.Integer(), nullable=True))
    op.add_column("plannedmeal", sa.Column("cook_time_min", sa.Integer(), nullable=True))
    op.add_column("plannedmeal", sa.Column("servings", sa.Integer(), nullable=True))
    op.add_column(
        "plannedmeal", sa.Column("image_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_plannedmeal_image", "plannedmeal", "file", ["image_file_id"], ["id"]
    )

    # ── meallog enrichment ──
    op.add_column(
        "meallog", sa.Column("image_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key("fk_meallog_image", "meallog", "file", ["image_file_id"], ["id"])

    # ── plan-level images ──
    op.add_column(
        "workoutplan", sa.Column("image_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_workoutplan_image", "workoutplan", "file", ["image_file_id"], ["id"]
    )
    op.add_column(
        "mealplan", sa.Column("image_file_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key("fk_mealplan_image", "mealplan", "file", ["image_file_id"], ["id"])

    # ── agentmessage enrichments ──
    op.add_column("agentmessage", sa.Column("task", sa.String(), nullable=True))
    op.add_column("agentmessage", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("agentmessage", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "agentmessage",
        sa.Column(
            "attached_file_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agentmessage", "attached_file_ids")
    op.drop_column("agentmessage", "output_tokens")
    op.drop_column("agentmessage", "input_tokens")
    op.drop_column("agentmessage", "task")

    op.drop_constraint("fk_mealplan_image", "mealplan", type_="foreignkey")
    op.drop_column("mealplan", "image_file_id")
    op.drop_constraint("fk_workoutplan_image", "workoutplan", type_="foreignkey")
    op.drop_column("workoutplan", "image_file_id")

    op.drop_constraint("fk_meallog_image", "meallog", type_="foreignkey")
    op.drop_column("meallog", "image_file_id")

    op.drop_constraint("fk_plannedmeal_image", "plannedmeal", type_="foreignkey")
    op.drop_column("plannedmeal", "image_file_id")
    op.drop_column("plannedmeal", "servings")
    op.drop_column("plannedmeal", "cook_time_min")
    op.drop_column("plannedmeal", "prep_time_min")
    op.drop_column("plannedmeal", "scheduled_time")

    op.drop_constraint("fk_workoutsession_image", "workoutsession", type_="foreignkey")
    op.drop_column("workoutsession", "image_file_id")
    op.drop_column("workoutsession", "cooldown")
    op.drop_column("workoutsession", "warmup")
    op.drop_column("workoutsession", "location")
    op.drop_column("workoutsession", "target_rpe")
    op.drop_column("workoutsession", "scheduled_time")

    op.drop_index("ix_file_created_at", table_name="file")
    op.drop_index("ix_file_kind", table_name="file")
    op.drop_index("ix_file_user_id", table_name="file")
    op.drop_table("file")

    bind = op.get_bind()
    bind.execute(sa.text("DROP TYPE IF EXISTS workoutlocation"))
    bind.execute(sa.text("DROP TYPE IF EXISTS filekind"))
