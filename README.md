# coacher

<p align="center">
  <img src="static/logo.svg" alt="coacher logo" width="220">
</p>

A self-hosted personal coaching agent: workouts, nutrition, mental-health
check-ins, productivity support, and plan generation, all sharing one rolling
chat thread. API-first, designed for homelab deployment with a future iOS
client in mind.

```
You → "I'm wrecked, can't get myself to train"
            ↓
   🧭 Boss (Anthropic) classifies the turn
            ↓
   🧠 Mental-health coach picks it up, correlates against
      your sleep + RPE history, suggests one small action,
      and logs the mood snapshot.

You → "Build me a hangboard plan for tomorrow at 7am"
            ↓
   🧭 Boss → 🏋️ Fitness coach → create_workout_session
   tool fires → row lands on your calendar.
```

## What it does

- **One chat, four coaches.** Boss classifies each turn and
  routes it to the right specialist (fitness / nutrition / mental health /
  productivity). You can also pin a coach manually.
- **Persistent rolling thread.** Every message — including which coach
  answered — is stored in Postgres and restored on login.
- **Multimodal attachments.** Drop in images (meal photo → log it),
  PDFs (training program → ingest it), DOCX, txt/csv. Images go to Claude
  as binary; PDFs/DOCX get extracted to text.
- **Image generation.** Coaches can call `generate_plan_image` (OpenAI
  `gpt-image-1`) to produce a weekly workout / meal calendar PNG, saved to
  the file store and referenced in the reply.
- **ICS calendar export.** Per-plan or rolling 30-day feed with
  Europe/Zurich VTIMEZONE — drag into Calendar.app, GCal, etc.
- **Per-user customization.** Override each coach's system prompt, pick
  the LLM provider per coach, store your own API keys (encrypted at rest).
- **Auto-generated weekly plans.** APScheduler runs every Sunday 19:00.
- **Single-user, multi-user-ready.** `user_id` is on every table.
  Auth is real JWT with bcrypt; users can self-register.

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI + uvicorn |
| Agent framework | PydanticAI 1.x |
| LLM (local) | llama.cpp / Ollama via OpenAI-compatible endpoint |
| LLM (cloud) | Anthropic Claude or OpenAI GPT (per-coach routing) |
| Image gen | OpenAI `gpt-image-1` |
| Database | PostgreSQL 17 + SQLModel (async) |
| Migrations | Alembic |
| Scheduler | APScheduler |
| Secrets | Fernet (AES-128-CBC + HMAC-SHA256) |
| Package manager | uv |

## Architecture

```
Clients (iOS, web UI, Home Assistant, curl)
                  │ HTTPS + JWT
                  ▼
            FastAPI (uvicorn)
                  │
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
Postgres    PydanticAI      APScheduler
(state)      agent         (Sun 19:00)
                │
       ┌────────┼────────┬─────────────┐
       ▼        ▼        ▼             ▼
Boss       Coaches   Tools         Files
  (router) (4 personas)│        (/opt/fitness-agent-data
                ┌──────┤            + encrypted keys)
                ▼      ▼
        Provider     11 tools
        Resolver  (workouts, meals,
        (per user) health metrics,
                  generate_plan_image,
                  log_mental_state, …)
```

### Boss

A small classifier (Claude by default, configurable) labels each turn into
one of the four sub-coach tasks and dispatches accordingly. The resolved
coach is returned to the client so the UI can show the right chip.

| Coach | Triggers (examples) |
|---|---|
| 🏋️ Fitness | "build me a workout / weekly plan", scheduling |
| 🥗 Nutrition | meals, recipes, macros, "I just ate X" |
| ✅ Productivity | prioritization, goals, schedule conflicts, next actions |
| 🧠 Mental Health | mood, stress, motivation, burnout, sleep *quality* |

The Mental-health coach has explicit safety guardrails: it never tries to
handle a crisis itself, and routes to the Swiss emergency line **143** /
European **112** when warranted.

### Per-coach LLM routing

Each coach can be sent to `local`, `anthropic`, or `openai`. Defaults come
from `.env`; users can override per-coach in Settings. If a chosen cloud
provider has no API key (DB *or* `.env`), routing falls back to local.

If the user attaches an image and the routed coach lives on the local
non-multimodal endpoint, that single turn is bumped to Claude so the model
actually sees the picture.

### Per-coach prompt overrides

Each user can override the system prompt for any of the four coaches in
Settings → "Coach prompts" (8 KB cap per prompt). Empty textarea = use the
built-in default. The Boss prompt itself is *not* user-editable — it's
a structured classifier and free-form prose silently breaks routing.

### Encrypted API keys

API keys can be supplied in `.env` (server-wide) or stored per-user in
the DB. DB keys are encrypted with Fernet. The encryption key resolves
in this order:

