# Fitness Agent

A personal fitness and meal planning backend API built with FastAPI and PydanticAI.

## Architecture

- **Framework**: FastAPI (Python 3.12)
- **Agent**: PydanticAI with task-routing between local and cloud LLMs
- **Database**: PostgreSQL via asyncpg + SQLModel ORM
- **Migrations**: Alembic
- **Scheduler**: APScheduler (weekly plan generation)
- **Package Manager**: uv

## Project Structure

```
app/
  main.py          - FastAPI entrypoint, routes, lifespan
  config.py        - Settings via pydantic-settings (env vars)
  api/             - REST endpoints (auth, chat, workouts, health, profile)
  agent/           - PydanticAI agent logic and task router
  db/              - Database models (SQLModel) and session management
  scheduler/       - Background jobs (weekly plan generation)
  tools/           - Agent tool implementations
alembic/           - Database migration scripts
scripts/           - Utility scripts (bootstrap_user.py)
static/            - Frontend static assets (served at /)
start.sh           - Startup script (transforms DATABASE_URL for asyncpg)
```

## Environment Variables

Required:
- `DATABASE_URL` - PostgreSQL connection string (auto-set by Replit DB)
- `JWT_SECRET` - Secret key for JWT authentication

Optional:
- `ANTHROPIC_API_KEY` - For cloud LLM planning tasks
- `OPENAI_API_KEY` - For OpenAI model routing
- `LOCAL_LLM_BASE_URL` - Local llama.cpp endpoint (default: http://localhost:8080/v1)
- `GARMIN_EMAIL` / `GARMIN_PASSWORD` - Garmin Connect integration
- `GOOGLE_CALENDAR_ID` - Google Calendar integration

## LLM Routing

The agent routes tasks to different providers:
- `provider_for_chat` (default: local)
- `provider_for_planning` (default: anthropic)
- `provider_for_nutrition` (default: anthropic)
- `provider_for_progress` (default: anthropic)

## Running

The app starts via `bash start.sh` which converts the DATABASE_URL from `postgresql://` to `postgresql+asyncpg://` and runs uvicorn on port 5000.

## Database

Uses Replit's built-in PostgreSQL. Migrations managed via Alembic.

To run migrations: `DATABASE_URL=<async_url> uv run alembic upgrade head`
