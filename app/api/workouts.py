"""Direct REST endpoints for workouts.

The iOS app needs both a chat interface AND structured endpoints — chatting
through the LLM for every list query is wasteful. Use the agent for
planning/conversation, hit these endpoints for plain CRUD.
"""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_user_id
from app.db.models import WorkoutSession
from app.db.session import get_session

router = APIRouter(prefix="/workouts", tags=["workouts"])


@router.get("", response_model=list[WorkoutSession])
async def list_workouts(
    days: int = 14,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> list[WorkoutSession]:
    cutoff = date.today() - timedelta(days=days)
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .where(WorkoutSession.scheduled_date >= cutoff)
        .order_by(WorkoutSession.scheduled_date.desc())
    )
    return list(result.scalars().all())


@router.post("/{workout_id}/complete", response_model=WorkoutSession)
async def complete_workout(
    workout_id: UUID,
    rpe: int,
    notes: str | None = None,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> WorkoutSession:
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.id == workout_id)
        .where(WorkoutSession.user_id == user_id)
    )
    workout = result.scalar_one_or_none()
    if not workout:
        raise HTTPException(status_code=404, detail="Workout not found")

    workout.completed = True
    workout.completed_at = datetime.utcnow()
    workout.perceived_exertion = rpe
    workout.completion_notes = notes
    await session.flush()
    return workout
