"""Symmetric encryption for user-provided secrets (API keys).

Key resolution, in order:
1. ``SETTINGS_ENCRYPTION_KEY`` env var (preferred for production: backed by
   your secrets manager / systemd environment file).
2. ``<file_storage_dir>/.encryption.key`` — auto-generated on first run with
   0600 perms. Lets a fresh dev install Just Work; do NOT delete this file
   in production without rotating, or all stored API keys become unreadable.

The format is Fernet (AES-128-CBC + HMAC-SHA256, 32-byte url-safe-b64 key).
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

log = structlog.get_logger()

_fernet: Fernet | None = None


def _key_path() -> Path:
    return Path(get_settings().file_storage_dir) / ".encryption.key"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("SETTINGS_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()

    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    with suppress(OSError):
        path.chmod(0o600)
    log.warning(
        "Generated new SETTINGS_ENCRYPTION_KEY",
        path=str(path),
        action="back this file up — losing it makes stored API keys unrecoverable",
    )
    return key


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Return a Fernet token (url-safe base64) for the given string."""
    return fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str | None:
    """Return plaintext, or None if the ciphertext is unreadable.

    Unreadable means the encryption key changed or the value was never
    encrypted with our key. Callers fall back to ``.env`` defaults rather
    than crashing.
    """
    try:
        return fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


async def verify_all() -> tuple[int, list[tuple[str, str]]]:
    """Decrypt every ``api_keys_enc`` row in the DB.

    Returns ``(ok_count, failures)`` where each failure is a
    ``(user_id, provider)`` tuple identifying the unreadable ciphertext.
    Used by the startup self-test and the daily verify-keys cron.
    """
    from sqlalchemy import text

    from app.db.session import engine

    ok = 0
    failures: list[tuple[str, str]] = []
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT user_id, api_keys_enc FROM userprofile "
                "WHERE api_keys_enc IS NOT NULL"
            )
        )
        for user_id, enc in rows:
            for provider, ciphertext in (enc or {}).items():
                if decrypt(ciphertext) is None:
                    failures.append((str(user_id), provider))
                else:
                    ok += 1
    return ok, failures
