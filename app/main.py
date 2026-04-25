"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api import chat, health, workouts
from app.config import get_settings
from app.scheduler.jobs import start_scheduler, stop_scheduler

settings = get_settings()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
app.include_router(chat.router)
app.include_router(workouts.router)
