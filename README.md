# Fitness Agent

Personal fitness and meal planning agent. API-first backend designed for homelab
deployment with future iOS client in mind.

## What it does

- Generates weekly workout plans tailored to your goals, equipment, and recent training
- Logs completed workouts and tracks progressive overload
- Plans meals aligned with macro targets and dietary preferences
- Generates grocery lists from meal plans
- Ingests health metrics from Garmin/Apple Health *(planned)*
- Auto-generates next week's plan every Sunday evening

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + uvicorn |
| Agent framework | PydanticAI 1.x |
| LLM (local) | llama.cpp server (OpenAI-compatible endpoint) |
| LLM (cloud) | Anthropic Claude or OpenAI GPT (per-task routing) |
| Database | PostgreSQL 17 + SQLModel (async) |
| Migrations | Alembic |
| Scheduler | APScheduler |
| Package manager | uv |

## Architecture

```
Clients (iOS app, web UI, Home Assistant, curl)
                  │ HTTPS + JWT
                  ▼
            FastAPI (uvicorn)
                  │
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
Postgres    PydanticAI      APScheduler
(state)      agent         (Sun 19:00)
                │
        ┌───────┴────────┐
        ▼                ▼
   Provider Router     Tools
        │            - profile
   ┌────┼────┐       - workouts
   ▼    ▼    ▼       - meals
local  Claude GPT    - health metrics
(B50)
```

### Per-task LLM routing

Configurable in `.env` — each task picks `local`, `anthropic`, or `openai`:

| Task | Default | Why |
|---|---|---|
| `chat` | local | conversational, no API cost |
| `plan_generation` | anthropic | best at multi-constraint planning |
| `nutrition_analysis` | anthropic | strong at structured math |
| `progress_review` | anthropic | better at trend synthesis |

Falls back to local automatically if a cloud provider's API key is missing.

## Repository layout

```
fitness-agent/
├── app/
│   ├── main.py              FastAPI entrypoint
│   ├── config.py            pydantic-settings, loads .env
│   ├── agent/
│   │   ├── agent.py         PydanticAI agent + system prompt
│   │   ├── router.py        local/cloud routing per task
│   │   └── tools.py         seven tools the agent can call
│   ├── api/
│   │   ├── auth.py          POST /auth/login JWT login
│   │   ├── chat.py          POST /chat with retry on transient errors
│   │   ├── workouts.py      direct REST for the iOS app
│   │   ├── health.py        GET /healthz
│   │   └── deps.py          JWT auth dependency
│   ├── db/
│   │   ├── models.py        SQLModel schema
│   │   └── session.py       async session factory
│   └── scheduler/jobs.py    Sunday weekly plan generation
├── alembic/                 database migrations
├── docker-compose.yml       Postgres for local dev (production runs in LXC)
├── pyproject.toml           uv-managed deps
├── .env.example
├── .gitignore
└── README.md
```

## Deployment (Proxmox)

Two LXC containers on the Server VLAN:

| LXC | Purpose | Resources | IP |
|---|---|---|---|
| fitness-db | Postgres 17 | 2 vCPU / 2 GB / 16 GB | `10.1.10.10` |
| fitness-agent | FastAPI app | 2 vCPU / 1 GB / 8 GB | `10.1.10.103` |

llama.cpp runs separately on a Windows workstation with an Intel Arc Pro B50,
exposed on `:8080`. Anthropic API and OpenAI API are reached over the internet.

### First-time setup

#### Postgres LXC

```bash
apt update && apt install -y postgresql-17

sudo -u postgres psql <<EOF
CREATE USER fitness WITH PASSWORD '<strong-password>';
CREATE DATABASE fitness OWNER fitness;
EOF

# Edit /etc/postgresql/17/main/pg_hba.conf — allow agent LXC only:
# host    fitness    fitness    10.1.10.103/32    scram-sha-256

# Edit /etc/postgresql/17/main/postgresql.conf:
# listen_addresses = '*'

systemctl restart postgresql
```

#### Agent LXC

```bash
apt update && apt install -y python3 git curl locales
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# Set locale (Swiss server)
sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
sed -i 's/# de_CH.UTF-8 UTF-8/de_CH.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8 LC_TIME=de_CH.UTF-8 LC_NUMERIC=de_CH.UTF-8

# Pull the project
mkdir -p /opt && cd /opt
git clone <your-private-repo-url> fitness-agent
cd fitness-agent

cp .env.example .env
$EDITOR .env  # see Configuration section below

uv sync
uv run alembic upgrade head
```

#### systemd unit

`/etc/systemd/system/fitness-agent.service`:

