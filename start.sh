#!/bin/bash
export DATABASE_URL=$(python3 -c "
import os, re
url = os.environ['DATABASE_URL']
url = url.replace('postgresql://', 'postgresql+asyncpg://')
url = re.sub(r'[?&]sslmode=[^&]*', '', url)
url = url.rstrip('?&')
print(url)
")
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
