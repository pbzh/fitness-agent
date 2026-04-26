#!/bin/bash
export DATABASE_URL="${DATABASE_URL/postgresql:\/\//postgresql+asyncpg://}"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
