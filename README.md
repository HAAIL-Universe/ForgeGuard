# ForgeGuard

**AI-powered repository governance platform.**

ForgeGuard connects to your GitHub repositories, runs automated audit checks on every commit, generates project contracts via AI-guided questionnaires, orchestrates LLM-driven code builds, and provides deep-scan repository analysis — all from a single real-time dashboard.

> **Status:** Private beta · Not open source · All rights reserved

---

## Architecture

```
┌─────────────┐      ┌──────────────┐      ┌────────────┐
│  React SPA  │◄────►│  FastAPI     │◄────►│ PostgreSQL │
│  (Vite)     │  WS  │  Backend     │      │            │
│  :5174      │      │  :8000       │      │            │
└─────────────┘      └──────┬───────┘      └────────────┘
                            │
               ┌────────────┼────────────┐
               ▼            ▼            ▼
        ┌───────────┐ ┌──────────┐ ┌──────────┐
        │ GitHub    │ │ Anthropic│ │ OpenAI   │
        │ API +     │ │ Claude   │ │ GPT-4o   │
        │ Webhooks  │ │          │ │          │
        └───────────┘ └──────────┘ └──────────┘
```

| Layer | Tech | Purpose |
|-------|------|---------|
| Frontend | React 19, TypeScript, Vite 6, React Router 7 | Single-page dashboard with lazy-loaded pages |
| Backend | Python 3.12+, FastAPI 0.115, Uvicorn | REST API (~60 endpoints) + WebSocket server |
| Database | PostgreSQL 15+ (via asyncpg, 11 tables) | Users, repos, projects, builds, audits, scouts |
| Auth | GitHub OAuth2 + JWT (HS256, 24h expiry) | Stateless session tokens |
| LLM | Anthropic Claude / OpenAI GPT-4o (BYOK) | Contract generation, build orchestration, repo analysis |
| Webhooks | GitHub push events, HMAC-SHA256 | Real-time commit ingestion |
| Migrations | Alembic (async mode with asyncpg) | Versioned schema management with rollback |
| Config | pydantic-settings (BaseSettings + .env) | Type-safe, validated configuration |

---

## Features

### Core

- **GitHub OAuth login** — one-click sign in, no passwords
- **Repo connection** — browse, create, and connect any repo you have access to
- **Webhook auto-registration** — ForgeGuard sets up the GitHub webhook for you
- **Push event processing** — every commit triggers an audit run

### Audit Engine

- **9-check audit engine** (A1–A9) — scope compliance, boundary checks, diff analysis, dependency gates
- **Real-time dashboard** — WebSocket-powered live updates as audits complete
- **Commit timeline** — per-repo history of all audited commits
- **Audit detail view** — drill into individual check results (pass/fail/warn)
- **Health badges** — green/yellow/red/pending status per repo

### Project Intake & Contracts

- **AI-guided questionnaire** — multi-turn LLM chat builds project specification
- **Contract generation** — AI generates scope, architecture, and dependency contracts
- **Contract versioning** — numbered batch snapshots with full history
- **Contract push** — push approved contracts to the repo as governance files

### Build Orchestration

- **Plan-execute architecture** — LLM plans phases, then executes sequentially
- **Live build logs** — streaming WebSocket output as code is generated
- **Phase progress** — granular tracking of build phases with completion counts
- **Pause / resume / interject** — human-in-the-loop controls during builds
- **Cost tracking** — per-phase token usage and estimated USD cost
- **Spend caps** — per-user budget limits with configurable warning thresholds
- **Circuit breaker** — emergency stop with automatic cleanup
- **BYOK** — bring your own Anthropic/OpenAI API keys (per-user, dual-key pool)

### Scout (Repository Analysis)

- **Quick scan** — lightweight audit of repo structure and health
- **Deep scan** — comprehensive architecture mapping with LLM-powered analysis
- **Dossier generation** — AI-generated detailed repository report
- **Score history** — track repo quality over time with computed scores
- **Upgrade plans** — AI-recommended improvement roadmaps

### Infrastructure

- **WebSocket reliability** — heartbeat loop (30s), per-user connection limit (3), exponential backoff on client
- **Request ID tracing** — `X-Request-ID` header injection on every request
- **Rate limiting** — sliding-window protection on critical endpoints
- **API pagination** — `limit`/`offset` on all list endpoints
- **GitHub API caching** — TTLCache (5 min) on read-only GitHub endpoints
- **Error boundary** — React error boundary with retry UI
- **Lazy loading** — all 10 pages code-split via `React.lazy()`

