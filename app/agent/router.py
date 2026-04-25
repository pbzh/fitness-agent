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


class TaskClass(StrEnum):
    CHAT = "chat"
    LOGGING = "logging"
    QUICK_LOOKUP = "quick_lookup"
    PLAN_GENERATION = "plan_generation"
    NUTRITION_ANALYSIS = "nutrition_analysis"
    PROGRESS_REVIEW = "progress_review"


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
    }
    chosen = task_provider_map.get(task, settings.provider_for_chat)

    # Fall back to local if the chosen cloud provider has no API key
    if chosen == Provider.ANTHROPIC and not settings.anthropic_api_key:
        chosen = Provider.LOCAL
    if chosen == Provider.OPENAI and not settings.openai_api_key:
        chosen = Provider.LOCAL

    return chosen


def get_model_for_task(task: TaskClass) -> Model:
    """Return a configured PydanticAI Model for the given task class."""
    settings = get_settings()
    provider = _resolve_provider(task)

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

    # Local llama.cpp via OpenAI-compatible endpoint
    return OpenAIModel(
        model_name=settings.local_llm_model,
        provider=OpenAIProvider(
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
        ),
    )
