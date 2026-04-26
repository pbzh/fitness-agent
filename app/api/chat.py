"""Chat endpoint — the main interface the iOS app and other clients hit."""

import asyncio
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agent.agent import AgentDeps, build_agent
from app.agent.router import TaskClass
from app.api.deps import get_current_user_id
from app.db.session import AsyncSessionLocal

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger()


class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID | None = None
    task_hint: TaskClass = TaskClass.CHAT


class ChatResponse(BaseModel):
    reply: str
    conversation_id: UUID
    model_used: str


# Retry on transient cloud-provider errors:
# - 529: Anthropic overloaded
# - 503: generic upstream unavailable
# - 502/504: gateway / timeout
# - 429: rate-limited
_RETRYABLE_STATUSES = {429, 502, 503, 504, 529}
_MAX_RETRIES = 3
_BASE_BACKOFF_S = 2.0


def _is_retryable(exc: Exception) -> bool:
    """Best-effort detection of retryable upstream errors across SDK shapes."""
    status = getattr(exc, "status_code", None)
    if status in _RETRYABLE_STATUSES:
        return True
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in ("overloaded", "503", "502", "504", "529", "rate limit")
    )


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
) -> ChatResponse:
    agent = build_agent(task=req.task_hint)
    deps = AgentDeps(session_factory=AsyncSessionLocal, user_id=user_id)

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            result = await agent.run(req.message, deps=deps)
            return ChatResponse(
                reply=result.output,
                conversation_id=req.conversation_id or uuid4(),
                model_used=str(agent.model),
            )
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == _MAX_RETRIES - 1:
                break
            backoff = _BASE_BACKOFF_S * (2**attempt)  # 2s, 4s, 8s
            log.warning(
                "Agent call failed, retrying",
                attempt=attempt + 1,
                backoff_s=backoff,
                error=str(e),
            )
            await asyncio.sleep(backoff)

    raise HTTPException(status_code=502, detail=f"Agent error: {last_exc}")