1. `SETTINGS_ENCRYPTION_KEY` env var (recommended for production)
2. `<file_storage_dir>/.encryption.key` — auto-generated on first run
   (chmod 600)

If you lose the encryption key, all DB-stored API keys become unreadable
and the runtime falls back to `.env` values with a warning. **Back this
file up.**

API keys are never returned to clients — `GET /profile/llm` reports
`"set"` / `"unset"` only.

## Repository layout

```
coacher/
├── app/
│   ├── main.py              FastAPI entrypoint, local-model probe
│   ├── config.py            pydantic-settings, loads .env
│   ├── agent/
│   │   ├── agent.py         PydanticAI agent factory
│   │   ├── manager.py       auto-router classifier
│   │   ├── prompts.py       per-coach system prompts + user-override resolver
│   │   ├── router.py        provider routing, model factory
│   │   ├── effective_config.py  per-user provider/key resolver
│   │   ├── attachments.py   image / PDF / DOCX → agent input parts
│   │   ├── image_gen.py     OpenAI gpt-image-1 wrapper
│   │   └── tools.py         11 tools (workouts, meals, metrics,
│   │                                   image gen, mental state, …)
│   ├── api/
│   │   ├── auth.py          /auth/login, /auth/register, /auth/change-password
│   │   ├── chat.py          /chat with retries + manager routing + multimodal
│   │   ├── files.py         /files upload / download / delete
│   │   ├── calendar.py      /calendar/{workouts,meals}/{plan_id}.ics
│   │   ├── profile.py       /profile, /profile/llm, /profile/coach-prompts/defaults
│   │   ├── workouts.py      direct REST for the iOS app
│   │   ├── config.py        /config/routing (env-level provider config)
│   │   ├── health.py        /healthz
│   │   └── deps.py          JWT auth dependency
│   ├── db/
│   │   ├── models.py        SQLModel schema
│   │   └── session.py       async session factory
│   ├── files/storage.py     disk-backed file store (sharded by UUID)
│   ├── security/secrets.py  Fernet encrypt/decrypt
│   └── scheduler/jobs.py    Sunday weekly plan generation
├── alembic/versions/        0001 init, 0002 files+extras, 0003 prompts,
│                            0004 user provider/key overrides
├── static/index.html        web UI (vanilla JS, no build step)
├── pyproject.toml
├── .env.example
└── README.md
```

## Deployment (Proxmox)

Two LXC containers on the Server VLAN:

| LXC | Purpose | Resources | IP |
|---|---|---|---|
| fitness-db | Postgres 17 | 2 vCPU / 2 GB / 16 GB | `10.1.10.10` |
| coacher | FastAPI app + file store | 2 vCPU / 1 GB / 16 GB | `10.1.10.103` |

llama.cpp runs separately on a workstation with a GPU, exposed at
`:8080`. Anthropic and OpenAI APIs are reached over the internet.

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

# Locale (Swiss server)
sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
sed -i 's/# de_CH.UTF-8 UTF-8/de_CH.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8 LC_TIME=de_CH.UTF-8 LC_NUMERIC=de_CH.UTF-8

# File store
# Default remains /opt/fitness-agent-data for backward compatibility.
mkdir -p /opt/fitness-agent-data
chmod 750 /opt/fitness-agent-data

# Pull and configure
mkdir -p /opt && cd /opt
git clone <your-private-repo-url> coacher
cd coacher
cp .env.example .env
$EDITOR .env  # see Configuration

uv sync
uv run alembic upgrade head
```

#### systemd unit

`/etc/systemd/system/coacher.service`:

```ini
[Unit]
Description=coacher API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/coacher
Environment="PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Optional: pin the encryption key instead of relying on the on-disk file.
# Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
# Environment="SETTINGS_ENCRYPTION_KEY=..."
ExecStart=/root/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now coacher
journalctl -u coacher -f
```

## Configuration

All knobs live in `.env` — see `.env.example` for a full template.

```bash
# Database
DATABASE_URL=postgresql+asyncpg://fitness:PASSWORD@10.1.10.10:5432/fitness

# Auth
JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">
JWT_EXPIRE_MINUTES=10080

# Local LLM (llama.cpp / Ollama, OpenAI-compatible)
LOCAL_LLM_BASE_URL=http://10.1.10.50:8080/v1
# LOCAL_LLM_MODEL is auto-detected at startup by probing /v1/models;
# only set this to override.
LOCAL_LLM_API_KEY=not-needed

# Cloud LLM (leave key blank to disable that provider)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-4-7
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.3
OPENAI_IMAGE_MODEL=gpt-image-1