### Developer Experience

- **One-command setup** — `boot.ps1` with `-SkipFrontend`, `-MigrateOnly`, `-TestOnly`, `-Check` flags
- **Docker Compose** — full local stack (Postgres + backend + frontend)
- **Pre-commit hooks** — ruff lint + format on every commit
- **929 backend + 66 frontend tests** — full coverage, 0 failures

---

## Prerequisites

| Tool | Version | Required |
|------|---------|----------|
| Python | 3.12+ | Yes |
| Node.js | 18+ | Yes (frontend) |
| PostgreSQL | 15+ | Yes (local, hosted, or via Docker Compose) |
| Git | 2.x | Yes |
| Docker | 24+ | Optional (for `docker compose up`) |
| ngrok | 3.x | Optional (for local webhook testing) |

---

## Quick Start

### Option A: boot.ps1 (recommended)

```powershell
pwsh -File boot.ps1
```

This will:
1. Verify prerequisites (Python 3.12+, Node 18+, psql)
2. Create a Python virtual environment and install dependencies
3. Validate `.env` configuration
4. Run database migrations
5. Install frontend dependencies
6. Start both backend (`:8000`) and frontend (`:5174`) servers

Other modes:

```powershell
pwsh -File boot.ps1 -SkipFrontend     # Backend only
pwsh -File boot.ps1 -MigrateOnly      # Run migrations then exit
pwsh -File boot.ps1 -TestOnly         # Run pytest then exit
pwsh -File boot.ps1 -Check            # Run ruff + mypy then exit
```

### Option B: Docker Compose

```powershell
docker compose up -d                  # Start Postgres + backend + frontend
docker compose exec backend alembic upgrade head   # Run migrations
```

### Option C: Manual Setup

#### 1. Clone

```powershell
git clone https://github.com/HAAIL-Universe/ForgeGuard.git
cd ForgeGuard
```

#### 2. Backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Optional: ruff, mypy, pre-commit
```

#### 3. Frontend

```powershell
cd web
npm install
cd ..
```

#### 4. Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/forgeguard
GITHUB_CLIENT_ID=your_oauth_client_id
GITHUB_CLIENT_SECRET=your_oauth_client_secret
GITHUB_WEBHOOK_SECRET=your_webhook_secret
JWT_SECRET=your_jwt_secret
FRONTEND_URL=http://localhost:5174
APP_URL=http://localhost:8000
```

<details>
<summary>All environment variables</summary>

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `GITHUB_CLIENT_ID` | Yes | — | From your GitHub OAuth App |
| `GITHUB_CLIENT_SECRET` | Yes | — | From your GitHub OAuth App |
| `GITHUB_WEBHOOK_SECRET` | Yes | — | Shared secret for webhook HMAC validation |
| `JWT_SECRET` | Yes | — | Secret for signing JWT session tokens |
| `FRONTEND_URL` | No | `http://localhost:5174` | Frontend origin for CORS |
| `APP_URL` | No | `http://localhost:8000` | Backend public URL |
| `DEBUG` | No | `true` | Enable debug mode |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `ANTHROPIC_API_KEY` | No | — | Server-level Anthropic API key |
| `OPENAI_API_KEY` | No | — | Server-level OpenAI API key |
| `LLM_PROVIDER` | No | auto | `anthropic`, `openai`, or auto-detect |
| `LLM_BUILDER_MODEL` | No | `claude-opus-4-6` | Model for build orchestration |
| `LLM_PLANNER_MODEL` | No | `claude-sonnet-4-5` | Model for build planning |
| `LLM_QUESTIONNAIRE_MODEL` | No | `claude-sonnet-4-5` | Model for questionnaire |
| `BUILD_MODE` | No | `plan_execute` | `plan_execute` or `conversation` |
| `BUILD_MAX_COST_USD` | No | `50.00` | Server-level build cost cap |
| `BUILD_COST_WARN_PCT` | No | `80` | Cost warning threshold (%) |
| `PAUSE_THRESHOLD` | No | `3` | Auto-pause after N consecutive errors |

</details>

#### 5. GitHub OAuth App

