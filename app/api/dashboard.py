"""Dashboard summary for the chat side panel."""

from datetime import date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import get_approved_user_id
from app.db.models import HealthMetric, MealLog, UserProfile, WorkoutSession
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class MacroSummary(BaseModel):
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    calorie_target: int | None
    protein_target_g: float | None
    carbs_target_g: float | None
    fat_target_g: float | None


class WeightSummary(BaseModel):
    value_kg: float
    recorded_at: datetime
    source: str | None = None


class WorkoutSummary(BaseModel):
    id: UUID
    scheduled_date: date
    scheduled_time: str | None
    workout_type: str
    intensity: str
    duration_min: int
    completed: bool
    notes: str | None


class DashboardSummary(BaseModel):
    today: date
    macros: MacroSummary
    last_weight: WeightSummary | None
    today_workouts: list[WorkoutSummary]
    upcoming_workouts: list[WorkoutSummary]
    weekly_workout_target: int
    completed_this_week: int
    planned_this_week: int


def _workout_summary(row: WorkoutSession) -> WorkoutSummary:
    return WorkoutSummary(
        id=row.id,
        scheduled_date=row.scheduled_date,
        scheduled_time=row.scheduled_time.strftime("%H:%M") if row.scheduled_time else None,
        workout_type=row.workout_type.value,
        intensity=row.intensity.value,
        duration_min=row.duration_min,
        completed=row.completed,
        notes=row.notes,
    )


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> DashboardSummary:
    today = date.today()
    day_start = datetime.combine(today, time.min)
    day_end = day_start + timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=7)

    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        meals = (
            await session.execute(
                select(MealLog)
                .where(MealLog.user_id == user_id)
                .where(MealLog.eaten_at >= day_start)
                .where(MealLog.eaten_at < day_end)
            )
        ).scalars().all()
        weight = (
            await session.execute(
                select(HealthMetric)
                .where(HealthMetric.user_id == user_id)
                .where(HealthMetric.metric_type.in_(["weight_kg", "weight"]))
                .order_by(HealthMetric.recorded_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        today_workouts = (
            await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == user_id)
                .where(WorkoutSession.scheduled_date == today)
                .order_by(WorkoutSession.scheduled_time, WorkoutSession.created_at)
            )
        ).scalars().all()
        upcoming_workouts = (
            await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == user_id)
                .where(WorkoutSession.scheduled_date > today)
                .where(WorkoutSession.scheduled_date <= today + timedelta(days=7))
                .order_by(WorkoutSession.scheduled_date, WorkoutSession.scheduled_time)
                .limit(5)
            )
        ).scalars().all()
        week_workouts = (
            await session.execute(
                select(WorkoutSession)
                .where(WorkoutSession.user_id == user_id)
                .where(WorkoutSession.scheduled_date >= week_start)
                .where(WorkoutSession.scheduled_date < week_end)
            )
        ).scalars().all()

    targets = (profile.macro_targets if profile else None) or {}
    return DashboardSummary(
        today=today,
        macros=MacroSummary(
            calories=sum(m.calories or 0 for m in meals),
            protein_g=sum(m.protein_g or 0 for m in meals),
            carbs_g=sum(m.carbs_g or 0 for m in meals),
            fat_g=sum(m.fat_g or 0 for m in meals),
            calorie_target=profile.daily_calorie_target if profile else None,
            protein_target_g=targets.get("protein_g"),
            carbs_target_g=targets.get("carbs_g"),
            fat_target_g=targets.get("fat_g"),
        ),
        last_weight=(
            WeightSummary(
                value_kg=weight.value,
                recorded_at=weight.recorded_at,
                source=weight.source,
            )
            if weight
            else None
        ),
        today_workouts=[_workout_summary(w) for w in today_workouts],
        upcoming_workouts=[_workout_summary(w) for w in upcoming_workouts],
        weekly_workout_target=profile.weekly_workout_target if profile else 4,
        completed_this_week=sum(1 for w in week_workouts if w.completed),
        planned_this_week=len(week_workouts),
    )
