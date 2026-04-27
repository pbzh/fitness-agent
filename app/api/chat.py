"""Chat endpoint — the main interface the iOS app and other clients hit.

Messages are persisted to AgentMessage so the WebUI/mobile clients can
restore the conversation on login. We use a rolling-thread model: every user
has a single deterministic conversation_id derived from their user id, so all
chat history accumulates in one thread regardless of client state.
"""

import asyncio
from typing import Annotated
from uuid import NAMESPACE_DNS, UUID, uuid5

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlmodel import select

from app.agent.agent import AgentDeps
from app.agent.attachments import build_part, has_image
from app.agent.effective_config import load_effective_config, resolve_api_key
from app.agent.manager import classify_turn
from app.agent.prompts import resolve_prompt
from app.agent.router import Provider, TaskClass, _env_provider_for, build_model
from app.agent.tools import register_tools
from app.api.deps import get_approved_user_id
from app.config import get_settings
from app.db.models import AgentMessage, UserProfile
from app.db.models import File as DBFile
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
    # Default is now AUTO: the manager picks the right sub-coach. Clients can
    # still force a specific coach by setting this explicitly.
    task_hint: TaskClass = TaskClass.AUTO
    attached_file_ids: list[UUID] = Field(default_factory=list)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    reply: str
    conversation_id: UUID
    model_used: str
    # The coach that actually answered (the manager's resolved task when
    # task_hint=auto, otherwise just the requested task).
    resolved_task: str
    routed_by_manager: bool = False
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
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
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
        found_ids = {f.id for f in rows}
        missing_ids = [fid for fid in req.attached_file_ids if fid not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail="One or more attachments were not found")
        for f in rows:
            full = storage.absolute_path(f.storage_path)
            if not full.exists():
                raise HTTPException(status_code=404, detail=f"Attachment file is missing: {f.id}")
            parts.append(build_part(f, full))
            attachment_ids.append(str(f.id))

    # ── Manager routing: when task_hint=auto, classify and dispatch ──
    routed_by_manager = req.task_hint == TaskClass.AUTO
    if routed_by_manager:
        recent_user_msgs: list[str] = []
        async with AsyncSessionLocal() as session:
            recent = (
                await session.execute(
                    select(AgentMessage)
                    .where(AgentMessage.user_id == user_id)
                    .where(AgentMessage.conversation_id == convo_id)
                    .where(AgentMessage.role == "user")
                    .order_by(AgentMessage.created_at.desc())
                    .limit(3)
                )
            ).scalars().all()
            recent_user_msgs = [m.content for m in reversed(recent)]
        resolved_task = await classify_turn(req.message, recent_user_msgs)
    else:
        resolved_task = req.task_hint

    # Persist the user turn (with the *resolved* task tag) before invoking the
    # agent so it survives even if the LLM call fails.
    async with AsyncSessionLocal() as session:
        session.add(
            AgentMessage(
                user_id=user_id,
                conversation_id=convo_id,
                role="user",
                content=req.message,
                task=resolved_task.value,
                attached_file_ids=attachment_ids,
            )
        )
        await session.commit()

    # Load per-user prompt overrides + effective LLM config (providers + keys).
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
    prompt_overrides = profile.coach_prompts if profile else None
    eff = await load_effective_config(user_id)

    # Resolve provider: user override > .env default. Then fall back if the
    # chosen cloud provider has neither user nor .env API key.
    settings = get_settings()
    local_only = bool(profile.local_only) if profile else False

    if local_only:
        # User has flipped the privacy switch: every turn stays on local LLM.
        # We deliberately ignore the per-coach provider override and the
        # image→Anthropic auto-bump. Cloud-only features (image gen) get
        # blocked separately at the tool level.
        resolved_provider = Provider.LOCAL
        log.info("Local-only mode active — forcing local LLM", task=resolved_task.value)
    else:
        resolved_provider = eff.provider_for(resolved_task.value, _env_provider_for(resolved_task))

        def _has_key(p: Provider) -> bool:
            if eff.key_for(p):
                return True
            if p == Provider.ANTHROPIC:
                return bool(settings.anthropic_api_key)
            if p == Provider.OPENAI:
                return bool(settings.openai_api_key)
            return True  # local always usable

        if resolved_provider in (Provider.ANTHROPIC, Provider.OPENAI) and not _has_key(
            resolved_provider
        ):
            log.warning(
                "Falling back to local: no API key for chosen provider",
                provider=resolved_provider.value,
            )
            resolved_provider = Provider.LOCAL

        # If the user attached an image and the task would otherwise route to
        # the local llama.cpp endpoint (not multimodal), bump this single run
        # to Anthropic so the image is actually visible to the model.
        if (
            has_image(parts)
            and resolved_provider == Provider.LOCAL
            and _has_key(Provider.ANTHROPIC)
        ):
            log.info("Routing image-bearing turn to anthropic", task=resolved_task.value)
            resolved_provider = Provider.ANTHROPIC

    api_key = resolve_api_key(resolved_provider, eff)
    model = build_model(resolved_provider, api_key=api_key)

    # Append a language directive so the agent answers in the user's
    # preferred language (independent of any prompt overrides).
    base_prompt = resolve_prompt(resolved_task, prompt_overrides)
    lang_directive = ""
    if profile and profile.preferred_language == "de":
        lang_directive = "\n\nAlways respond in German (Deutsch). Use Swiss spelling where natural."
    elif profile and profile.preferred_language == "en":
        lang_directive = "\n\nAlways respond in English."

    agent: Agent[AgentDeps, str] = Agent(
        model=model,
        deps_type=AgentDeps,
        system_prompt=base_prompt + lang_directive,
    )
    register_tools(agent)
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
                        task=resolved_task.value,
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
                resolved_task=resolved_task.value,
                routed_by_manager=routed_by_manager,
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
    user_id: Annotated[UUID, Depends(get_approved_user_id)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
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
async def clear_history(user_id: Annotated[UUID, Depends(get_approved_user_id)]) -> None:
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
