"""GDPR data-subject endpoints: export (Art. 15 + 20) and delete (Art. 17)."""

from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import select

from app.api.auth import verify_password
from app.api.deps import get_approved_user_id
from app.db.models import (
    AgentMessage,
    HealthMetric,
    MealLog,
    MealPlan,
    PlannedMeal,
    User,
    UserProfile,
    WorkoutPlan,
    WorkoutSession,
)
from app.db.models import (
    File as DBFile,
)
from app.db.session import AsyncSessionLocal
from app.files import storage

router = APIRouter(prefix="/profile", tags=["gdpr"])
log = structlog.get_logger()


def _json_default(obj):
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Not JSON-serializable: {type(obj).__name__}")


def _row_dict(row) -> dict:
    """Best-effort dump of a SQLModel row, omitting encrypted fields."""
    out = {}
    for col in row.__table__.columns:
        if col.name == "api_keys_enc":
            continue  # never export ciphertext
        if col.name == "hashed_password":
            continue  # never export password hash
        out[col.name] = getattr(row, col.name)
    return out


# ────────────────────────────────────────────────────────────────────────
# Export — Art. 15 (access) + Art. 20 (portability)
# ────────────────────────────────────────────────────────────────────────


@router.get("/export.zip")
async def export_my_data(
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> StreamingResponse:
    """Bundle every piece of personal data into a single ZIP.

    Contains ``data.json`` (all rows the user owns, minus ciphertext + password
    hashes) and ``files/<id>/<filename>`` for every uploaded or generated file.
    """
    payload: dict = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "format_version": 1,
    }

    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        payload["user"] = {
            "id": str(user.id),
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        }

        async def all_for(model):
            rows = (
                await session.execute(
                    select(model).where(model.user_id == user_id)
                )
            ).scalars().all()
            return [_row_dict(r) for r in rows]

        payload["profile"]         = await all_for(UserProfile)
        payload["chat_history"]    = await all_for(AgentMessage)
        payload["workout_plans"]   = await all_for(WorkoutPlan)
        payload["workout_sessions"]= await all_for(WorkoutSession)
        payload["meal_plans"]      = await all_for(MealPlan)
        payload["planned_meals"]   = await all_for(PlannedMeal)
        payload["meal_logs"]       = await all_for(MealLog)
        payload["health_metrics"]  = await all_for(HealthMetric)
        payload["files"]           = await all_for(DBFile)

        files = (
            await session.execute(select(DBFile).where(DBFile.user_id == user_id))
        ).scalars().all()

        # Snapshot api-key status (never ciphertext or plaintext)
        profile = (
            await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile and profile.api_keys_enc:
            payload["api_keys_status"] = {
                provider: "set (encrypted, not exported)"
                for provider in profile.api_keys_enc
            }
        else:
            payload["api_keys_status"] = {}

    tmp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")  # noqa: SIM115
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "data.json",
            json.dumps(payload, default=_json_default, indent=2),
        )
        zf.writestr(
            "README.txt",
            (
                "coacher — GDPR data export\n"
                f"Exported: {payload['exported_at']}\n"
                f"User: {payload['user']['email']} ({payload['user']['id']})\n\n"
                "data.json contains every row you own across the database.\n"
                "files/<file_id>/<filename> contains each uploaded or "
                "generated file's bytes.\n\n"
                "API keys are stored encrypted in the database and are NOT "
                "included in this export. Only their 'set/unset' status is\n"
                "reported in data.json under api_keys_status. Hashed passwords "
                "are omitted entirely.\n"
            ),
        )
        for f in files:
            full = storage.absolute_path(f.storage_path)
            if not full.exists():
                continue
            try:
                zf.write(full, arcname=f"files/{f.id}/{f.filename}")
            except Exception as exc:
                log.warning("Skipping file in export", file_id=str(f.id), error=str(exc))

    tmp.seek(0)
    fname = f"coacher-export-{date.today().isoformat()}.zip"
    return StreamingResponse(
        tmp,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ────────────────────────────────────────────────────────────────────────
# Delete account — Art. 17 (erasure)
# ────────────────────────────────────────────────────────────────────────


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirm: str  # must equal the user's email — typed-confirm guard


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    body: DeleteAccountRequest,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
) -> None:
    """Hard-delete every row the user owns plus their on-disk files.

    Requires the user's current password AND a typed-confirm of their email.
    Irreversible — there is no soft-delete and no recovery.
    """
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if body.confirm != user.email:
            raise HTTPException(
                status_code=400,
                detail="confirm must match your email exactly",
            )
        if not verify_password(body.password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Password is incorrect")

        # Snapshot file paths so we can scrub disk after the DB rows are gone.
        files = (
            await session.execute(select(DBFile).where(DBFile.user_id == user_id))
        ).scalars().all()
        file_paths = [f.storage_path for f in files]

        # FK ordering: image_file_id columns (workoutsession, plannedmeal,
        # meallog, workoutplan, mealplan) reference file. Null those before
        # dropping rows from `file`. Then plannedmeal/workoutsession before
        # mealplan/workoutplan, and so on.
        await session.execute(
            text("UPDATE workoutsession SET image_file_id=NULL WHERE user_id=:u"),
            {"u": str(user_id)},
        )
        await session.execute(
            text("UPDATE plannedmeal SET image_file_id=NULL WHERE user_id=:u"),
            {"u": str(user_id)},
        )
        await session.execute(
            text("UPDATE meallog SET image_file_id=NULL WHERE user_id=:u"),
            {"u": str(user_id)},
        )
        await session.execute(
            text("UPDATE workoutplan SET image_file_id=NULL WHERE user_id=:u"),
            {"u": str(user_id)},
        )
        await session.execute(
            text("UPDATE mealplan SET image_file_id=NULL WHERE user_id=:u"),
            {"u": str(user_id)},
        )

        for table in (
            "agentmessage",
            "file",
            "workoutsession",
            "plannedmeal",
            "meallog",
            "healthmetric",
            "workoutplan",
            "mealplan",
            "userprofile",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE user_id=:u"),
                {"u": str(user_id)},
            )
        await session.execute(
            text("DELETE FROM \"user\" WHERE id=:u"), {"u": str(user_id)}
        )
        await session.commit()

    # Now scrub disk. Errors here are logged, not raised — the DB delete
    # already succeeded and the user shouldn't see a 500 over a stale file.
    for path in file_paths:
        try:
            storage.delete(path)
        except Exception as exc:
            log.warning("Could not delete file from disk", path=path, error=str(exc))

    log.info("Account deleted", user_id=str(user_id))
