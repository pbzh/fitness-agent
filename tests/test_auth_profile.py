from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.auth import create_access_token, hash_password, verify_password
from app.api.profile import ProfileUpdate


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
