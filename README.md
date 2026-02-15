# ForgeGuard

**Automated repository audit monitoring dashboard.**

ForgeGuard connects to your GitHub repositories, listens for push events via webhooks, and runs automated audit checks on every commit — surfacing violations before they ship.

> **Status:** Private beta · Not open source · All rights reserved

---

## Architecture

```
┌─────────────┐      ┌──────────────┐      ┌────────────┐
│  React SPA  │◄────►│  FastAPI     │◄────►│ PostgreSQL │
│  (Vite)     │  WS  │  Backend     │      │  (Neon)    │
│  :5173      │      │  :8000       │      │            │
└─────────────┘      └──────┬───────┘      └────────────┘
                            │
                    ┌───────▼───────┐
                    │  GitHub API   │
                    │  + Webhooks   │
                    └───────────────┘
```

| Layer | Tech | Purpose |
|-------|------|---------|
| Frontend | React 19, TypeScript, Vite 6, React Router 7 | Single-page dashboard |
| Backend | Python 3.12+, FastAPI 0.115, Uvicorn | REST API + WebSocket server |
| Database | PostgreSQL 15+ (via asyncpg) | Users, repos, audit runs, checks |
| Auth | GitHub OAuth2 + JWT (HS256, 24h expiry) | Stateless session tokens |
| Webhooks | GitHub push events, HMAC-SHA256 verification | Real-time commit ingestion |
| Audit | Pluggable check engine (A1–A9 checks) | Automated code review |

---

## Features

- **GitHub OAuth login** — one-click sign in, no passwords
- **Repo connection** — browse and connect any repo you have access to
- **Webhook auto-registration** — ForgeGuard sets up the GitHub webhook for you
- **Push event processing** — every commit triggers an audit run
- **9-check audit engine** — scope compliance, boundary checks, diff analysis, dependency gates
- **Real-time dashboard** — WebSocket-powered live updates as audits complete
- **Commit timeline** — per-repo history of all audited commits
- **Audit detail view** — drill into individual check results (pass/fail/warn)
- **Health badges** — green/yellow/red/pending status per repo
- **Rate limiting** — sliding-window protection on webhook endpoint (30 req/60s per IP)
- **Input validation** — Pydantic field constraints on all API inputs
- **Error containment** — global exception handler, no stack trace leaks
- **CORS hardening** — explicit method and header allowlists

---

## Prerequisites

| Tool | Version | Required |
|------|---------|----------|
| Python | 3.12+ | Yes |
| Node.js | 18+ | Yes (frontend) |
| PostgreSQL | 15+ | Yes (local or hosted) |
| Git | 2.x | Yes |
| ngrok | 3.x | For local webhook testing |

---

## Quick Start

```powershell
# One command does everything:
pwsh -File boot.ps1
```

This will:
1. Verify prerequisites (Python 3.12+, Node 18+, psql)
2. Create a Python virtual environment
3. Install backend dependencies
4. Validate `.env` configuration
5. Run database migrations
6. Install frontend dependencies
7. Start both backend (`:8000`) and frontend (`:5173`) servers

---

## Manual Setup

### 1. Clone

```powershell
git clone https://github.com/HAAIL-Universe/ForgeGuard.git
cd ForgeGuard
```

### 2. Backend

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate         # Linux/macOS
pip install -r requirements.txt
```

### 3. Frontend

```powershell
cd web
npm install
cd ..
```

### 4. Environment Variables

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/forgeguard
GITHUB_CLIENT_ID=your_oauth_client_id
GITHUB_CLIENT_SECRET=your_oauth_client_secret
GITHUB_WEBHOOK_SECRET=your_webhook_secret
JWT_SECRET=your_jwt_secret
FRONTEND_URL=http://localhost:5173
APP_URL=http://localhost:8000
```

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `GITHUB_CLIENT_ID` | Yes | From your GitHub OAuth App |
| `GITHUB_CLIENT_SECRET` | Yes | From your GitHub OAuth App |
| `GITHUB_WEBHOOK_SECRET` | Yes | Shared secret for webhook HMAC validation |
| `JWT_SECRET` | Yes | Secret for signing JWT session tokens |
| `FRONTEND_URL` | No | Defaults to `http://localhost:5173` |
| `APP_URL` | No | Defaults to `http://localhost:8000` |

### 5. GitHub OAuth App

1. Go to **GitHub → Settings → Developer Settings → OAuth Apps → New OAuth App**
2. Set **Homepage URL** to `http://localhost:5173`
3. Set **Callback URL** to `http://localhost:5173/auth/callback`
4. Copy Client ID and Client Secret into `.env`

### 6. Database

```powershell
# If using a local PostgreSQL:
createdb forgeguard
psql postgresql://user:pass@localhost:5432/forgeguard -f db/migrations/001_initial_schema.sql

# If using Neon/Supabase, paste the connection string into DATABASE_URL
# Then run the migration script:
python _migrate.py
```

### 7. Webhook Tunnel (local dev)

```powershell
ngrok http 8000
# Copy the https://xxx.ngrok-free.app URL into APP_URL in .env
```

### 8. Run

