"""Admin endpoints for user approval and role management."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlmodel import select

from app.api.auth import hash_password
from app.api.deps import get_current_admin_user
from app.db.models import AdminAuditLog, User
from app.db.models import File as DBFile
from app.db.session import AsyncSessionLocal
from app.files import storage

router = APIRouter(prefix="/admin", tags=["admin"])
log = structlog.get_logger()


class AdminUserRead(BaseModel):
    id: UUID
    email: str
    created_at: datetime
    is_admin: bool
    is_approved: bool
    approved_at: datetime | None


class AdminUserUpdate(BaseModel):
    is_admin: bool | None = None
    is_approved: bool | None = None


class AdminPasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class AdminAuditRead(BaseModel):
    id: UUID
    actor_user_id: UUID
    actor_email: str | None = None
    target_user_id: UUID
    target_email: str | None = None
    action: str
    before: dict | None
    after: dict | None
    created_at: datetime


def _to_read(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
        approved_at=user.approved_at,
    )


def _audit_state(user: User) -> dict:
    return {
        "email": user.email,
        "is_admin": user.is_admin,
        "is_approved": user.is_approved,
        "approved_at": user.approved_at.isoformat() if user.approved_at else None,
    }


async def _delete_user_owned_data(session, user_id: UUID) -> list[str]:
    files = (
        await session.execute(select(DBFile).where(DBFile.user_id == user_id))
    ).scalars().all()
    file_paths = [f.storage_path for f in files]

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
        text(
            "DELETE FROM adminauditlog "
            "WHERE actor_user_id=:u OR target_user_id=:u"
        ),
        {"u": str(user_id)},
    )
    await session.execute(text('DELETE FROM "user" WHERE id=:u'), {"u": str(user_id)})
    return file_paths


@router.get("/users", response_model=list[AdminUserRead])
async def list_users(_: Annotated[User, Depends(get_current_admin_user)]) -> list[AdminUserRead]:
    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(select(User).order_by(User.created_at.desc()))
        ).scalars().all()
    return [_to_read(user) for user in users]


@router.patch("/users/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> AdminUserRead:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        before = _audit_state(user)
        actions: list[str] = []

        if body.is_admin is not None:
            if user.id == admin.id and not body.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You cannot remove your own admin role",
                )
            if user.is_admin != body.is_admin:
                actions.append("grant_admin" if body.is_admin else "revoke_admin")
            user.is_admin = body.is_admin

        if body.is_approved is not None:
            if user.id == admin.id and not body.is_approved:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You cannot revoke your own approval",
                )
            if user.is_approved != body.is_approved:
                actions.append("approve_user" if body.is_approved else "revoke_approval")
            user.is_approved = body.is_approved
            user.approved_at = datetime.utcnow() if body.is_approved else None

        after = _audit_state(user)
        for action in actions:
            session.add(
                AdminAuditLog(
                    actor_user_id=admin.id,
                    target_user_id=user.id,
                    action=action,
                    before=before,
                    after=after,
                )
            )

        session.add(user)
        await session.commit()
        await session.refresh(user)
        return _to_read(user)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: UUID,
    body: AdminPasswordResetRequest,
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        before = _audit_state(user)
        user.hashed_password = hash_password(body.new_password)
        session.add(user)
        session.add(
            AdminAuditLog(
                actor_user_id=admin.id,
                target_user_id=user.id,
                action="reset_password",
                before=before,
                after={**_audit_state(user), "password_reset_at": datetime.utcnow().isoformat()},
            )
        )
        await session.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    admin: Annotated[User, Depends(get_current_admin_user)],
) -> None:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account from the admin page",
        )

    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if user.is_admin:
            admin_count = (
                await session.execute(
                    select(func.count()).select_from(User).where(User.is_admin.is_(True))
                )
            ).scalar_one()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You cannot delete the last admin user",
                )

        before = _audit_state(user)
        file_paths = await _delete_user_owned_data(session, user.id)
        session.add(
            AdminAuditLog(
                actor_user_id=admin.id,
                target_user_id=admin.id,
                action="delete_user",
                before=before,
                after={"deleted_user_id": str(user.id), "deleted_email": user.email},
            )
        )
        await session.commit()

    for path in file_paths:
        try:
            storage.delete(path)
        except Exception as exc:
            log.warning("Could not delete admin-removed user's file", path=path, error=str(exc))


@router.get("/audit", response_model=list[AdminAuditRead])
async def list_audit_logs(
    _: Annotated[User, Depends(get_current_admin_user)],
    limit: int = 100,
) -> list[AdminAuditRead]:
    bounded_limit = max(1, min(limit, 500))
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(bounded_limit)
            )
        ).scalars().all()
        user_ids = {r.actor_user_id for r in rows} | {r.target_user_id for r in rows}
        users = (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all()
    email_by_id = {u.id: u.email for u in users}
    return [
        AdminAuditRead(
            id=row.id,
            actor_user_id=row.actor_user_id,
            actor_email=email_by_id.get(row.actor_user_id),
            target_user_id=row.target_user_id,
            target_email=email_by_id.get(row.target_user_id),
            action=row.action,
            before=row.before,
            after=row.after,
            created_at=row.created_at,
        )
        for row in rows
    ]
