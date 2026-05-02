"""Authentication dependencies."""

from uuid import UUID

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt
from sqlmodel import select

from app.config import get_settings
from app.db.models import User
from app.db.session import AsyncSessionLocal


async def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> UUID:
    token: str | None = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        subject = payload.get("sub")
        if not isinstance(subject, str):
            raise JWTError("Missing subject")
        return UUID(subject)
    except (JWTError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token",
        ) from exc


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> User:
    resolved_user_id = await get_current_user_id(
        authorization=authorization,
    )
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == resolved_user_id))
        ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is pending approval",
        )
    return user


async def get_approved_user_id(
    authorization: str | None = Header(default=None),
) -> UUID:
    user = await get_current_user(
        authorization=authorization,
    )
    return user.id


async def get_current_admin_user(
    authorization: str | None = Header(default=None),
) -> User:
    user = await get_current_user(
        authorization=authorization,
    )
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
