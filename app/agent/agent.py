"""PydanticAI agent definition.

The agent passes a session FACTORY in deps, not a session. Each tool opens
its own short-lived session via `async with deps.session_factory() as s`.
This avoids "concurrent operations not permitted" when PydanticAI runs
multiple tools in parallel.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.router import Provider, TaskClass, get_model_for_task

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class AgentDeps:
    """Dependencies injected into every tool call."""

    session_factory: SessionFactory
    user_id: UUID


def build_agent(
    task: TaskClass = TaskClass.CHAT,
    override_provider: Provider | None = None,
) -> Agent[AgentDeps, str]:
    """Construct a PydanticAI agent. Tools are registered separately."""
    from app.agent.prompts import get_prompt

    model = get_model_for_task(task, override_provider=override_provider)
    agent = Agent(
        model=model,
        deps_type=AgentDeps,
        system_prompt=get_prompt(task),
    )
    from app.agent import tools as _tools

    _tools.register_tools(agent)
    return agent
