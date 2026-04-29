# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repo.

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

Single-user fitness coaching API. Clients (iOS, web, Home Assistant, curl) send chat messages to `POST /chat`. Endpoint builds PydanticAI agent, runs message through it, returns LLM reply.

### LLM routing (`app/agent/router.py`)

Each request carries `task_hint` (`chat`, `plan_generation`, `nutrition_analysis`, `progress_review`). Router maps to provider (`local` | `anthropic` | `openai`) via `.env` settings, falls back to local llama.cpp server if cloud key absent. Local server = OpenAI-compatible llama.cpp endpoint on Windows at `10.1.10.50:8080`.

### Agent and tools (`app/agent/`)

`agent.py` builds PydanticAI `Agent`, defines `AgentDeps` (injected into every tool call):
- `session_factory`: callable returning async context manager for DB session — **not a live session**. Each tool must open own session via `async with ctx.deps.session_factory() as session:`. Prevents "concurrent operations not permitted" when PydanticAI runs tools in parallel.
- `user_id`: always `UUID("00000000-0000-0000-0000-000000000001")` while auth stub in place.

`tools.py` registers seven tools: `get_user_profile`, `get_recent_workouts`, `log_completed_workout`, `create_workout_session`, `get_recent_meals`, `log_meal`, `get_recent_health_metrics`.

### Auth (`app/api/deps.py`)

Stub: any non-empty bearer token resolves to `SINGLE_USER_ID`. Only this file changes when real JWT auth added.

### Scheduler (`app/scheduler/jobs.py`)

APScheduler runs `generate_next_week_plan` every Sunday at 19:00 (Europe/Zurich). Drives agent with structured prompt that calls tools to build and persist coming week's workout plan.

### Database (`app/db/`)

PostgreSQL 17 via SQLModel (SQLAlchemy async). Models use JSONB for `equipment`, `macro_targets`, `exercises`, similar list/dict fields. Every table has `user_id` even with single user.

Production: Postgres in separate LXC at `10.1.10.10`; app at `10.1.10.103`.

## Key constraints

- **Session factory pattern mandatory.** Every new tool uses `async with ctx.deps.session_factory() as session:`, never share sessions between tools.
- **Agent must write, not describe.** System prompt directs agent to always call `create_workout_session` when planning. If LLM describes workout in prose instead of calling tool, make prompt more directive (add "Use create_workout_session to persist it").
- **Retry on transient cloud errors.** `app/api/chat.py` retries 3× with exponential backoff (2s, 4s, 8s) for HTTP 429/502/503/504/529. New cloud-facing code follows same pattern.