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
    AUTO = "auto"  # router/manager: pick the best sub-coach per turn
    CHAT = "chat"
    LOGGING = "logging"
    QUICK_LOOKUP = "quick_lookup"
    PLAN_GENERATION = "plan_generation"
    NUTRITION_ANALYSIS = "nutrition_analysis"
    PROGRESS_REVIEW = "progress_review"
    MENTAL_HEALTH = "mental_health"


# Tasks Boss is allowed to dispatch to. TaskClass.CHAT remains for older rows
# and internal fallback paths, but it is no longer exposed as a coach.
DISPATCHABLE_TASKS: tuple[TaskClass, ...] = (
    TaskClass.PLAN_GENERATION,
    TaskClass.NUTRITION_ANALYSIS,
    TaskClass.PROGRESS_REVIEW,
    TaskClass.MENTAL_HEALTH,
)


class Provider(StrEnum):
    LOCAL = "local"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


def _env_provider_for(task: TaskClass) -> Provider:
    """The .env-configured provider for ``task``, ignoring user overrides."""
    settings = get_settings()
    task_provider_map = {
        TaskClass.AUTO:             settings.provider_for_boss,
        TaskClass.PLAN_GENERATION:  settings.provider_for_planning,
        TaskClass.NUTRITION_ANALYSIS: settings.provider_for_nutrition,
        TaskClass.PROGRESS_REVIEW:  settings.provider_for_progress,
        TaskClass.MENTAL_HEALTH:    settings.provider_for_mental_health,
    }
    return Provider(task_provider_map.get(task, settings.provider_for_chat))


def _resolve_provider(task: TaskClass) -> Provider:
    """Backwards-compat: .env-only resolver (used by the manager classifier)."""
    settings = get_settings()
    chosen = _env_provider_for(task)
    # Fall back to local if the chosen cloud provider has no API key
    if chosen == Provider.ANTHROPIC and not settings.anthropic_api_key:
        chosen = Provider.LOCAL
    if chosen == Provider.OPENAI and not settings.openai_api_key:
        chosen = Provider.LOCAL
    return chosen


def build_model(provider: Provider, api_key: str | None = None) -> Model:
    """Construct a PydanticAI Model for ``provider``.

    ``api_key`` overrides the .env value when provided.
    """
    settings = get_settings()

    if provider == Provider.ANTHROPIC:
        return AnthropicModel(
            model_name=settings.anthropic_model,
            provider=AnthropicProvider(api_key=api_key or settings.anthropic_api_key),
        )

    if provider == Provider.OPENAI:
        return OpenAIModel(
            model_name=settings.openai_model,
            provider=OpenAIProvider(api_key=api_key or settings.openai_api_key),
        )

    # Local llama.cpp / Ollama via OpenAI-compatible endpoint
    return OpenAIModel(
        model_name=get_effective_local_model(),
        provider=OpenAIProvider(
            base_url=settings.local_llm_base_url,
            api_key=api_key or settings.local_llm_api_key,
        ),
    )


def get_model_for_task(task: TaskClass, override_provider: Provider | None = None) -> Model:
    """.env-only model factory (kept for the manager classifier and other
    code paths that don't have a user_id at hand)."""
    return build_model(override_provider or _resolve_provider(task))
