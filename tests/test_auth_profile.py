import inspect
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app.api.auth import create_access_token, hash_password, verify_password
from app.api.deps import get_current_user_id
from app.api.profile import (
    InnerTeamRole,
    InnerTeamUpdate,
    ProfileUpdate,
    _ascii_safe_inner_team,
)
from app.inner_team import default_inner_team, detect_inner_team_role, normalize_inner_team
from app.security import rate_limit


def test_password_hash_and_verify() -> None:
    hashed = hash_password("correct horse")

    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong", hashed)


def test_create_access_token() -> None:
    token = create_access_token(UUID("00000000-0000-0000-0000-000000000001"))

    assert token


def test_auth_dependency_does_not_accept_query_string_token() -> None:
    params = inspect.signature(get_current_user_id).parameters

    assert "access_token" not in params


@pytest.mark.asyncio
async def test_memory_auth_rate_limit_blocks_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_limit._memory_auth_counts.clear()
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(
            auth_rate_limit_backend="memory",
            auth_rate_limit_max_attempts=2,
            auth_rate_limit_window_seconds=60,
            trusted_proxy_cidrs="",
        ),
    )
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 12345)})

    await rate_limit.check_auth_rate_limit(request, "user@example.com")
    await rate_limit.check_auth_rate_limit(request, "user@example.com")
    with pytest.raises(HTTPException) as exc:
        await rate_limit.check_auth_rate_limit(request, "user@example.com")

    assert exc.value.status_code == 429


def test_profile_update_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(height_cm=-1, weekly_workout_target=99)


def test_profile_update_trims_lists() -> None:
    profile = ProfileUpdate(equipment=[" dumbbell ", "", "TRX"])

    assert profile.equipment == ["dumbbell", "TRX"]


def test_inner_team_update_rejects_duplicate_role_ids() -> None:
    role = InnerTeamRole(id="custom", name="Custom")

    with pytest.raises(ValidationError):
        InnerTeamUpdate(roles=[role, role])


def test_inner_team_role_lists_are_trimmed() -> None:
    role = InnerTeamRole(
        id="custom",
        name="  Custom  ",
        archetype="architect",
        strengths=[" Focus ", ""],
        tasks=[" Choose next step "],
    )

    assert role.name == "Custom"
    assert role.archetype == "architect"
    assert role.strengths == ["Focus"]
    assert role.tasks == ["Choose next step"]


def test_inner_team_detector_updates_auto_role_for_recovery_language() -> None:
    settings = default_inner_team()
    updated = detect_inner_team_role(
        "I'm wrecked and slept badly but feel pressure to train hard.",
        settings,
    )

    assert updated["active_role_id"] == "recovery_guardian"
    assert updated["suggestions"][0]["role_id"] == "recovery_guardian"


def test_inner_team_detector_does_not_switch_manual_mode() -> None:
    settings = normalize_inner_team({"mode": "manual", "active_role_id": "athlete"})
    updated = detect_inner_team_role("I feel guilty after overeating.", settings)

    assert updated["active_role_id"] == "athlete"
    assert updated["suggestions"][0]["role_id"] == "compassionate_friend"


def test_inner_team_normalize_preserves_removed_default_roles() -> None:
    settings = default_inner_team()
    settings["roles"] = [
        role for role in settings["roles"] if role["id"] != "recovery_guardian"
    ]

    normalized = normalize_inner_team(settings)

    assert "recovery_guardian" not in {role["id"] for role in normalized["roles"]}


def test_inner_team_ascii_sanitizer_rewrites_unicode_text() -> None:
    payload = {
        "active_reason": "Selected manually…",
        "roles": [
            {
                "id": "custom",
                "name": "Rôle 🚀",
                "description": "Coach's größte Stärke",
                "tasks": ["Nächster Schritt für Öl und Übung"],
            }
        ],
    }

    cleaned = _ascii_safe_inner_team(payload)

    assert cleaned["active_reason"] == "Selected manually..."
    assert cleaned["roles"][0]["name"] == "Role "
    assert cleaned["roles"][0]["description"] == "Coach's groesste Staerke"
    assert cleaned["roles"][0]["tasks"] == ["Naechster Schritt fuer Oel und Uebung"]
