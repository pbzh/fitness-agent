"""LLM routing: local llama.cpp for chat, cloud for heavy reasoning.

Three providers supported:
- Local llama.cpp (free, runs on the B50)
- Anthropic API (Claude)
- OpenAI API (GPT)
"""

from enum import StrEnum

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.config import get_settings

# Populated at startup by probing the local server; falls back to settings value.
_probed_local_model: str | None = None


def set_probed_local_model(name: str) -> None:
    global _probed_local_model
    _probed_local_model = name


def get_effective_local_model() -> str:
    return _probed_local_model or get_settings().local_llm_model


class TaskClass(StrEnum):
    CHAT = "chat"
    LOGGING = "logging"
    QUICK_LOOKUP = "quick_lookup"
    PLAN_GENERATION = "plan_generation"
    NUTRITION_ANALYSIS = "nutrition_analysis"
    PROGRESS_REVIEW = "progress_review"
    MENTAL_HEALTH = "mental_health"


class Provider(StrEnum):
    LOCAL = "local"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


def _resolve_provider(task: TaskClass) -> Provider:
    """Decide which provider handles this task based on .env config."""
    settings = get_settings()

    task_provider_map = {
        TaskClass.PLAN_GENERATION: settings.provider_for_planning,
        TaskClass.NUTRITION_ANALYSIS: settings.provider_for_nutrition,
        TaskClass.PROGRESS_REVIEW: settings.provider_for_progress,
        TaskClass.MENTAL_HEALTH: settings.provider_for_mental_health,
    }
    chosen = task_provider_map.get(task, settings.provider_for_chat)

    # Fall back to local if the chosen cloud provider has no API key
    if chosen == Provider.ANTHROPIC and not settings.anthropic_api_key:
        chosen = Provider.LOCAL
    if chosen == Provider.OPENAI and not settings.openai_api_key:
        chosen = Provider.LOCAL

    return chosen


def get_model_for_task(task: TaskClass, override_provider: Provider | None = None) -> Model:
    """Return a configured PydanticAI Model for the given task class.

    ``override_provider`` lets callers force a specific provider for one run
    (e.g. force Anthropic when the user attached an image and the task would
    otherwise route to the local non-multimodal llama.cpp endpoint).
    """
    settings = get_settings()
    provider = override_provider or _resolve_provider(task)

    if provider == Provider.ANTHROPIC:
        return AnthropicModel(
            model_name=settings.anthropic_model,
            provider=AnthropicProvider(api_key=settings.anthropic_api_key),
        )

    if provider == Provider.OPENAI:
        return OpenAIModel(
            model_name=settings.openai_model,
            provider=OpenAIProvider(api_key=settings.openai_api_key),
        )

    # Local llama.cpp / Ollama via OpenAI-compatible endpoint
    return OpenAIModel(
        model_name=get_effective_local_model(),
        provider=OpenAIProvider(
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
        ),
    )