1. Go to **GitHub → Settings → Developer Settings → OAuth Apps → New OAuth App**
2. Set **Homepage URL** to `http://localhost:5174`
3. Set **Callback URL** to `http://localhost:5174/auth/callback`
4. Copy Client ID and Client Secret into `.env`

#### 6. Database

```powershell
# Option A — Alembic (recommended):
alembic upgrade head

# Option B — Raw SQL:
psql $DATABASE_URL -f db/migrations/001_initial_schema.sql
python _migrate.py   # runs all 19 migration files

# Option C — Docker Compose spins up Postgres for you (see above)
```

#### 7. Webhook Tunnel (local dev)

```powershell
ngrok http 8000
# Copy the https://xxx.ngrok-free.app URL into APP_URL in .env
```

#### 8. Run

```powershell
# Terminal 1 — Backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Frontend
cd web && npm run dev
```

Open **http://localhost:5174** and sign in with GitHub.

---

## API Endpoints

<details>
<summary>Full endpoint list (~60 endpoints across 9 routers)</summary>

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Health check |
| `GET` | `/health/version` | No | App version |

### Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/auth/github` | No | Start OAuth flow |
| `GET` | `/auth/github/callback` | No | Exchange code for JWT |
| `GET` | `/auth/me` | Bearer | Current user profile |
| `PUT` | `/auth/api-key` | Bearer | Set Anthropic API key |
| `DELETE` | `/auth/api-key` | Bearer | Remove API key |
| `PUT` | `/auth/api-key-2` | Bearer | Set second API key (pool) |
| `DELETE` | `/auth/api-key-2` | Bearer | Remove second key |
| `PUT` | `/auth/audit-toggle` | Bearer | Toggle LLM audit |
| `PUT` | `/auth/spend-cap` | Bearer | Set per-build spend cap |
| `DELETE` | `/auth/spend-cap` | Bearer | Remove spend cap |

### Repos

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/repos` | Bearer | List connected repos (paginated) |
| `GET` | `/repos/available` | Bearer | List available GitHub repos (paginated) |
| `GET` | `/repos/all` | Bearer | List all repos (paginated) |
| `POST` | `/repos/create` | Bearer | Create new GitHub repo |
| `POST` | `/repos/connect` | Bearer | Connect repo + register webhook |
| `DELETE` | `/repos/{id}/disconnect` | Bearer | Disconnect repo + remove webhook |
| `POST` | `/repos/{id}/sync` | Bearer | Sync repo metadata |
| `GET` | `/repos/{id}/audits` | Bearer | List audit runs |
| `GET` | `/repos/{id}/audits/{audit_id}` | Bearer | Get audit detail with checks |

### Projects

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/projects` | Bearer | Create project |
| `GET` | `/projects` | Bearer | List projects (paginated) |
| `GET` | `/projects/{id}` | Bearer | Get project detail |
| `DELETE` | `/projects/{id}` | Bearer | Delete project |
| `POST` | `/projects/{id}/questionnaire` | Bearer | Send questionnaire message |
| `GET` | `/projects/{id}/questionnaire/state` | Bearer | Get questionnaire state |
| `DELETE` | `/projects/{id}/questionnaire` | Bearer | Reset questionnaire |
| `POST` | `/projects/{id}/contracts/generate` | Bearer | Generate contracts via AI |
| `POST` | `/projects/{id}/contracts/cancel` | Bearer | Cancel generation |
| `GET` | `/projects/{id}/contracts` | Bearer | List contracts |
| `GET` | `/projects/{id}/contracts/history` | Bearer | Contract version history |
| `GET` | `/projects/{id}/contracts/history/{batch}` | Bearer | Get specific batch |
| `POST` | `/projects/{id}/contracts/push` | Bearer | Push contracts to repo |
| `GET` | `/projects/{id}/contracts/{type}` | Bearer | Get specific contract |
| `PUT` | `/projects/{id}/contracts/{type}` | Bearer | Update contract |
| `GET` | `/projects/{id}/certificate` | Bearer | Get project certificate |
| `GET` | `/projects/{id}/certificate/html` | Bearer | Certificate (HTML) |
| `GET` | `/projects/{id}/certificate/text` | Bearer | Certificate (text) |

