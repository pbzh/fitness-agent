"""Resolve a user's effective LLM config (providers + API keys).

DB overrides win; ``.env`` defaults fill anything the user hasn't set. API
keys live encrypted in ``UserProfile.api_keys_enc`` and are decrypted only
when needed for a request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog
from sqlmodel import select

from app.agent.router import Provider
from app.config import get_settings
from app.db.models import UserProfile
from app.db.session import AsyncSessionLocal
from app.security.secrets import decrypt

log = structlog.get_logger()


@dataclass
class EffectiveLLMConfig:
    coach_providers: dict[str, str] = field(default_factory=dict)
    api_keys: dict[str, str] = field(default_factory=dict)

    def provider_for(self, task: str, default: Provider) -> Provider:
        chosen = self.coach_providers.get(task)
        if chosen:
            try:
                return Provider(chosen)
            except ValueError:
                pass
        return default

    def key_for(self, provider: Provider) -> str | None:
        """User-overridden API key for ``provider``, or None to use .env."""
        return self.api_keys.get(provider.value)


async def load_effective_config(user_id: UUID) -> EffectiveLLMConfig:
    async with AsyncSessionLocal() as session:
        profile = (
            await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        ).scalar_one_or_none()

    cfg = EffectiveLLMConfig()
    if not profile:
        return cfg

    cfg.coach_providers = dict(profile.coach_providers or {})

    for provider_name, ciphertext in (profile.api_keys_enc or {}).items():
        plain = decrypt(ciphertext)
        if plain:
            cfg.api_keys[provider_name] = plain
        else:
            log.warning(
                "Could not decrypt stored API key — falling back to .env",
                provider=provider_name,
                user_id=str(user_id),
            )
    return cfg


def resolve_api_key(provider: Provider, cfg: EffectiveLLMConfig) -> str | None:
    """User key if present.

    Cloud-provider keys are user-scoped and stored encrypted in the DB.
    The local provider may still use a deployment-level API key when the
    upstream local endpoint requires one.
    """
    user_key = cfg.key_for(provider)
    if user_key:
        return user_key
    settings = get_settings()
    if provider == Provider.LOCAL:
        return settings.local_llm_api_key
    return None
