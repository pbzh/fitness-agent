"""Authentication endpoints."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.agent.prompts import COACH_META, default_prompts
from app.api.deps import get_approved_user_id, get_current_user
from app.config import get_settings
from app.db.models import AdminAuditLog, LoginAttempt, User, UserProfile
from app.db.session import get_session
from app.security.rate_limit import check_auth_rate_limit, clear_auth_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])
_background_tasks: set = set()


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    pending_approval: bool = False
    is_admin: bool = False


class UserRead(BaseModel):
    id: UUID
    email: str
    is_admin: bool = False
    is_approved: bool = False


def _password_bytes(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be 72 bytes or fewer")
    return encoded


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(password), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


async def _ensure_default_profile(session: AsyncSession, user_id: UUID) -> None:
    """Create a UserProfile with safe defaults if one doesn't exist yet."""
    existing = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    if existing:
        return
    profile = UserProfile(
        user_id=user_id,
        coach_providers={task: "local" for task in COACH_META},
        coach_prompts=default_prompts(),
    )
    session.add(profile)
    await session.commit()


async def _notify_admins_registration(new_user_email: str) -> None:
    from app.core.email import send_registration_notification
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        admins = (
            await session.execute(select(User).where(User.is_admin.is_(True)))
        ).scalars().all()
    for admin in admins:
        await send_registration_notification(admin.email, new_user_email)


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    req: RegisterRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegisterResponse:
    await check_auth_rate_limit(request, req.email)
    result = await session.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )
    has_users = (await session.execute(select(User.id).limit(1))).first() is not None
    is_first_user = not has_users
    user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        is_admin=is_first_user,
        is_approved=is_first_user,
        approved_at=datetime.utcnow() if is_first_user else None,
    )
    session.add(user)
    await session.flush()  # write user row first so FK on audit log resolves
    session.add(
        AdminAuditLog(
            actor_user_id=user.id,
            target_user_id=user.id,
            action="registration",
            before=None,
            after={"email": user.email, "is_admin": user.is_admin, "is_approved": user.is_approved},
        )
    )
    await session.commit()
    await session.refresh(user)
    if not user.is_approved:
        from app.core.email import send_registration_pending_email
        for coro in (
            _notify_admins_registration(req.email),
            send_registration_pending_email(req.email),
        ):
            t = asyncio.create_task(coro)
            _background_tasks.add(t)
            t.add_done_callback(_background_tasks.discard)
        return RegisterResponse(pending_approval=True)
    await _ensure_default_profile(session, user.id)
    await clear_auth_rate_limit(request, req.email)
    return RegisterResponse(
        access_token=create_access_token(user.id),
        pending_approval=False,
        is_admin=user.is_admin,
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


@router.post("/change-password", status_code=200)
async def change_password(
    req: ChangePasswordRequest,
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    if not verify_password(req.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    user.hashed_password = hash_password(req.new_password)
    session.add(user)
    await session.commit()
    return {"ok": True}


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    await check_auth_rate_limit(request, credentials.email)
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)
    ua = request.headers.get("User-Agent")

    result = await session.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.hashed_password):
        session.add(LoginAttempt(
            email=credentials.email,
            user_id=user.id if user else None,
            success=False,
            ip_address=ip,
            user_agent=ua,
            failure_reason="invalid_credentials",
        ))
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_approved:
        session.add(LoginAttempt(
            email=credentials.email,
            user_id=user.id,
            success=False,
            ip_address=ip,
            user_agent=ua,
            failure_reason="pending_approval",
        ))
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is pending approval",
        )

    session.add(LoginAttempt(
        email=credentials.email,
        user_id=user.id,
        success=True,
        ip_address=ip,
        user_agent=ua,
    ))
    await _ensure_default_profile(session, user.id)
    await clear_auth_rate_limit(request, credentials.email)
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserRead)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        is_approved=user.is_approved,
    )
