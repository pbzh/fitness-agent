"""One-time script to bootstrap the single user and their profile.

Run with: uv run python scripts/bootstrap_user.py

Edit the values below before running.
"""

import asyncio
from uuid import UUID

from app.db.models import User, UserProfile
from app.db.session import AsyncSessionLocal

USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def bootstrap() -> None:
    async with AsyncSessionLocal() as session:
        user = User(
            id=USER_ID,
            email="patrick@example.com",  # adjust
            hashed_password="not-used-yet",
        )
        session.add(user)

        profile = UserProfile(
            user_id=USER_ID,
            height_cm=180,            # adjust
            weight_kg=78,             # adjust
            primary_goal="strength",  # or "hypertrophy", "endurance", "fat_loss"
            weekly_workout_target=4,
            equipment=[
                "MountainGrip hangboard",
                "TRX Suspension Trainer",
                "pull-up bar",
            ],
            dietary_restrictions=[],
            daily_calorie_target=2600,
            macro_targets={"protein_g": 160, "carbs_g": 280, "fat_g": 80},
            notes=(
                "Trains 4x/week. Rest days typically Wednesday and Saturday. "
                "Prefers compact sessions of 45-60 min. Metric units."
            ),
        )
        session.add(profile)
        await session.commit()
        print(f"Created user {USER_ID} with profile")


if __name__ == "__main__":
    asyncio.run(bootstrap())
