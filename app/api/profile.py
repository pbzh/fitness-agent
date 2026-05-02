"""User profile endpoints."""

import json
import unicodedata
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select

from app.agent.prompts import COACH_META, default_prompts
from app.agent.router import Provider, TaskClass, _env_provider_for
from app.api.deps import get_approved_user_id
from app.config import get_settings
from app.db.models import UserProfile
from app.db.session import AsyncSessionLocal
from app.inner_team import normalize_inner_team
from app.security.secrets import encrypt

_MAX_PROMPT_CHARS = 8000
_API_KEY_PROVIDERS = {p.value for p in Provider}

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
    coach_prompts: dict[str, str] | None
    inner_team: dict[str, Any] | None


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
    # Per-coach system-prompt overrides. Keys must be editable coach task names
    # (see GET /profile/coach-prompts/defaults). Empty string clears the
    # override and falls back to the built-in default.
    coach_prompts: dict[str, str] | None = None

    @field_validator("equipment", "dietary_restrictions")
    @classmethod
    def validate_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 80 for item in cleaned):
            raise ValueError("List items must be 80 characters or fewer")
        return cleaned

    @field_validator("coach_prompts")
    @classmethod
    def validate_coach_prompts(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        cleaned: dict[str, str] = {}
        for key, prompt in value.items():
            if key not in COACH_META:
                raise ValueError(f"Unknown coach: {key}")
            if not isinstance(prompt, str):
                raise ValueError("Prompt values must be strings")
            if len(prompt) > _MAX_PROMPT_CHARS:
                raise ValueError(
                    f"Prompt for '{key}' exceeds {_MAX_PROMPT_CHARS} chars"
                )
            cleaned[key] = prompt
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


class CoachPromptDefault(BaseModel):
    task: str
    label: str
    editable: bool
    default_prompt: str


class LLMConfigRead(BaseModel):
    """Per-user LLM routing + API key visibility.

    Returns provider overrides verbatim (with the .env default as a fallback
    indicator), and only "set"/"unset" status for API keys — never the
    plaintext or ciphertext.
    """

    coach_providers: dict[str, str]    # task -> "local"|"anthropic"|"openai"
    env_providers: dict[str, str]      # task -> .env default (read-only)
    provider_models: dict[str, str]    # provider -> model name (for badge label)
    api_keys_set: dict[str, bool]      # provider -> "set in DB?"
    local_only: bool                   # if true, every coach forced to local
    chat_retention_days: int | None    # null = keep forever
    preferred_language: str | None     # "en" | "de" | null=auto


class LLMConfigUpdate(BaseModel):
    # task -> provider name. Empty string clears that override.
    coach_providers: dict[str, str] | None = None
    # provider -> plaintext API key. Empty string clears that key.
    api_keys: dict[str, str] | None = None
    local_only: bool | None = None
    chat_retention_days: int | None = Field(default=None, ge=0, le=3650)
    preferred_language: str | None = None  # "en"|"de"|"" (cleared)

    @field_validator("preferred_language")
    @classmethod
    def validate_lang(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        if v not in {"en", "de"}:
            raise ValueError("preferred_language must be 'en', 'de', or empty")
        return v

    @field_validator("coach_providers")
    @classmethod
    def validate_coach_providers(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        cleaned: dict[str, str] = {}
        for task, provider in value.items():
            if task not in COACH_META:
                raise ValueError(f"Unknown coach task: {task}")
            if provider == "":
                cleaned[task] = ""
                continue
            try:
                Provider(provider)
            except ValueError as exc:
                raise ValueError(f"Unknown provider for {task}: {provider}") from exc
            cleaned[task] = provider
        return cleaned

    @field_validator("api_keys")
    @classmethod
    def validate_api_keys(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        cleaned: dict[str, str] = {}
        for provider, key in value.items():
            if provider not in _API_KEY_PROVIDERS:
                raise ValueError(f"Unknown provider: {provider}")
            if not isinstance(key, str):
                raise ValueError("API keys must be strings")
            if len(key) > 4096:
                raise ValueError("API key too long")
            cleaned[provider] = key
        return cleaned


class InnerTeamRole(BaseModel):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    archetype: str = Field(default="warrior", min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    description: str = Field(default="", max_length=200)
    intention: str = Field(default="", max_length=400)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    watch_outs: list[str] = Field(default_factory=list, max_length=8)
    tasks: list[str] = Field(default_factory=list, max_length=8)
    tone: str = Field(default="", max_length=80)
    challenge_level: int = Field(default=3, ge=1, le=5)
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    is_custom: bool = False

    @field_validator("name", "description", "intention", "tone")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("strengths", "watch_outs", "tasks", "focus_areas", "avoid")
    @classmethod
    def clean_short_list(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 120 for item in cleaned):
            raise ValueError("List items must be 120 characters or fewer")
        return cleaned


class InnerTeamSuggestion(BaseModel):
    role_id: str
    name: str
    confidence: int
    reason: str


class InnerTeamRead(BaseModel):
    mode: str
    active_role_id: str
    active_reason: str
    roles: list[InnerTeamRole]
    suggestions: list[InnerTeamSuggestion] = Field(default_factory=list)
    updated_at: str | None = None


class InnerTeamUpdate(BaseModel):
    mode: str | None = None
    active_role_id: str | None = Field(default=None, min_length=1, max_length=50)
    active_reason: str | None = Field(default=None, max_length=400)
    roles: list[InnerTeamRole] | None = Field(default=None, min_length=1, max_length=10)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in {"auto", "manual"}:
            raise ValueError("mode must be 'auto' or 'manual'")
        return value

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: list[InnerTeamRole] | None) -> list[InnerTeamRole] | None:
        if value is None:
            return None
        ids = [role.id for role in value]
        if len(set(ids)) != len(ids):
            raise ValueError("Role ids must be unique")
        return value


_ASCII_REPLACEMENTS = str.maketrans(
    {
        "\u00c4": "Ae",
        "\u00d6": "Oe",
        "\u00dc": "Ue",
        "\u00df": "ss",
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def _ascii_safe_text(value: str) -> str:
    value = value.translate(_ASCII_REPLACEMENTS)
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _ascii_safe_inner_team(value: Any) -> Any:
    """Legacy SQL_ASCII Postgres cannot persist arbitrary Unicode in JSONB."""
    if isinstance(value, str):
        return _ascii_safe_text(value)
    if isinstance(value, list):
        return [_ascii_safe_inner_team(item) for item in value]
    if isinstance(value, dict):
        return {key: _ascii_safe_inner_team(item) for key, item in value.items()}
    return value


@router.get("/coach-prompts/defaults", response_model=list[CoachPromptDefault])
async def get_coach_prompt_defaults(
    _: Annotated[UUID, Depends(get_approved_user_id)],
) -> list[CoachPromptDefault]:
    """Built-in default system prompts per editable coach.

    UI uses these as placeholders / 'Reset to default' targets in the
    coach-prompts settings card.
    """
    defaults = default_prompts()
    return [
        CoachPromptDefault(
            task=key,
            label=meta["label"],
            editable=meta["editable"],
            default_prompt=defaults.get(key, ""),
        )
        for key, meta in COACH_META.items()
    ]


def _llm_snapshot(profile: UserProfile | None) -> LLMConfigRead:
    coach_providers = dict((profile.coach_providers if profile else None) or {})
    env_providers = {
        task: _env_provider_for(TaskClass(task)).value for task in COACH_META
    }
    enc = (profile.api_keys_enc if profile else None) or {}
    from app.agent.router import get_effective_local_model
    return LLMConfigRead(
        coach_providers=coach_providers,
        env_providers=env_providers,
        provider_models={
            "local":     get_effective_local_model(),
            "anthropic": get_settings().anthropic_model,
            "openai":    get_settings().openai_model,
        },
        api_keys_set={p: (p in enc) for p in _API_KEY_PROVIDERS},
        local_only=bool(profile.local_only) if profile else False,
        chat_retention_days=profile.chat_retention_days if profile else None,
        preferred_language=profile.preferred_language if profile else None,
    )


def _inner_team_snapshot(profile: UserProfile | None) -> InnerTeamRead:
    return InnerTeamRead.model_validate(
        normalize_inner_team(profile.inner_team if profile else None)
    )


@router.get("/llm", response_model=LLMConfigRead)
async def get_llm_config(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> LLMConfigRead:
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
    return _llm_snapshot(profile)


@router.put("/llm", response_model=LLMConfigRead)
async def update_llm_config(
    body: LLMConfigUpdate,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> LLMConfigRead:
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        if not profile:
            profile = UserProfile(user_id=user_id)
            session.add(profile)

        if body.coach_providers is not None:
            merged = dict(profile.coach_providers or {})
            for task, provider in body.coach_providers.items():
                if provider == "":
                    merged.pop(task, None)
                else:
                    merged[task] = provider
            profile.coach_providers = merged or None

        if body.api_keys is not None:
            merged = dict(profile.api_keys_enc or {})
            for provider, plaintext in body.api_keys.items():
                if plaintext == "":
                    merged.pop(provider, None)
                else:
                    merged[provider] = encrypt(plaintext)
            profile.api_keys_enc = merged or None

        if body.local_only is not None:
            profile.local_only = body.local_only

        if body.chat_retention_days is not None:
            # 0 = "keep forever" (clears the limit); positive int = N-day window.
            profile.chat_retention_days = body.chat_retention_days or None

        # preferred_language: empty string clears (sets to null = auto).
        if body.preferred_language is not None:
            profile.preferred_language = body.preferred_language or None

        profile.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
        return _llm_snapshot(profile)


@router.get("/inner-team", response_model=InnerTeamRead)
async def get_inner_team(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> InnerTeamRead:
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
    return _inner_team_snapshot(profile)


@router.put("/inner-team", response_model=InnerTeamRead)
async def update_inner_team(
    body: InnerTeamUpdate,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> InnerTeamRead:
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        if not profile:
            profile = UserProfile(user_id=user_id)
            session.add(profile)

        settings = normalize_inner_team(profile.inner_team)
        update = body.model_dump(exclude_unset=True)

        if "mode" in update:
            settings["mode"] = update["mode"]
        if "roles" in update:
            settings["roles"] = update["roles"]
        if "active_role_id" in update:
            role_ids = {role["id"] for role in settings["roles"]}
            if update["active_role_id"] not in role_ids:
                raise HTTPException(
                    status_code=422,
                    detail="active_role_id must match a configured role",
                )
            settings["active_role_id"] = update["active_role_id"]
        if "active_reason" in update:
            settings["active_reason"] = update["active_reason"] or ""

        role_ids = {role["id"] for role in settings["roles"]}
        if settings["active_role_id"] not in role_ids:
            settings["active_role_id"] = settings["roles"][0]["id"]
            settings["active_reason"] = "Active role reset because the previous role was removed."

        settings["updated_at"] = datetime.utcnow().isoformat()
        profile.inner_team = _ascii_safe_inner_team(settings)
        profile.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
        return _inner_team_snapshot(profile)


@router.get("", response_model=ProfileRead)
async def get_profile(user_id: Annotated[UUID, Depends(get_approved_user_id)]):
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
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
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
            if key == "coach_prompts" and value is not None:
                # Merge: only the keys present in the request are updated.
                # Empty string clears that coach's override.
                merged = dict(profile.coach_prompts or {})
                for coach, prompt in value.items():
                    if prompt == "":
                        merged.pop(coach, None)
                    else:
                        merged[coach] = prompt
                setattr(profile, key, merged or None)
            else:
                setattr(profile, key, value)

        profile.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
        return profile
