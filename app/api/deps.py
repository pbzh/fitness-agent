"""Authentication dependencies."""

from uuid import UUID

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings


async def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    settings = get_settings()
    token = authorization.removeprefix("Bearer ").strip()
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