```ini
[Unit]
Description=Fitness Agent API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fitness-agent
Environment="PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/root/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now fitness-agent
journalctl -u fitness-agent -f
```

## Configuration

`.env` (all settings):

```bash
# Database
DATABASE_URL=postgresql+asyncpg://fitness:PASSWORD@10.1.10.10:5432/fitness

# Auth
JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">

# Local LLM (llama.cpp on Windows/B50)
LOCAL_LLM_BASE_URL=http://10.1.10.50:8080/v1
LOCAL_LLM_MODEL=qwen3-32b-q4

# Cloud LLM keys (leave blank to disable that provider)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-7
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.3

# Per-task routing: local | anthropic | openai
PROVIDER_FOR_CHAT=local
PROVIDER_FOR_PLANNING=anthropic
PROVIDER_FOR_NUTRITION=anthropic
PROVIDER_FOR_PROGRESS=anthropic

TIMEZONE=Europe/Zurich
SCHEDULER_USER_ID=00000000-0000-0000-0000-000000000001
```

**Spending caps:** set hard monthly limits in the Anthropic/OpenAI consoles before
running the service. Recommended: $10/month while developing.

## Bootstrapping your user

Create the first login account once. Set the email and password explicitly so
the stored password is hashed before it reaches the database:

```bash
cd /opt/fitness-agent
BOOTSTRAP_EMAIL=you@example.com BOOTSTRAP_PASSWORD='<strong-password>' \
  uv run python scripts/bootstrap_user.py
```

The web UI served at `/` uses `POST /auth/login` and stores the returned JWT in
browser local storage. API clients should do the same login first, then pass the
token as `Authorization: Bearer <token>`.

## Smoke testing

```bash
# Health
curl http://10.1.10.103:8000/healthz

# Local LLM path
TOKEN=$(curl -s -X POST http://10.1.10.103:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "<strong-password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -X POST http://10.1.10.103:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my primary fitness goal?", "task_hint": "chat"}'

# Cloud LLM path with explicit write directive
curl -X POST http://10.1.10.103:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Schedule a workout for tomorrow. Use create_workout_session to persist it after checking my profile and recent training.",
    "task_hint": "plan_generation"
  }'

# Verify writes landed
psql postgresql://fitness:PASSWORD@10.1.10.10:5432/fitness \
  -c "SELECT scheduled_date, workout_type, intensity, duration_min FROM workoutsession ORDER BY scheduled_date;"
```

## Operations

### Logs

```bash
journalctl -u fitness-agent -f
journalctl -u fitness-agent --since "1 hour ago"
```

### Database backups

In the Postgres LXC, `/etc/cron.daily/pg_backup`:

```bash
#!/bin/bash
sudo -u postgres pg_dump fitness | gzip > /var/backups/fitness_$(date +\%Y\%m\%d).sql.gz
find /var/backups/fitness_*.sql.gz -mtime +14 -delete
```

In addition, Proxmox backs up both LXCs nightly to PBS or local storage.

### Common issues

**`status_code: 529, overloaded_error`** — Anthropic API overloaded. Transient.
The chat endpoint retries 3× with exponential backoff automatically. If it
persists, check https://status.anthropic.com/ or flip `PROVIDER_FOR_PLANNING=openai`
as a fallback.

**`concurrent operations are not permitted`** — fixed in `tools.py` by giving each
tool its own short-lived session via `deps.session_factory()`. If this resurfaces,
it means a tool is sharing a session with another tool. Each tool must wrap its
DB work in `async with ctx.deps.session_factory() as session:`.

**Agent reply doesn't write to DB** — the LLM chose to describe in prose rather
than call `create_workout_session`. Make the prompt directive ("Use
create_workout_session to schedule it") and ensure the system prompt's operating
principles spell out when to write.

**`UserLocation` import error** — pydantic-ai SDK version mismatch. Bump to
`pydantic-ai[anthropic,openai]>=1.5.0` and clear `__pycache__`.

## Roadmap

- [x] Schema, FastAPI skeleton, agent with 7 tools
- [x] Local + cloud LLM routing
- [x] Sunday weekly plan generation (APScheduler)
- [x] Retry logic for transient cloud errors
- [x] Real JWT auth
- [ ] Meal planning REST endpoints
- [ ] Open Food Facts / USDA FDC nutrition lookup tools
- [ ] wger exercise database integration
- [ ] Garmin Connect ingestion service
- [ ] Apple HealthKit upload endpoint (for the iOS app)
- [ ] Grocery list generation from meal plans
- [ ] Home Assistant conversation agent integration
- [ ] iOS app (separate repo, SwiftUI, generated client from OpenAPI spec)

## License

Private. Not for distribution.
