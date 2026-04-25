"""User profile endpoints."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import get_current_user_id
from app.db.models import UserProfile
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileRead(BaseModel):
    id: UUID
    user_id: UUID
    height_cm: float | None
    weight_kg: float | None
    birth_date: date | None
    sex: str | None
    primary_goal: str | None
    target_weight_kg: float | None
    weekly_workout_target: int
    equipment: list[str]
    dietary_restrictions: list[str]
    daily_calorie_target: int | None
    macro_targets: dict[str, Any] | None
    notes: str | None


class ProfileUpdate(BaseModel):
    height_cm: float | None = None
    weight_kg: float | None = None
    birth_date: date | None = None
    sex: str | None = None
    primary_goal: str | None = None
    target_weight_kg: float | None = None
    weekly_workout_target: int | None = None
    equipment: list[str] | None = None
    dietary_restrictions: list[str] | None = None
    daily_calorie_target: int | None = None
    macro_targets: dict[str, Any] | None = None
    notes: str | None = None


@router.get("", response_model=ProfileRead)
async def get_profile(user_id: UUID = Depends(get_current_user_id)):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile


@router.put("", response_model=ProfileRead)
async def update_profile(
    update_data: ProfileUpdate, user_id: UUID = Depends(get_current_user_id)
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = result.scalar_one_or_none()

        if not profile:
            profile = UserProfile(user_id=user_id)
            session.add(profile)

        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(profile, key, value)

        profile.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
        return profile
