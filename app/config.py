"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["local", "anthropic", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    # Local LLM (llama.cpp on Windows/B50)
    local_llm_base_url: str = "http://localhost:8080/v1"
    local_llm_model: str = "qwen3-32b-q4"
    local_llm_api_key: str = "not-needed"

    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-4-7"

    # OpenAI
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.3"

    # Per-task provider routing
    provider_for_chat: ProviderName = "local"
    provider_for_planning: ProviderName = "anthropic"
    provider_for_nutrition: ProviderName = "anthropic"
    provider_for_progress: ProviderName = "anthropic"

    # External
    garmin_email: str | None = None
    garmin_password: str | None = None
    google_calendar_id: str | None = None

    log_level: str = "INFO"
    timezone: str = "Europe/Zurich"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
