"""Database models using SQLModel (Pydantic + SQLAlchemy unified).

Single-user system on day one, but `user_id` is on every table so multi-user
is a non-event later.
"""

from datetime import date, datetime, time
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


class WorkoutLocation(StrEnum):
    HOME = "home"
    GYM = "gym"
    OUTDOOR = "outdoor"
    CLIMBING_GYM = "climbing_gym"
    CRAG = "crag"
    OTHER = "other"


class FileKind(StrEnum):
    UPLOAD = "upload"
    GENERATED = "generated"


class CoachTask(StrEnum):
    """Persona/coach the message belongs to. Mirrors agent.router.TaskClass."""

    CHAT = "chat"
    PLAN_GENERATION = "plan_generation"
    NUTRITION_ANALYSIS = "nutrition_analysis"
    PROGRESS_REVIEW = "progress_review"
    MENTAL_HEALTH = "mental_health"


class User(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_admin: bool = False
    is_approved: bool = False
    approved_at: datetime | None = None

    profile: Optional["UserProfile"] = Relationship(back_populates="user")


class AdminAuditLog(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    actor_user_id: UUID = Field(foreign_key="user.id", index=True)
    target_user_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    action: str = Field(index=True)
    before: dict | None = Field(default=None, sa_type=JSONB)
    after: dict | None = Field(default=None, sa_type=JSONB)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


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
    # Per-coach system-prompt overrides. Keys are TaskClass values (e.g.
    # "chat", "mental_health"). Empty/missing keys fall back to the built-in
    # default in app.agent.prompts.
    coach_prompts: dict[str, str] | None = Field(default=None, sa_type=JSONB)
    # Per-coach provider override: task name -> "local"|"anthropic"|"openai".
    # Missing keys fall back to PROVIDER_FOR_X .env defaults.
    coach_providers: dict[str, str] | None = Field(default=None, sa_type=JSONB)
    # API keys, *encrypted* with app.security.secrets.encrypt(). Keys are
    # provider names: "anthropic" | "openai" | "local". Missing keys fall
    # back to the corresponding .env value.
    api_keys_enc: dict[str, str] | None = Field(default=None, sa_type=JSONB)
    # Privacy + i18n knobs.
    local_only: bool = False
    chat_retention_days: int | None = None  # None = keep forever
    preferred_language: str | None = None    # "en" | "de" | None=auto
    # Inner Team role state/config. This is a profile dashboard lens, not a
    # separate coach taxonomy.
    inner_team: dict | None = Field(default=None, sa_type=JSONB)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User | None = Relationship(back_populates="profile")


class WorkoutPlan(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    week_start: date = Field(index=True)

    rationale: str | None = None
    generated_by_model: str | None = None
    generation_metadata: dict | None = Field(default=None, sa_type=JSONB)
    image_file_id: UUID | None = Field(default=None, foreign_key="file.id")

    created_at: datetime = Field(default_factory=datetime.utcnow)

    sessions: list["WorkoutSession"] = Relationship(back_populates="plan")


class WorkoutSession(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    plan_id: UUID | None = Field(default=None, foreign_key="workoutplan.id")

    scheduled_date: date = Field(index=True)
    scheduled_time: time | None = None
    workout_type: WorkoutType
    intensity: IntensityLevel = IntensityLevel.MODERATE
    duration_min: int
    target_rpe: int | None = None
    location: WorkoutLocation | None = None

    exercises: list[dict] = Field(default_factory=list, sa_type=JSONB)
    notes: str | None = None
    warmup: str | None = None
    cooldown: str | None = None
    image_file_id: UUID | None = Field(default=None, foreign_key="file.id")

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
    image_file_id: UUID | None = Field(default=None, foreign_key="file.id")

    created_at: datetime = Field(default_factory=datetime.utcnow)

    meals: list["PlannedMeal"] = Relationship(back_populates="plan")


class PlannedMeal(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    plan_id: UUID | None = Field(default=None, foreign_key="mealplan.id")

    scheduled_date: date = Field(index=True)
    scheduled_time: time | None = None
    slot: MealSlot

    name: str
    recipe: str | None = None
    ingredients: list[dict] = Field(default_factory=list, sa_type=JSONB)
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    servings: int | None = None

    calories: int | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    image_file_id: UUID | None = Field(default=None, foreign_key="file.id")

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
    image_file_id: UUID | None = Field(default=None, foreign_key="file.id")


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
    task: str | None = None
    tool_calls: list[dict] | None = Field(default=None, sa_type=JSONB)
    model_used: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    attached_file_ids: list[str] = Field(default_factory=list, sa_type=JSONB)


class LoginAttempt(SQLModel, table=True):
    """Records every login attempt for audit and rate-limit analysis."""

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(index=True)
    user_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    success: bool = Field(index=True)
    ip_address: str | None = None
    user_agent: str | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class SystemConfig(SQLModel, table=True):
    """Single-row key/value store for admin-managed system settings.

    Each row is keyed by `key` (e.g. "smtp"). Value is JSONB so each config
    type can carry its own schema.  Sensitive fields (passwords) must be
    Fernet-encrypted before storage.
    """

    key: str = Field(primary_key=True)
    value: dict = Field(default_factory=dict, sa_type=JSONB)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class File(SQLModel, table=True):
    """User-uploaded files and agent-generated artifacts (images, PDFs).

    Bytes live on disk under settings.file_storage_dir; this row holds metadata
    and a relative storage_path.
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True)
    kind: FileKind = Field(index=True)

    filename: str
    mime_type: str
    size_bytes: int
    storage_path: str  # relative to settings.file_storage_dir

    description: str | None = None
    prompt: str | None = None  # for generated images: the prompt used

    linked_workout_plan_id: UUID | None = Field(default=None, foreign_key="workoutplan.id")
    linked_meal_plan_id: UUID | None = Field(default=None, foreign_key="mealplan.id")

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