# Per-coach defaults (users can override in Settings)
PROVIDER_FOR_CHAT=local
PROVIDER_FOR_PLANNING=anthropic
PROVIDER_FOR_NUTRITION=anthropic
PROVIDER_FOR_PROGRESS=openai
PROVIDER_FOR_MENTAL_HEALTH=anthropic

# File storage
FILE_STORAGE_DIR=/opt/fitness-agent-data
MAX_UPLOAD_BYTES=26214400   # 25 MB

# Optional: pin the encryption key (otherwise auto-provisioned on first run)
# SETTINGS_ENCRYPTION_KEY=...

TIMEZONE=Europe/Zurich
SCHEDULER_USER_ID=00000000-0000-0000-0000-000000000001
```

**Spending caps:** set hard monthly limits in the Anthropic and OpenAI
consoles before exposing the service. Recommended: $10/month while
developing, raise once usage stabilizes.

## Bootstrapping

Either register the first user via the web UI ("Create account") or seed
one from the CLI:

```bash
BOOTSTRAP_EMAIL=you@example.com BOOTSTRAP_PASSWORD='<strong-password>' \
  uv run python scripts/bootstrap_user.py
```

The web UI at `/` calls `POST /auth/login` and stores the JWT in browser
local storage. API clients pass the token as `Authorization: Bearer <token>`.

## API surface

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | email + password → JWT |
| POST | `/auth/register` | self-service signup |
| POST | `/auth/change-password` | bearer required |
| GET  | `/profile` | full profile read |
| PUT  | `/profile` | merge-update; `coach_prompts` empty-string clears |
| GET  | `/profile/llm` | provider overrides + `set/unset` key state |
| PUT  | `/profile/llm` | merge-update; empty-string clears |
| GET  | `/profile/coach-prompts/defaults` | built-in defaults for each coach |
| POST | `/chat` | `task_hint` defaults to `auto` (Boss picks) |
| GET  | `/chat/history` | rolling thread, oldest → newest |
| DELETE | `/chat/history` | wipe the rolling thread |
| GET  | `/files` | list user's files |
| POST | `/files` | multipart upload (≤ 25 MB) |
| GET  | `/files/{id}` | download |
| DELETE | `/files/{id}` | remove file + DB row |
| GET  | `/calendar/workouts/{plan_id}.ics` | per-plan workout calendar |
| GET  | `/calendar/meals/{plan_id}.ics` | per-plan meal calendar |
| GET  | `/calendar/upcoming.ics?days=30` | rolling combined feed |
| GET  | `/profile/export.zip` | GDPR Art. 15+20 export — data.json + files |
| DELETE | `/profile/account` | GDPR Art. 17 erasure (requires password + email confirm) |
| GET  | `/healthz` | liveness probe |

## Smoke testing

```bash
# Health
curl http://10.1.10.103:8000/healthz

