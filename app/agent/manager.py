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

from app.agent.router import DISPATCHABLE_TASKS, Provider, TaskClass, get_model_for_task
from app.config import get_settings

log = structlog.get_logger()


_LITERAL = Literal[
    "plan_generation",
    "nutrition_analysis",
    "progress_review",
    "mental_health",
]


CLASSIFIER_PROMPT = """You are Boss, the Orchestrator managing multiple specialized coaches:
- Fitness
- Nutrition
- Mental Health
- Productivity

Your role:
- Route user requests to the correct coach(s)
- Combine outputs into a coherent plan
- Resolve conflicts between coaches (e.g., training vs recovery)

Rules:
- Do not duplicate information
- Prioritize user goals and constraints
- Keep responses structured and concise

When multiple domains are involved:
- Merge outputs into a single actionable plan
- Highlight trade-offs clearly

This app currently dispatches one primary coach per turn. Given the user's
latest message and brief context, output exactly ONE internal routing label
from this list — nothing else, no explanation:

- plan_generation: Fitness. Workouts, weekly plans, training schedules,
  exercise selection, recovery days, strength, climbing, mobility.
- nutrition_analysis: Nutrition. Meals, recipes, macros, calories, hunger,
  food logging, supplements, hydration.
- mental_health: Mental Health. Mood, stress, anxiety, motivation, burnout,
  self-talk, feelings, pressure, overwhelm, sleep quality.
- progress_review: Productivity. Prioritization, planning non-training tasks,
  habit systems, goal review, schedule conflicts, "what should I focus on?"

Bias toward `mental_health` whenever the message is about how the user feels.
Bias toward `plan_generation` when the user asks for training plans or workout
changes. Bias toward `progress_review` for general planning, task management,
or ambiguous "what should I do?" requests.
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
            return TaskClass.PLAN_GENERATION
        log.info("Boss classified turn", task=task.value)
        return task
    except Exception as exc:
        log.warning("Boss classifier failed, defaulting to fitness", error=str(exc))
        return TaskClass.PLAN_GENERATION