### Builds

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/projects/{id}/build` | Bearer + Rate | Start build |
| `GET` | `/projects/{id}/builds` | Bearer | List builds |
| `DELETE` | `/projects/{id}/builds` | Bearer | Delete builds |
| `POST` | `/projects/{id}/build/cancel` | Bearer | Cancel build |
| `POST` | `/projects/{id}/build/force-cancel` | Bearer | Force cancel |
| `POST` | `/projects/{id}/build/resume` | Bearer | Resume paused build |
| `POST` | `/projects/{id}/build/interject` | Bearer | Send message during build |
| `GET` | `/projects/{id}/build/files` | Bearer | List generated files |
| `GET` | `/projects/{id}/build/files/{path}` | Bearer | Get file content |
| `GET` | `/projects/{id}/build/status` | Bearer | Build status |
| `GET` | `/projects/{id}/build/logs` | Bearer | Build logs |
| `GET` | `/projects/{id}/build/phases` | Bearer | Phase progress |
| `GET` | `/projects/{id}/build/summary` | Bearer | Build summary |
| `GET` | `/projects/{id}/build/instructions` | Bearer | Build instructions |
| `POST` | `/projects/{id}/build/circuit-break` | Bearer | Emergency stop |
| `GET` | `/projects/{id}/build/live-cost` | Bearer | Real-time cost |

### Scout

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/scout/{repo_id}/run` | Bearer | Start quick scan |
| `POST` | `/scout/{repo_id}/deep-scan` | Bearer | Start deep scan |
| `GET` | `/scout/history` | Bearer | All scan history |
| `GET` | `/scout/runs/{run_id}` | Bearer | Scan detail |
| `GET` | `/scout/runs/{run_id}/dossier` | Bearer | AI-generated dossier |
| `GET` | `/scout/{repo_id}/history` | Bearer | Per-repo scan history |
| `GET` | `/scout/{repo_id}/score-history` | Bearer | Score trend data |
| `POST` | `/scout/runs/{run_id}/upgrade-plan` | Bearer | Generate upgrade plan |
| `GET` | `/scout/runs/{run_id}/upgrade-plan` | Bearer | Get upgrade plan |