# Auth
TOKEN=$(curl -s -X POST http://10.1.10.103:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"<strong-password>"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# Boss-routed chat
curl -X POST http://10.1.10.103:8000/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Build me a 45-min hangboard session for Tuesday at 7am"}'

# Forced coach: nutrition
curl -X POST http://10.1.10.103:8000/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"I just ate 200g chicken and 150g rice","task_hint":"nutrition_analysis"}'

# Verify writes landed
psql postgresql://fitness:PASSWORD@10.1.10.10:5432/fitness \
  -c "SELECT scheduled_date, workout_type, intensity, duration_min FROM workoutsession ORDER BY scheduled_date;"
```

## Operations

### Logs

```bash
journalctl -u coacher -f
journalctl -u coacher --since "1 hour ago"
```

### Database backups

In the Postgres LXC, `/etc/cron.daily/pg_backup`:

```bash
#!/bin/bash
sudo -u postgres pg_dump fitness | gzip > /var/backups/fitness_$(date +\%Y\%m\%d).sql.gz
find /var/backups/fitness_*.sql.gz -mtime +14 -delete
```

In addition, Proxmox backs up both LXCs nightly.

### Encryption key & file-store backups

Back up these alongside the DB — losing either breaks the agent:

| Path | What |
|---|---|
| `/opt/fitness-agent-data/.encryption.key` | Fernet key for DB-stored API keys |
| `/opt/fitness-agent-data/<shard>/<uuid>.<ext>` | uploads + generated images |

If you'd rather not have the key on disk, set `SETTINGS_ENCRYPTION_KEY`
in the systemd unit's `Environment=` and delete the file.

### Common issues

**`status_code: 529, overloaded_error`** — Anthropic API overloaded. The
chat endpoint retries 3× with exponential backoff. If persistent, switch
that coach's provider to `openai` in Settings, or in `.env`.

**`401 invalid x-api-key`** — your Anthropic / OpenAI key is wrong or
expired. Either rotate the value in `.env` (server-wide) or in Settings →
"LLM providers & API keys" (per-user, encrypted in DB).

**`Could not probe local model`** at startup — the local llama.cpp endpoint
is unreachable. The service still boots and falls back to the configured
default model name; cloud-routed coaches still work.

**Generated images aren't appearing in chat** — `OPENAI_API_KEY` missing or
out of credits. Image generation requires OpenAI; Anthropic has no image
endpoint.

**`concurrent operations are not permitted`** — a tool is sharing a
session. Every tool must wrap its DB work in
`async with ctx.deps.session_factory() as session:`.

**Stored DB API keys stop decrypting after a restart** — the encryption
key changed. Check `/opt/fitness-agent-data/.encryption.key` exists and is
unchanged, or that `SETTINGS_ENCRYPTION_KEY` env matches what was used
when the keys were stored.

## GDPR

The app is built to make data-subject rights easy to exercise. The full
privacy notice is in the WebUI's **About** tab; what's wired up:

| Right | How |
|---|---|
| Access + portability (Art. 15, 20) | `GET /profile/export.zip` — Settings → "Download my data". Returns `data.json` (every row you own, with bcrypt hashes and ciphertext stripped) plus `files/<id>/<filename>` for each upload or generated image. |
| Erasure (Art. 17) | `DELETE /profile/account` — Settings → "Delete my account". Hard-deletes DB rows + on-disk files. Requires the user's current password and a typed-confirm of the email. |
| Rectification (Art. 16) | `PUT /profile`, `PUT /profile/llm`, `PUT /profile` (`coach_prompts` field). |
| Restriction / objection (Art. 18, 21) | Switch any coach to `local` in Settings → "LLM providers & API keys". The Mental Health coach can run end-to-end on local LLM — no third-party processor. |
| Withdraw consent | Same effect as restriction or erasure; the moment you flip a coach to `local` (or delete the account), processing stops. |

**Recipients (data processors):** Anthropic, PBC and OpenAI, L.L.C. — but
*only* for turns whose coach you've routed to them. Both publish DPAs:

- [Anthropic DPA](https://www.anthropic.com/legal/dpa)
- [OpenAI DPA](https://openai.com/policies/data-processing-addendum)

Both process in the United States; Standard Contractual Clauses apply.
Local-LLM routing keeps everything in your homelab.

**Security at rest:** API keys encrypted with Fernet (AES-128-CBC + HMAC-SHA256);
passwords bcrypt-hashed. Encryption-key path is logged at startup; back it
up alongside your DB. Run over HTTPS in any non-loopback deployment.

**Privacy controls in the UI:**

- **Local-only mode** (Settings → "Data & Privacy"): forces every coach onto
  the local LLM, ignoring per-coach overrides and the image-attachment
  auto-bump to Anthropic. Image generation is disabled while it's on (since
  it requires OpenAI). Fastest path to "no third-party processing for
  any of my data".
- **Chat retention**: pick Forever / 30 / 90 / 180 / 365 days. A daily
  systemd-style APScheduler job at 03:15 hard-deletes `agentmessage` rows
  older than the cutoff for users who opted in. Idempotent.
- **Language**: UI strings ship in **English** and **Deutsch**, with a
  language toggle in the topbar (🌐). Your selection is persisted to your
  profile and prepended as a "respond in this language" directive to every
  coach turn. Long-form privacy and feature prose stays in English in both
  languages — translating legal text without review would be worse than
  not translating it.

**Multi-tenant note:** if you invite others onto your instance you become
a controller. Document the lawful basis (consent, captured at signup), tell
them you process their data, and respect their export/erasure requests
through the same endpoints.

## Roadmap

- [x] Schema, FastAPI skeleton, agent with structured tools
- [x] Local + cloud LLM routing per task
- [x] Sunday weekly plan generation (APScheduler)
- [x] Real JWT auth + user registration + change-password
- [x] Rolling chat thread, server-side history
- [x] Mental-health coach with safety guardrails
- [x] Boss router (one chat, four coaches)
- [x] File uploads + multimodal chat (images, PDFs, DOCX, text)
- [x] Image generation (gpt-image-1) with on-disk store
- [x] ICS calendar export (Europe/Zurich VTIMEZONE)
- [x] Per-user prompt overrides
- [x] Per-user provider overrides + encrypted API keys
- [ ] Open Food Facts / USDA FDC nutrition lookup tools
- [ ] wger exercise database integration
- [ ] Garmin Connect ingestion service
- [ ] Apple HealthKit upload endpoint (for the iOS app)
- [ ] Grocery list generation from meal plans
- [ ] Home Assistant conversation agent integration
- [ ] iOS app (separate repo, SwiftUI, generated client from OpenAPI spec)

## License

Private. Not for distribution.
