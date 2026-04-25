"""Tools the agent can invoke.

Each tool opens its OWN short-lived session via deps.session_factory(),
so concurrent tool calls don't collide on a shared session.
"""

from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlmodel import select

from app.agent.agent import AgentDeps
from app.db.models import (
    HealthMetric,
    IntensityLevel,
    MealLog,
    MealSlot,
    UserProfile,
    WorkoutSession,
    WorkoutType,
)


class WorkoutSummary(BaseModel):
    date: date
    type: str
    intensity: str
    duration_min: int
    completed: bool
    rpe: int | None = None
    notes: str | None = None


class MealSummary(BaseModel):
    eaten_at: datetime
    slot: str
    name: str
    calories: int | None
    protein_g: float | None


class HealthMetricSummary(BaseModel):
    recorded_at: datetime
    metric_type: str
    value: float


class ProfileSummary(BaseModel):
    height_cm: float | None
    weight_kg: float | None
    primary_goal: str | None
    weekly_workout_target: int
    equipment: list[str]
    dietary_restrictions: list[str]
    daily_calorie_target: int | None
    macro_targets: dict | None
    notes: str | None


def register_tools(agent: Agent[AgentDeps, str]) -> None:
    @agent.tool
    async def get_user_profile(ctx: RunContext[AgentDeps]) -> ProfileSummary:
        """Get the user's profile: goals, equipment, dietary preferences, targets."""
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == ctx.deps.user_id)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                return ProfileSummary(
                    height_cm=None,
                    weight_kg=None,
                    primary_goal=None,
                    weekly_workout_target=4,
                    equipment=[],
                    dietary_restrictions=[],
                    daily_calorie_target=None,
                    macro_targets=None,
                    notes="No profile yet. Ask the user about goals and equipment.",
                )
            return ProfileSummary(
                height_cm=profile.height_cm,
                weight_kg=profile.weight_kg,
                primary_goal=profile.primary_goal,
                weekly_workout_target=profile.weekly_workout_target,
                equipment=profile.equipment,
                dietary_restrictions=profile.dietary_restrictions,
                daily_calorie_target=profile.daily_calorie_target,
                macro_targets=profile.macro_targets,
                notes=profile.notes,
            )

    @agent.tool
    async def get_recent_workouts(
        ctx: RunContext[AgentDeps],
        days: int = Field(default=14, description="Number of days to look back"),
    ) -> list[WorkoutSummary]:
        """Get workouts (planned + completed) from the last N days. Always call
        this before generating a new plan to inform progressive overload."""
        cutoff = date.today() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == ctx.deps.user_id)
                .where(WorkoutSession.scheduled_date >= cutoff)
                .order_by(WorkoutSession.scheduled_date.desc())
            )
            sessions = result.scalars().all()
            return [
                WorkoutSummary(
                    date=s.scheduled_date,
                    type=s.workout_type.value,
                    intensity=s.intensity.value,
                    duration_min=s.duration_min,
                    completed=s.completed,
                    rpe=s.perceived_exertion,
                    notes=s.completion_notes or s.notes,
                )
                for s in sessions
            ]

    @agent.tool
    async def log_completed_workout(
        ctx: RunContext[AgentDeps],
        scheduled_date: date,
        rpe: int = Field(ge=1, le=10, description="Rate of Perceived Exertion 1-10"),
        notes: str | None = None,
    ) -> str:
        """Mark a planned workout as completed. Use when the user reports finishing one."""
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == ctx.deps.user_id)
                .where(WorkoutSession.scheduled_date == scheduled_date)
                .where(WorkoutSession.completed == False)  # noqa: E712
            )
            session_obj = result.scalar_one_or_none()
            if not session_obj:
                return f"No planned workout found for {scheduled_date}. Create one first."

            session_obj.completed = True
            session_obj.completed_at = datetime.utcnow()
            session_obj.perceived_exertion = rpe
            session_obj.completion_notes = notes
            await session.commit()
            return f"Logged: {session_obj.workout_type.value} on {scheduled_date}, RPE {rpe}."

    @agent.tool
    async def create_workout_session(
        ctx: RunContext[AgentDeps],
        scheduled_date: date,
        workout_type: WorkoutType,
        intensity: IntensityLevel,
        duration_min: int,
        exercises: list[dict],
        notes: str | None = None,
        plan_id: UUID | None = None,
    ) -> str:
        """Create a planned workout session. Use when generating weekly plans
        or when the user requests an ad-hoc workout."""
        async with ctx.deps.session_factory() as session:
            ws = WorkoutSession(
                user_id=ctx.deps.user_id,
                plan_id=plan_id,
                scheduled_date=scheduled_date,
                workout_type=workout_type,
                intensity=intensity,
                duration_min=duration_min,
                exercises=exercises,
                notes=notes,
            )
            session.add(ws)
            await session.commit()
            return f"Scheduled {workout_type.value} for {scheduled_date} ({duration_min} min)."

    @agent.tool
    async def get_recent_meals(
        ctx: RunContext[AgentDeps],
        days: int = 7,
    ) -> list[MealSummary]:
        """Get logged meals from the last N days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(MealLog)
                .where(MealLog.user_id == ctx.deps.user_id)
                .where(MealLog.eaten_at >= cutoff)
                .order_by(MealLog.eaten_at.desc())
            )
            meals = result.scalars().all()
            return [
                MealSummary(
                    eaten_at=m.eaten_at,
                    slot=m.slot.value,
                    name=m.name,
                    calories=m.calories,
                    protein_g=m.protein_g,
                )
                for m in meals
            ]

    @agent.tool
    async def log_meal(
        ctx: RunContext[AgentDeps],
        slot: MealSlot,
        name: str,
        calories: int | None = None,
        protein_g: float | None = None,
        carbs_g: float | None = None,
        fat_g: float | None = None,
        eaten_at: datetime | None = None,
    ) -> str:
        """Log a meal the user has eaten. Estimate macros if not specified."""
        async with ctx.deps.session_factory() as session:
            meal = MealLog(
                user_id=ctx.deps.user_id,
                eaten_at=eaten_at or datetime.utcnow(),
                slot=slot,
                name=name,
                calories=calories,
                protein_g=protein_g,
                carbs_g=carbs_g,
                fat_g=fat_g,
            )
            session.add(meal)
            await session.commit()
            macros = ""
            if protein_g:
                macros = f" ({calories} kcal, {protein_g:.0f}g P)"
            return f"Logged {slot.value}: {name}{macros}"

    @agent.tool
    async def get_recent_health_metrics(
        ctx: RunContext[AgentDeps],
        metric_type: str = Field(
            description="e.g. 'weight_kg', 'resting_hr', 'hrv_ms', 'sleep_h', 'steps'"
        ),
        days: int = 30,
    ) -> list[HealthMetricSummary]:
        """Pull a time-series of a health metric ingested from Garmin/Apple Health."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                select(HealthMetric)
                .where(HealthMetric.user_id == ctx.deps.user_id)
                .where(HealthMetric.metric_type == metric_type)
                .where(HealthMetric.recorded_at >= cutoff)
                .order_by(HealthMetric.recorded_at.desc())
            )
            metrics = result.scalars().all()
            return [
                HealthMetricSummary(
                    recorded_at=m.recorded_at,
                    metric_type=m.metric_type,
                    value=m.value,
                )
                for m in metrics
            ]
