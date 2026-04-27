# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the development server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Database migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# Bootstrap the single user (first-time setup only, edit values first)
uv run python scripts/bootstrap_user.py

# Linting
uv run ruff check .
uv run ruff format .

# Type checking
uv run mypy app/

# Run tests
uv run pytest
uv run pytest tests/path/to/test_file.py::test_name  # single test
```

## Architecture

This is a single-user fitness coaching API. Clients (iOS, web, Home Assistant, curl) send chat messages to `POST /chat`. The chat endpoint builds a PydanticAI agent, runs the user's message through it, and returns the LLM's reply.

### LLM routing (`app/agent/router.py`)

Each request carries a `task_hint` (`chat`, `plan_generation`, `nutrition_analysis`, `progress_review`). The router maps these to a provider (`local` | `anthropic` | `openai`) via `.env` settings, falling back to the local llama.cpp server if a cloud key is absent. The local server is an OpenAI-compatible llama.cpp endpoint on a Windows machine at `10.1.10.50:8080`.

### Agent and tools (`app/agent/`)

`agent.py` builds the PydanticAI `Agent` and defines `AgentDeps` (injected into every tool call):
- `session_factory`: a callable returning an async context manager for a DB session — **not a live session**. Each tool must open its own session via `async with ctx.deps.session_factory() as session:`. This prevents "concurrent operations not permitted" when PydanticAI runs tools in parallel.
- `user_id`: always `UUID("00000000-0000-0000-0000-000000000001")` while the auth stub is in place.

`tools.py` registers seven tools on the agent: `get_user_profile`, `get_recent_workouts`, `log_completed_workout`, `create_workout_session`, `get_recent_meals`, `log_meal`, `get_recent_health_metrics`.

### Auth (`app/api/deps.py`)

Currently a stub: any non-empty bearer token resolves to `SINGLE_USER_ID`. The file is designed so only this file needs to change when real JWT auth is added.

### Scheduler (`app/scheduler/jobs.py`)

APScheduler runs `generate_next_week_plan` every Sunday at 19:00 (Europe/Zurich). It drives the agent with a structured prompt that calls tools to build and persist the coming week's workout plan.

### Database (`app/db/`)

PostgreSQL 17 via SQLModel (SQLAlchemy async). Models use JSONB for `equipment`, `macro_targets`, `exercises`, and similar list/dict fields. Every table has `user_id` even though there is currently only one user.

In production, Postgres runs in a separate LXC at `10.1.10.10`; the app runs at `10.1.10.103`.

## Key constraints

- **Session factory pattern is mandatory.** Every new tool must use `async with ctx.deps.session_factory() as session:`, never share sessions between tools.
- **Agent must write, not describe.** The system prompt directs the agent to always call `create_workout_session` when planning. If the LLM describes a workout in prose instead of calling the tool, make the prompt more directive (add "Use create_workout_session to persist it").
- **Retry on transient cloud errors.** `app/api/chat.py` retries 3× with exponential backoff (2s, 4s, 8s) for HTTP 429/502/503/504/529. New cloud-facing code should follow the same pattern.
