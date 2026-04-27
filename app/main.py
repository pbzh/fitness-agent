"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin,
    auth,
    calendar,
    chat,
    config,
    dashboard,
    files,
    gdpr,
    health,
    profile,
    workouts,
)
from app.config import get_settings
from app.scheduler.jobs import start_scheduler, stop_scheduler

settings = get_settings()
log = structlog.get_logger()


async def _probe_local_model() -> None:
    """Query the local llama.cpp server for the loaded model and cache it."""
    from app.agent.router import set_probed_local_model

    url = settings.local_llm_base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                url, headers={"Authorization": f"Bearer {settings.local_llm_api_key}"}
            )
            r.raise_for_status()
            models = r.json().get("data", [])
            if models:
                name = models[0]["id"]
                set_probed_local_model(name)
                log.info("Local model probed", model=name)
    except Exception as exc:
        log.warning("Could not probe local model, using config default", error=str(exc))


async def _verify_stored_secrets() -> None:
    """Decrypt every stored API-key ciphertext at boot.

    Catches the 'restart broke decryption' failure mode immediately —
    typically caused by the encryption key file being deleted, replaced,
    or shadowed by a SETTINGS_ENCRYPTION_KEY env mismatch.
    """
    from app.security.secrets import verify_all

    try:
        ok, failures = await verify_all()
    except Exception as exc:
        log.warning("Secret-decryption self-test errored", error=str(exc))
        return
    if failures:
        log.error(
            "Stored API keys could not be decrypted — encryption key may have changed",
            failed=failures,
            ok_count=ok,
        )
    else:
        log.info("Secret-decryption self-test passed", ok_count=ok)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _probe_local_model()
    await _verify_stored_secrets()
    log.info("Starting fitness agent", model_local=settings.local_llm_model)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Fitness Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(config.router)
app.include_router(dashboard.router)
app.include_router(chat.router)
app.include_router(workouts.router)
app.include_router(profile.router)
app.include_router(gdpr.router)
app.include_router(files.router)
app.include_router(calendar.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
