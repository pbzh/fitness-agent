"""User profile endpoints."""

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from app.api.deps import get_current_user_id
from app.db.models import UserProfile
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/profile", tags=["profile"])


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


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
    height_cm: float | None = Field(default=None, ge=50, le=250)
    weight_kg: float | None = Field(default=None, ge=20, le=350)
    birth_date: date | None = None
    sex: Sex | None = None
    primary_goal: str | None = Field(default=None, max_length=80)
    target_weight_kg: float | None = Field(default=None, ge=20, le=350)
    weekly_workout_target: int | None = Field(default=None, ge=0, le=14)
    equipment: list[str] | None = Field(default=None, max_length=30)
    dietary_restrictions: list[str] | None = Field(default=None, max_length=30)
    daily_calorie_target: int | None = Field(default=None, ge=800, le=8000)
    macro_targets: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("equipment", "dietary_restrictions")
    @classmethod
    def validate_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 80 for item in cleaned):
            raise ValueError("List items must be 80 characters or fewer")
        return cleaned

    @field_validator("macro_targets")
    @classmethod
    def validate_macro_targets(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if len(json.dumps(value)) > 2000:
            raise ValueError("Macro targets are too large")
        for key, macro_value in value.items():
            if len(key) > 40:
                raise ValueError("Macro target keys must be 40 characters or fewer")
            if not isinstance(macro_value, int | float) or macro_value < 0:
                raise ValueError("Macro target values must be non-negative numbers")
        return value


@router.get("", response_model=ProfileRead)
async def get_profile(user_id: Annotated[UUID, Depends(get_current_user_id)]):
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
    update_data: ProfileUpdate,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
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
