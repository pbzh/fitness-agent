"""Database models using SQLModel (Pydantic + SQLAlchemy unified).

Single-user system on day one, but `user_id` is on every table so multi-user
is a non-event later.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


class WorkoutType(StrEnum):
    STRENGTH = "strength"
    HANGBOARD = "hangboard"
    TRX = "trx"
    CARDIO = "cardio"
    MOBILITY = "mobility"
    REST = "rest"
    MIXED = "mixed"


class IntensityLevel(StrEnum):
    LIGHT = "light"
    MODERATE = "moderate"
    HARD = "hard"
    MAX = "max"


class MealSlot(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    profile: Optional["UserProfile"] = Relationship(back_populates="user")


class UserProfile(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", unique=True)

    height_cm: float | None = None
    weight_kg: float | None = None
    birth_date: date | None = None
    sex: str | None = None

    primary_goal: str | None = None
    target_weight_kg: float | None = None
    weekly_workout_target: int = 4

    equipment: list[str] = Field(default_factory=list, sa_type=JSONB)
    dietary_restrictions: list[str] = Field(default_factory=list, sa_type=JSONB)
    daily_calorie_target: int | None = None
    macro_targets: dict | None = Field(default=None, sa_type=JSONB)

    notes: str | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User | None = Relationship(back_populates="profile")


class WorkoutPlan(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    week_start: date = Field(index=True)

    rationale: str | None = None
    generated_by_model: str | None = None
    generation_metadata: dict | None = Field(default=None, sa_type=JSONB)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    sessions: list["WorkoutSession"] = Relationship(back_populates="plan")


class WorkoutSession(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    plan_id: UUID | None = Field(default=None, foreign_key="workoutplan.id")

    scheduled_date: date = Field(index=True)
    workout_type: WorkoutType
    intensity: IntensityLevel = IntensityLevel.MODERATE
    duration_min: int

    exercises: list[dict] = Field(default_factory=list, sa_type=JSONB)
    notes: str | None = None

    completed: bool = False
    completed_at: datetime | None = None
    perceived_exertion: int | None = None
    completion_notes: str | None = None

    plan: WorkoutPlan | None = Relationship(back_populates="sessions")


class MealPlan(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    week_start: date = Field(index=True)

    rationale: str | None = None
    generated_by_model: str | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    meals: list["PlannedMeal"] = Relationship(back_populates="plan")


class PlannedMeal(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    plan_id: UUID | None = Field(default=None, foreign_key="mealplan.id")

    scheduled_date: date = Field(index=True)
    slot: MealSlot

    name: str
    recipe: str | None = None
    ingredients: list[dict] = Field(default_factory=list, sa_type=JSONB)

    calories: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None

    plan: MealPlan | None = Relationship(back_populates="meals")


class MealLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    eaten_at: datetime = Field(index=True)
    slot: MealSlot

    name: str
    calories: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    notes: str | None = None


class HealthMetric(SQLModel, table=True):
    """Wide-net table for time-series metrics: weight, HR, sleep, steps, HRV."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    recorded_at: datetime = Field(index=True)

    metric_type: str = Field(index=True)
    value: float
    source: str | None = None
    raw_data: dict | None = Field(default=None, sa_type=JSONB)


class AgentMessage(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    conversation_id: UUID = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    role: str
    content: str
    tool_calls: list[dict] | None = Field(default=None, sa_type=JSONB)
    model_used: str | None = None
