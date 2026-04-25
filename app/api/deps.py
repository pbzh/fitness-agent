"""Auth dependency.

Single-user mode: any non-empty bearer token resolves to the single user.
Swap to JWT when the iOS app needs OAuth. The interface stays the same —
only this file changes.
"""

from uuid import UUID

from fastapi import Header, HTTPException

SINGLE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return SINGLE_USER_ID
