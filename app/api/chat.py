"""Chat endpoint — the main interface the iOS app and other clients hit.

Messages are persisted to AgentMessage so the WebUI/mobile clients can
restore the conversation on login. We use a rolling-thread model: every user
has a single deterministic conversation_id derived from their user id, so all
chat history accumulates in one thread regardless of client state.
"""

import asyncio
from typing import Annotated
from uuid import UUID, uuid5, NAMESPACE_DNS

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.agent.agent import AgentDeps, build_agent
from app.agent.attachments import build_part, has_image
from app.agent.router import Provider, TaskClass, _resolve_provider
from app.api.deps import get_current_user_id
from app.config import get_settings
from app.db.models import AgentMessage, File as DBFile
from app.db.session import AsyncSessionLocal
from app.files import storage

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger()


def rolling_conversation_id(user_id: UUID) -> UUID:
    """Deterministic per-user conversation id for the rolling thread."""
    return uuid5(NAMESPACE_DNS, f"fitness-agent.rolling.{user_id}")


class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID | None = None  # ignored — kept for client compat
    task_hint: TaskClass = TaskClass.CHAT
    attached_file_ids: list[UUID] = Field(default_factory=list)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    reply: str
    conversation_id: UUID
    model_used: str
    token_usage: TokenUsage | None = None


class AttachmentRef(BaseModel):
    id: UUID
    filename: str
    mime_type: str


class HistoryMessage(BaseModel):
    id: UUID
    role: str
    content: str
    task: str | None
    model_used: str | None
    created_at: str
    input_tokens: int | None
    output_tokens: int | None
    attachments: list[AttachmentRef] = Field(default_factory=list)


_RETRYABLE_STATUSES = {429, 502, 503, 504, 529}
_MAX_RETRIES = 3
_BASE_BACKOFF_S = 2.0


def _is_retryable(exc: Exception) -> bool:
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
    convo_id = rolling_conversation_id(user_id)

    # ── Resolve attachments (validate ownership, build input parts) ──
    parts: list = []
    attachment_ids: list[str] = []
    if req.attached_file_ids:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(DBFile)
                    .where(DBFile.id.in_(req.attached_file_ids))
                    .where(DBFile.user_id == user_id)
                )
            ).scalars().all()
        for f in rows:
            full = storage.absolute_path(f.storage_path)
            if not full.exists():
                continue
            parts.append(build_part(f, full))
            attachment_ids.append(str(f.id))

    # Persist the user turn before invoking the agent so it survives even if
    # the LLM call fails.
    async with AsyncSessionLocal() as session:
        session.add(
            AgentMessage(
                user_id=user_id,
                conversation_id=convo_id,
                role="user",
                content=req.message,
                task=req.task_hint.value,
                attached_file_ids=attachment_ids,
            )
        )
        await session.commit()

    # If the user attached an image and the task would route to the local
    # llama.cpp endpoint (which is not multimodal), bump this single run to
    # Anthropic so the image is actually visible to the model.
    override_provider: Provider | None = None
    if has_image(parts):
        resolved = _resolve_provider(req.task_hint)
        if resolved == Provider.LOCAL and get_settings().anthropic_api_key:
            override_provider = Provider.ANTHROPIC
            log.info("Routing image-bearing turn to anthropic", task=req.task_hint.value)

    agent = build_agent(task=req.task_hint, override_provider=override_provider)
    deps = AgentDeps(session_factory=AsyncSessionLocal, user_id=user_id)

    # PydanticAI accepts a list of mixed strings + BinaryContent for multimodal.
    agent_input: str | list = req.message if not parts else [req.message, *parts]

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            result = await agent.run(agent_input, deps=deps)
            usage = result.usage()
            model_used = str(agent.model)

            async with AsyncSessionLocal() as session:
                session.add(
                    AgentMessage(
                        user_id=user_id,
                        conversation_id=convo_id,
                        role="assistant",
                        content=result.output,
                        task=req.task_hint.value,
                        model_used=model_used,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                    )
                )
                await session.commit()

            return ChatResponse(
                reply=result.output,
                conversation_id=convo_id,
                model_used=model_used,
                token_usage=TokenUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.input_tokens + usage.output_tokens,
                ),
            )
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == _MAX_RETRIES - 1:
                break
            backoff = _BASE_BACKOFF_S * (2**attempt)
            log.warning(
                "Agent call failed, retrying",
                attempt=attempt + 1,
                backoff_s=backoff,
                error=str(e),
            )
            await asyncio.sleep(backoff)

    raise HTTPException(status_code=502, detail=f"Agent error: {last_exc}")


@router.get("/history", response_model=list[HistoryMessage])
async def chat_history(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    limit: int = 200,
) -> list[HistoryMessage]:
    """Return the user's rolling conversation, oldest first."""
    convo_id = rolling_conversation_id(user_id)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AgentMessage)
                .where(AgentMessage.user_id == user_id)
                .where(AgentMessage.conversation_id == convo_id)
                .order_by(AgentMessage.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    rows.reverse()  # oldest → newest

    # Bulk-load every distinct attached file in one query.
    file_ids = {fid for m in rows for fid in (m.attached_file_ids or [])}
    file_meta: dict[str, AttachmentRef] = {}
    if file_ids:
        async with AsyncSessionLocal() as session:
            files = (
                await session.execute(
                    select(DBFile)
                    .where(DBFile.id.in_([UUID(x) for x in file_ids]))
                    .where(DBFile.user_id == user_id)
                )
            ).scalars().all()
        file_meta = {
            str(f.id): AttachmentRef(id=f.id, filename=f.filename, mime_type=f.mime_type)
            for f in files
        }

    return [
        HistoryMessage(
            id=m.id,
            role=m.role,
            content=m.content,
            task=m.task,
            model_used=m.model_used,
            created_at=m.created_at.isoformat(),
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            attachments=[
                file_meta[fid] for fid in (m.attached_file_ids or []) if fid in file_meta
            ],
        )
        for m in rows
    ]


@router.delete("/history", status_code=204)
async def clear_history(user_id: Annotated[UUID, Depends(get_current_user_id)]) -> None:
    """Wipe the rolling conversation for this user."""
    convo_id = rolling_conversation_id(user_id)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AgentMessage)
                .where(AgentMessage.user_id == user_id)
                .where(AgentMessage.conversation_id == convo_id)
            )
        ).scalars().all()
        for r in rows:
            await session.delete(r)
        await session.commit()
