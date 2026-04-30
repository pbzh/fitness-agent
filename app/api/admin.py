"""Admin endpoints for user approval and role management."""

import asyncio
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
from app.db.models import AdminAuditLog, LoginAttempt, SystemConfig, User
from app.db.models import File as DBFile
from app.db.session import AsyncSessionLocal
from app.files import storage
from app.security.secrets import encrypt

router = APIRouter(prefix="/admin", tags=["admin"])
log = structlog.get_logger()
_background_tasks: set = set()


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
    target_user_id: UUID | None = None
    target_email: str | None = None
    action: str
    before: dict | None
    after: dict | None
    created_at: datetime


class SmtpConfig(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""         # plaintext in/out — encrypted at rest
    from_address: str = ""
    use_tls: bool = True
    use_ssl: bool = False


class SmtpConfigRead(BaseModel):
    host: str
    port: int
    username: str
    from_address: str
    use_tls: bool
    use_ssl: bool
    has_password: bool


class LoginAttemptRead(BaseModel):
    id: UUID
    email: str
    user_id: UUID | None
    success: bool
    ip_address: str | None
    user_agent: str | None
    failure_reason: str | None
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

        # send email notifications after commit (fire-and-forget)
        if "approve_user" in actions:
            from app.core.email import send_approval_email
            t = asyncio.create_task(send_approval_email(user.email))
            _background_tasks.add(t)
            t.add_done_callback(_background_tasks.discard)
        elif "revoke_approval" in actions:
            from app.core.email import send_rejection_email
            t = asyncio.create_task(send_rejection_email(user.email))
            _background_tasks.add(t)
            t.add_done_callback(_background_tasks.discard)

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
                target_user_id=None,
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
        user_ids = {r.actor_user_id for r in rows}
        user_ids |= {r.target_user_id for r in rows if r.target_user_id is not None}
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
            target_email=(
                email_by_id.get(row.target_user_id)
                if row.target_user_id is not None
                else None
            ),
            action=row.action,
            before=row.before,
            after=row.after,
            created_at=row.created_at,
        )
        for row in rows
    ]


# ── SMTP configuration ───────────────────────────────────────────────────────

_SMTP_KEY = "smtp"


def _smtp_row_to_read(cfg: dict) -> SmtpConfigRead:
    return SmtpConfigRead(
        host=cfg.get("host", ""),
        port=int(cfg.get("port", 587)),
        username=cfg.get("username", ""),
        from_address=cfg.get("from_address", ""),
        use_tls=bool(cfg.get("use_tls", True)),
        use_ssl=bool(cfg.get("use_ssl", False)),
        has_password=bool(cfg.get("password_enc")),
    )


@router.get("/smtp", response_model=SmtpConfigRead)
async def get_smtp_config(
    _: Annotated[User, Depends(get_current_admin_user)],
) -> SmtpConfigRead:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == _SMTP_KEY)
            )
        ).scalar_one_or_none()
    if not row:
        return SmtpConfigRead(
            host="", port=587, username="", from_address="",
            use_tls=True, use_ssl=False, has_password=False,
        )
    return _smtp_row_to_read(row.value)


@router.put("/smtp", response_model=SmtpConfigRead)
async def put_smtp_config(
    body: SmtpConfig,
    _: Annotated[User, Depends(get_current_admin_user)],
) -> SmtpConfigRead:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == _SMTP_KEY)
            )
        ).scalar_one_or_none()

        stored: dict = row.value.copy() if row else {}
        stored["host"] = body.host
        stored["port"] = body.port
        stored["username"] = body.username
        stored["from_address"] = body.from_address
        stored["use_tls"] = body.use_tls
        stored["use_ssl"] = body.use_ssl
        if body.password:
            stored["password_enc"] = encrypt(body.password)

        if row:
            row.value = stored
            row.updated_at = datetime.utcnow()
            session.add(row)
        else:
            session.add(SystemConfig(key=_SMTP_KEY, value=stored))
        await session.commit()
    return _smtp_row_to_read(stored)


@router.post("/smtp/test", status_code=200)
async def test_smtp_config(
    _: Annotated[User, Depends(get_current_admin_user)],
    to: str = "",
) -> dict[str, bool | str]:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(SystemConfig).where(SystemConfig.key == _SMTP_KEY)
            )
        ).scalar_one_or_none()
    if not row or not row.value.get("host"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SMTP not configured")

    cfg = row.value
    dest = to or cfg.get("from_address", "")
    if not dest:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No recipient address")

    from app.core.email import send_email

    ok = await send_email(
        dest,
        "Coacher SMTP test",
        "<p>SMTP configuration test — delivery confirmed.</p>",
        "SMTP configuration test — delivery confirmed.",
    )
    return {"ok": ok, "to": dest}


# ── Login attempts ───────────────────────────────────────────────────────────

@router.get("/login-attempts", response_model=list[LoginAttemptRead])
async def list_login_attempts(
    _: Annotated[User, Depends(get_current_admin_user)],
    limit: int = 100,
    success: bool | None = None,
) -> list[LoginAttemptRead]:
    bounded_limit = max(1, min(limit, 500))
    async with AsyncSessionLocal() as session:
        q = select(LoginAttempt).order_by(LoginAttempt.created_at.desc()).limit(bounded_limit)
        if success is not None:
            q = q.where(LoginAttempt.success == success)
        rows = (await session.execute(q)).scalars().all()
    return [
        LoginAttemptRead(
            id=row.id,
            email=row.email,
            user_id=row.user_id,
            success=row.success,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
            failure_reason=row.failure_reason,
            created_at=row.created_at,
        )
        for row in rows
    ]
