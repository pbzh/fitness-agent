"""Boss coach: classify a user turn into the right sub-coach.

Runs a lightweight, structured-output agent against the internal chat model
(usually the local llama.cpp endpoint) so routing is fast and free. Falls
back to TaskClass.PLAN_GENERATION on any error — the worst case is "wrong coach
answered", never "request 500s".
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic_ai import Agent

from app.agent.prompts import BOSS_PROMPT
from app.agent.router import DISPATCHABLE_TASKS, Provider, TaskClass
from app.config import get_settings

log = structlog.get_logger()


_LITERAL = Literal[
    "plan_generation",
    "nutrition_analysis",
    "progress_review",
    "mental_health",
]


async def classify_turn(
    message: str,
    recent_user_msgs: list[str],
    boss_provider: Provider | None = None,
    prompt_override: str | None = None,
    api_key: str | None = None,
) -> TaskClass:
    """Return the best dispatchable task for this turn.

    ``boss_provider`` is the resolved provider (user override > .env default).
    ``api_key`` is the user's DB-stored key for that provider (may be None).
    ``prompt_override`` replaces the built-in BOSS_PROMPT when set.
    Falls back to Anthropic if available, otherwise local.
    """
    if boss_provider is None:
        boss_provider = Provider.ANTHROPIC if get_settings().anthropic_api_key else Provider.LOCAL
    from app.agent.router import build_model
    model = build_model(boss_provider, api_key=api_key)
    classifier: Agent[None, str] = Agent(
        model=model,
        output_type=_LITERAL,
        system_prompt=prompt_override or BOSS_PROMPT,
    )

    context = ""
    if recent_user_msgs:
        joined = "\n".join(f"- {m[:200]}" for m in recent_user_msgs[-3:])
        context = f"Recent user messages:\n{joined}\n\n"

    prompt = f"{context}Latest user message:\n{message[:1500]}"

    try:
        result = await classifier.run(prompt)
        task = TaskClass(result.output)
        if task not in DISPATCHABLE_TASKS:
            return TaskClass.PLAN_GENERATION
        log.info("Boss classified turn", task=task.value)
        return task
    except Exception as exc:
        log.warning("Boss classifier failed, defaulting to fitness", error=str(exc))
        return TaskClass.PLAN_GENERATION
