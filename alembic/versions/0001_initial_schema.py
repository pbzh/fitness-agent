"""Initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


workouttype = postgresql.ENUM(
    "STRENGTH",
    "HANGBOARD",
    "TRX",
    "CARDIO",
    "MOBILITY",
    "REST",
    "MIXED",
    name="workouttype",
    create_type=False,
)
intensitylevel = postgresql.ENUM(
    "LIGHT",
    "MODERATE",
    "HARD",
    "MAX",
    name="intensitylevel",
    create_type=False,
)
mealslot = postgresql.ENUM(
    "BREAKFAST",
    "LUNCH",
    "DINNER",
    "SNACK",
    name="mealslot",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    workouttype.create(bind, checkfirst=True)
    intensitylevel.create(bind, checkfirst=True)
    mealslot.create(bind, checkfirst=True)

    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    op.create_table(
        "userprofile",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(), nullable=True),
        sa.Column("primary_goal", sa.String(), nullable=True),
        sa.Column("target_weight_kg", sa.Float(), nullable=True),
        sa.Column("weekly_workout_target", sa.Integer(), nullable=False),
        sa.Column("equipment", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dietary_restrictions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("daily_calorie_target", sa.Integer(), nullable=True),
        sa.Column("macro_targets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "workoutplan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("generated_by_model", sa.String(), nullable=True),
        sa.Column("generation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workoutplan_week_start", "workoutplan", ["week_start"], unique=False)
    op.create_index("ix_workoutplan_user_id", "workoutplan", ["user_id"], unique=False)

    op.create_table(
        "mealplan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("rationale", sa.String(), nullable=True),
        sa.Column("generated_by_model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mealplan_user_id", "mealplan", ["user_id"], unique=False)
    op.create_index("ix_mealplan_week_start", "mealplan", ["week_start"], unique=False)

    op.create_table(
        "meallog",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eaten_at", sa.DateTime(), nullable=False),
        sa.Column("slot", mealslot, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("calories", sa.Integer(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meallog_user_id", "meallog", ["user_id"], unique=False)
    op.create_index("ix_meallog_eaten_at", "meallog", ["eaten_at"], unique=False)

    op.create_table(
        "healthmetric",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("metric_type", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_healthmetric_recorded_at", "healthmetric", ["recorded_at"], unique=False)
    op.create_index("ix_healthmetric_user_id", "healthmetric", ["user_id"], unique=False)
    op.create_index("ix_healthmetric_metric_type", "healthmetric", ["metric_type"], unique=False)

    op.create_table(
        "agentmessage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_used", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agentmessage_user_id", "agentmessage", ["user_id"], unique=False)
    op.create_index(
        "ix_agentmessage_conversation_id", "agentmessage", ["conversation_id"], unique=False
    )
    op.create_index("ix_agentmessage_created_at", "agentmessage", ["created_at"], unique=False)

    op.create_table(
        "workoutsession",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("workout_type", workouttype, nullable=False),
        sa.Column("intensity", intensitylevel, nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("exercises", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("perceived_exertion", sa.Integer(), nullable=True),
        sa.Column("completion_notes", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["workoutplan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workoutsession_scheduled_date", "workoutsession", ["scheduled_date"], unique=False
    )
    op.create_index("ix_workoutsession_user_id", "workoutsession", ["user_id"], unique=False)

    op.create_table(
        "plannedmeal",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("slot", mealslot, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("recipe", sa.String(), nullable=True),
        sa.Column("ingredients", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calories", sa.Integer(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["mealplan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plannedmeal_user_id", "plannedmeal", ["user_id"], unique=False)
    op.create_index(
        "ix_plannedmeal_scheduled_date", "plannedmeal", ["scheduled_date"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_plannedmeal_scheduled_date", table_name="plannedmeal")
    op.drop_index("ix_plannedmeal_user_id", table_name="plannedmeal")
    op.drop_table("plannedmeal")

    op.drop_index("ix_workoutsession_user_id", table_name="workoutsession")
    op.drop_index("ix_workoutsession_scheduled_date", table_name="workoutsession")
    op.drop_table("workoutsession")

    op.drop_index("ix_agentmessage_created_at", table_name="agentmessage")
    op.drop_index("ix_agentmessage_conversation_id", table_name="agentmessage")
    op.drop_index("ix_agentmessage_user_id", table_name="agentmessage")
    op.drop_table("agentmessage")

    op.drop_index("ix_healthmetric_metric_type", table_name="healthmetric")
    op.drop_index("ix_healthmetric_user_id", table_name="healthmetric")
    op.drop_index("ix_healthmetric_recorded_at", table_name="healthmetric")
    op.drop_table("healthmetric")

    op.drop_index("ix_meallog_eaten_at", table_name="meallog")
    op.drop_index("ix_meallog_user_id", table_name="meallog")
    op.drop_table("meallog")

    op.drop_index("ix_mealplan_week_start", table_name="mealplan")
    op.drop_index("ix_mealplan_user_id", table_name="mealplan")
    op.drop_table("mealplan")

    op.drop_index("ix_workoutplan_user_id", table_name="workoutplan")
    op.drop_index("ix_workoutplan_week_start", table_name="workoutplan")
    op.drop_table("workoutplan")

    op.drop_table("userprofile")

    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")

    bind = op.get_bind()
    mealslot.drop(bind, checkfirst=True)
    intensitylevel.drop(bind, checkfirst=True)
    workouttype.drop(bind, checkfirst=True)