```powershell
# Terminal 1 — Backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Frontend
cd web && npm run dev
```

Open **http://localhost:5173** and sign in with GitHub.

---

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Health check |
| `GET` | `/auth/github` | No | Start OAuth flow → returns `redirect_url` |
| `GET` | `/auth/github/callback` | No | Exchange code for JWT token |
| `GET` | `/auth/me` | Bearer | Current user profile |
| `GET` | `/repos` | Bearer | List connected repos (with health status) |
| `POST` | `/repos/connect` | Bearer | Connect a repo + register webhook |
| `DELETE` | `/repos/{id}` | Bearer | Disconnect a repo + remove webhook |
| `GET` | `/repos/{id}/audits` | Bearer | List audit runs for a repo |
| `GET` | `/repos/{id}/audits/{audit_id}` | Bearer | Get audit detail with checks |
| `POST` | `/webhooks/github` | HMAC | Receive GitHub push events |
| `WS` | `/ws?token=JWT` | Query | Real-time audit updates |

---

## Database Schema

Four tables managed by raw SQL (no ORM):

- **`users`** — GitHub-authenticated users (id, github_id, login, avatar, access_token)
- **`repos`** — Connected repositories (github_repo_id, full_name, webhook_id, webhook_active)
- **`audit_runs`** — One per push event (commit_sha, status, overall_result, timestamps)
- **`audit_checks`** — Individual check results per audit (check_code, result, detail)

Migration: [`db/migrations/001_initial_schema.sql`](db/migrations/001_initial_schema.sql)

---

## Project Structure

```
ForgeGuard/
├── app/                        # Python backend
│   ├── main.py                 # FastAPI app factory + lifespan
│   ├── config.py               # Env var validation + settings
│   ├── auth.py                 # JWT creation + verification
│   ├── webhooks.py             # HMAC-SHA256 signature validation
│   ├── ws_manager.py           # WebSocket connection manager
│   ├── api/
│   │   ├── deps.py             # Auth dependency injection
│   │   ├── rate_limit.py       # Sliding-window rate limiter
│   │   └── routers/            # Route handlers
│   │       ├── auth.py         # OAuth + JWT endpoints
│   │       ├── health.py       # Health check
│   │       ├── repos.py        # Repo CRUD + validation
│   │       ├── webhooks.py     # GitHub webhook receiver
│   │       └── ws.py           # WebSocket endpoint
│   ├── audit/
│   │   └── engine.py           # Audit check engine (A1–A9)
│   ├── clients/
│   │   └── github_client.py    # GitHub API wrapper
│   ├── repos/
│   │   ├── db.py               # asyncpg connection pool
│   │   ├── user_repo.py        # User queries
│   │   ├── repo_repo.py        # Repo queries + health
│   │   └── audit_repo.py       # Audit run/check queries
│   └── services/
│       ├── auth_service.py     # OAuth token exchange
│       ├── repo_service.py     # Repo connect/disconnect + health
│       └── audit_service.py    # Push event processing + WS broadcast
├── web/                        # React frontend
│   ├── src/
│   │   ├── App.tsx             # Router + protected routes
│   │   ├── main.tsx            # Entry point
│   │   ├── context/            # Auth + Toast providers
│   │   ├── hooks/              # useWebSocket
│   │   ├── pages/              # Dashboard, Timeline, AuditDetail, Login
│   │   └── components/         # AppShell, RepoCard, Skeleton, etc.
│   ├── vite.config.ts          # Dev server + API proxy
│   └── package.json
├── db/migrations/              # SQL migrations
├── tests/                      # 70 backend tests (pytest)
├── boot.ps1                    # One-click setup + run
├── requirements.txt            # Python dependencies
└── .env                        # Environment config (not committed)
```

---

## Testing

```powershell
# Backend (70 tests)
python -m pytest tests/ -v

# Frontend (15 tests)
cd web && npx vitest run

# Static analysis
python -m compileall app/ tests/ -q
```

All 85 tests pass on every commit — enforced by the Forge governance framework.

---

## Security

- **No secrets in code** — all credentials via environment variables
- **JWT tokens** — HS256-signed, 24-hour expiry
- **Webhook HMAC** — SHA-256 signature verification on every payload
- **Rate limiting** — 30 requests per 60 seconds per IP on webhook endpoint
- **Input validation** — Pydantic Field constraints (regex, min/max, ge)
- **Error containment** — global exception handler strips stack traces
- **CORS** — explicit origin, method, and header allowlists
- **No ORM** — parameterised SQL queries only (no injection surface)

---

## Deployment

Designed for deployment on **Render** (or any platform supporting Python + Node):

1. Set all environment variables in your hosting provider
2. Set `APP_URL` to your public domain
3. Update your GitHub OAuth App callback URL to match
4. Run the database migration against your production PostgreSQL
5. Deploy backend: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
6. Deploy frontend: `cd web && npm run build` → serve `web/dist/`

---

## License

**Proprietary — All Rights Reserved.** See [LICENSE](LICENSE) for details.

This software is not open source. Unauthorised use, copying, modification, or distribution is strictly prohibited.

---

© 2026 HAAIL Universe. All rights reserved.
