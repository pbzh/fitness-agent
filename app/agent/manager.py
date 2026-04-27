"""The 'manager' coach: classify a user turn into the right sub-coach.

Runs a lightweight, structured-output agent against the chat-task model
(usually the local llama.cpp endpoint) so routing is fast and free. Falls
back to TaskClass.CHAT on any error — the worst case is "wrong coach
answered", never "request 500s".
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic_ai import Agent

from app.agent.router import DISPATCHABLE_TASKS, Provider, TaskClass, get_model_for_task
from app.config import get_settings

log = structlog.get_logger()


_LITERAL = Literal[
    "chat",
    "plan_generation",
    "nutrition_analysis",
    "progress_review",
    "mental_health",
]


CLASSIFIER_PROMPT = """You are a routing classifier for a fitness coaching app.
Given the user's latest message and brief context, output exactly ONE label
from this list — nothing else, no explanation:

- chat: small talk, generic Q&A, anything that doesn't fit a category below
- plan_generation: user wants a workout / weekly plan / training schedule
  built or modified, or asks "what should I do this week?"
- nutrition_analysis: meals, recipes, macros, calories, hunger, food logging,
  supplements, hydration
- progress_review: trends, weight change, lifts going up, sleep / HRV / RPE
  patterns, "how am I doing?" style questions
- mental_health: mood, stress, anxiety, motivation slumps, burnout, sleep
  *quality* (vs duration), self-talk, frustration, identity around training,
  feelings, pressure, overwhelm

Bias toward `chat` when in doubt. Bias toward `mental_health` whenever the
message is about how the user *feels* (not what their body did). Bias toward
`plan_generation` when the user uses imperative verbs about training
(\"build me\", \"make me a plan\", \"what should I lift today\").
"""


async def classify_turn(message: str, recent_user_msgs: list[str]) -> TaskClass:
    """Return the best dispatchable task for this turn.

    Uses Anthropic (Claude) for the classification — small models occasionally
    refuse or pad the structured-output literal. Falls back to local if no
    Anthropic key is configured.
    """
    # Prefer Anthropic for routing; fall back gracefully if no key.
    override = Provider.ANTHROPIC if get_settings().anthropic_api_key else None
    model = get_model_for_task(TaskClass.CHAT, override_provider=override)
    classifier: Agent[None, str] = Agent(
        model=model,
        output_type=_LITERAL,
        system_prompt=CLASSIFIER_PROMPT,
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
            return TaskClass.CHAT
        log.info("Manager classified turn", task=task.value)
        return task
    except Exception as exc:
        log.warning("Manager classifier failed, defaulting to chat", error=str(exc))
        return TaskClass.CHAT
