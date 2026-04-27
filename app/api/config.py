"""Config endpoint — exposes and mutates LLM routing settings."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.agent.router import TaskClass, Provider, _resolve_provider, get_effective_local_model
from app.api.deps import get_current_user_id
from app.config import get_settings

router = APIRouter(prefix="/config", tags=["config"])

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

_TASK_ENV_KEY: dict[TaskClass, str] = {
    TaskClass.CHAT:               "PROVIDER_FOR_CHAT",
    TaskClass.PLAN_GENERATION:    "PROVIDER_FOR_PLANNING",
    TaskClass.NUTRITION_ANALYSIS: "PROVIDER_FOR_NUTRITION",
    TaskClass.PROGRESS_REVIEW:    "PROVIDER_FOR_PROGRESS",
    TaskClass.MENTAL_HEALTH:      "PROVIDER_FOR_MENTAL_HEALTH",
}

# Tasks exposed to the UI (internal ones like logging/quick_lookup are excluded)
_UI_TASKS = set(_TASK_ENV_KEY)


class RoutingUpdate(BaseModel):
    task: str
    provider: str


def _routing_snapshot() -> dict[str, dict[str, str]]:
    settings = get_settings()
    model_map = {
        "local":     get_effective_local_model(),
        "anthropic": settings.anthropic_model,
        "openai":    settings.openai_model,
    }
    result = {}
    for task in _UI_TASKS:
        provider = str(_resolve_provider(task))
        result[task.value] = {"provider": provider, "model": model_map.get(provider, "")}
    return result


@router.get("/routing")
def get_routing() -> dict[str, dict[str, str]]:
    return _routing_snapshot()


@router.patch("/routing")
def update_routing(
    body: RoutingUpdate,
    _: Annotated[UUID, Depends(get_current_user_id)],
) -> dict[str, dict[str, str]]:
    try:
        task = TaskClass(body.task)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown task: {body.task}")

    try:
        provider = Provider(body.provider)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider: {body.provider}")

    env_key = _TASK_ENV_KEY.get(task)
    if env_key is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Task '{task}' is not configurable")

    set_key(str(_ENV_FILE), env_key, provider.value)
    get_settings.cache_clear()

    return _routing_snapshot()
