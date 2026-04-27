from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.auth import create_access_token, hash_password, verify_password
from app.api.profile import InnerTeamRole, InnerTeamUpdate, ProfileUpdate
from app.inner_team import default_inner_team, detect_inner_team_role, normalize_inner_team


def test_password_hash_and_verify() -> None:
    hashed = hash_password("correct horse")

    assert verify_password("correct horse", hashed)
    assert not verify_password("wrong", hashed)


def test_create_access_token() -> None:
    token = create_access_token(UUID("00000000-0000-0000-0000-000000000001"))

    assert token


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
        strengths=[" Focus ", ""],
        tasks=[" Choose next step "],
    )

    assert role.name == "Custom"
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