### Other

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/webhooks/github` | HMAC | Receive GitHub push events |
| `WS` | `/ws?token=JWT` | Query | Real-time updates (builds, audits, scouts) |
| `GET` | `/audit/run` | Bearer | Manual audit trigger |

</details>

---

## Database Schema

11 tables managed via Alembic (async mode, asyncpg) — no ORM, parameterised SQL only.

| Table | Purpose |
|-------|---------|
| `users` | GitHub-authenticated users + API keys + spend caps |
| `repos` | Connected repositories + webhook state |
| `audit_runs` | One per push event (commit, status, result) |
| `audit_checks` | Individual check results per audit (A1–A9) |
| `projects` | Project intake records + questionnaire state |
| `project_contracts` | Live governance contracts per project |
| `contract_snapshots` | Contract version history (numbered batches) |
| `builds` | Build orchestration runs + phase tracking |
| `build_logs` | Streaming builder output per build |
| `build_costs` | Token usage and estimated cost per phase |
| `scout_runs` | Quick/deep scan results + computed scores |

Migrations: `db/migrations/` (19 raw SQL files) + `db/alembic/` (versioned with rollback).

---

## Project Structure

```
ForgeGuard/
├── app/                          # Python backend
│   ├── main.py                   # FastAPI app + lifespan (heartbeat, pool)
│   ├── config.py                 # pydantic BaseSettings (env + .env)
│   ├── auth.py                   # JWT creation + verification
│   ├── webhooks.py               # HMAC-SHA256 signature validation
│   ├── ws_manager.py             # WebSocket manager (heartbeat, connection limits)
│   ├── middleware/                # RequestIDMiddleware (X-Request-ID)
│   ├── api/
│   │   ├── deps.py               # Auth dependency (HTTPBearer)
│   │   ├── rate_limit.py         # Sliding-window rate limiter
│   │   └── routers/              # 9 route modules (~60 endpoints)
│   ├── audit/
│   │   ├── engine.py             # Pluggable audit check engine (A1–A9)
│   │   └── runner.py             # Background audit runner
│   ├── clients/
│   │   ├── github_client.py      # GitHub API (cached, connection-pooled)
│   │   ├── llm_client.py         # Anthropic + OpenAI wrapper
│   │   ├── git_client.py         # Git CLI operations
│   │   └── agent_client.py       # Agent orchestration client
│   ├── repos/
│   │   ├── db.py                 # asyncpg connection pool
│   │   ├── user_repo.py          # User queries
│   │   ├── repo_repo.py          # Repo queries + health badges
│   │   ├── audit_repo.py         # Audit run/check queries
│   │   ├── project_repo.py       # Project + contract queries
│   │   ├── build_repo.py         # Build + log + cost queries
│   │   └── scout_repo.py         # Scout run queries
│   └── services/
│       ├── auth_service.py       # OAuth token exchange
│       ├── repo_service.py       # Repo connect/disconnect + health
│       ├── audit_service.py      # Push event processing + WS broadcast
│       ├── build/                # Build orchestration (decomposed)
│       │   ├── _state.py         # Shared state + in-memory tracking
│       │   ├── context.py        # Build context builder
│       │   ├── cost.py           # Cost tracking + spend caps
│       │   ├── planner.py        # LLM phase planner
│       │   └── verification.py   # Build output verification
│       ├── project/              # Project management (decomposed)
│       │   ├── questionnaire.py  # Multi-turn LLM questionnaire
│       │   └── contract_generator.py  # AI contract generation
│       ├── scout/                # Repository analysis (decomposed)
│       │   ├── quick_scan.py     # Lightweight audit scan
│       │   ├── deep_scan.py      # LLM-powered architecture analysis
│       │   ├── dossier_builder.py # AI-generated repo report
│       │   └── _utils.py         # Shared helpers
│       └── tool_executor.py      # Build tool execution engine
├── web/                          # React frontend
│   └── src/
│       ├── App.tsx               # Router + lazy loading + ErrorBoundary
│       ├── pages/                # 10 pages (lazy-loaded)
│       ├── components/           # 22 reusable components
│       ├── context/              # Auth + Toast providers
│       └── hooks/                # useWebSocket (exponential backoff)
├── db/
│   ├── migrations/               # 19 raw SQL migration files
│   └── alembic/                  # Alembic config + versioned migrations
├── tests/                        # 44 test files (929 backend tests)
├── Forge/                        # Governance contracts + evidence
├── forge_ide/                    # ForgeIDE integration layer
├── boot.ps1                      # One-click setup (4 modes)
├── docker-compose.yml            # Postgres + backend + frontend
├── Dockerfile                    # Backend container image
├── pyproject.toml                # ruff + mypy + pytest config
├── .pre-commit-config.yaml       # ruff lint + format hooks
├── requirements.txt              # Runtime dependencies
├── requirements-dev.txt          # Dev dependencies (ruff, mypy, pre-commit)
├── alembic.ini                   # Alembic configuration
└── .env                          # Environment config (not committed)
```

---

## Testing

```powershell
# Full backend suite (929 tests)
python -m pytest tests/ -q

# Frontend tests (66 tests)
cd web && npx vitest run

# Quick mode via boot.ps1
pwsh -File boot.ps1 -TestOnly

# Lint + type check
pwsh -File boot.ps1 -Check
# …or manually:
ruff check .
ruff format --check .
mypy app/ --ignore-missing-imports
```

---

## Security

- **No secrets in code** — all credentials via pydantic-settings + `.env`
- **JWT tokens** — HS256-signed, 24-hour expiry
- **Webhook HMAC** — SHA-256 signature verification on every payload
- **Rate limiting** — sliding-window protection on critical endpoints
- **Input validation** — Pydantic Field constraints (regex, min/max, ge)
- **Error containment** — global exception handler strips stack traces, domain exceptions
- **CORS** — explicit origin, method, and header allowlists
- **WebSocket auth** — JWT validation on connection, message size limits
- **Request tracing** — X-Request-ID on every request/response
- **No ORM** — parameterised SQL queries only (no injection surface)

---

## Deployment

### Docker Compose (local / staging)

```powershell
docker compose up -d
docker compose exec backend alembic upgrade head
```

### Manual (Render / Fly.io / any platform)

1. Set all required environment variables in your hosting provider
2. Set `APP_URL` to your public domain
3. Update your GitHub OAuth App callback URL
4. Run migrations: `alembic upgrade head`
5. Deploy backend: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
6. Deploy frontend: `cd web && npm run build` → serve `web/dist/`

---

## License

**Proprietary — All Rights Reserved.** See [LICENSE](LICENSE) for details.

This software is not open source. Unauthorised use, copying, modification, or distribution is strictly prohibited.

---

© 2026 HAAIL Universe. All rights reserved.
