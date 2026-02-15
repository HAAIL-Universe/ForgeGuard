# ForgeGuard — User Instructions

ForgeGuard is a repository audit monitoring dashboard. It connects to your GitHub repos, listens for push events via webhooks, and runs automated audit checks on each commit.

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Backend runtime |
| Node.js | 18+ | Frontend build |
| PostgreSQL | 15+ | Database |
| Git | 2.x | Version control |

You also need a **GitHub OAuth App** (for login) and a **webhook secret** (for repo monitoring).

---

## Install

### Quick Start (one command)

```powershell
pwsh -File boot.ps1
```

This creates the venv, installs all deps, validates `.env`, runs DB migrations, and starts both servers.

### Manual Install

```powershell
# Backend
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt

# Frontend
cd web && npm install && cd ..

# Database
psql $DATABASE_URL -f db/migrations/001_initial_schema.sql
```

---

## Credential / API Setup

### GitHub OAuth App

1. Go to **GitHub > Settings > Developer Settings > OAuth Apps > New OAuth App**
2. Fill in:
   - **Application name:** ForgeGuard
   - **Homepage URL:** `http://localhost:5173`
   - **Authorization callback URL:** `http://localhost:5173/auth/callback`
3. Copy the **Client ID** and **Client Secret** into your `.env`.

### Webhook Secret

Generate a random string (e.g. `openssl rand -hex 32`) and use it as `GITHUB_WEBHOOK_SECRET` in `.env`. ForgeGuard will register this secret when connecting repos.

---

## Configure `.env`

Create a `.env` file in the project root (or copy `.env.example`):

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/forgeguard
GITHUB_CLIENT_ID=your_client_id
GITHUB_CLIENT_SECRET=your_client_secret
GITHUB_WEBHOOK_SECRET=your_webhook_secret
JWT_SECRET=your_jwt_secret
FRONTEND_URL=http://localhost:5173
APP_URL=http://localhost:8000
```

**Required** (app will not start without these):
- `DATABASE_URL` — PostgreSQL connection string
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` — GitHub OAuth app credentials
- `GITHUB_WEBHOOK_SECRET` — shared secret for webhook signature validation
- `JWT_SECRET` — secret for signing session tokens

**Optional** (defaults shown):
- `FRONTEND_URL` — `http://localhost:5173`
- `APP_URL` — `http://localhost:8000`

---

## Run

```powershell
# Option 1: Quick start (installs + runs)
pwsh -File boot.ps1

# Option 2: Backend only
pwsh -File boot.ps1 -SkipFrontend

# Option 3: Manual
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # Terminal 1
cd web && npm run dev                                        # Terminal 2
```

Open `http://localhost:5173` and sign in with GitHub.

---

## Stop

Press `Ctrl+C` in the terminal running the backend. If the frontend was started via `boot.ps1`, it runs as a background job and will stop when the PowerShell session ends.

---

## Key Settings Explained

| Setting | Purpose |
|---------|---------|
| `DATABASE_URL` | PostgreSQL connection string with credentials |
| `GITHUB_CLIENT_ID` | Identifies your OAuth app to GitHub |
| `GITHUB_CLIENT_SECRET` | Authenticates your OAuth app to GitHub |
| `GITHUB_WEBHOOK_SECRET` | Validates incoming webhook payloads are from GitHub |
| `JWT_SECRET` | Signs session tokens — keep this secret and random |
| `FRONTEND_URL` | Used for CORS and OAuth redirect — must match your frontend URL |
| `APP_URL` | Backend URL for generating webhook callback URLs |

---

## Troubleshooting

### App refuses to start: "missing required environment variables"
Your `.env` file is missing one or more required variables. Check the console output for which ones.

### Database connection errors
1. Ensure PostgreSQL is running: `pg_isready`
2. Verify `DATABASE_URL` format: `postgresql://user:pass@host:port/dbname`
3. Run migrations: `psql $DATABASE_URL -f db/migrations/001_initial_schema.sql`

### OAuth login fails
1. Verify `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` match your GitHub OAuth app
2. Ensure the callback URL in GitHub settings is exactly `http://localhost:5173/auth/callback`

### Webhooks not arriving
1. Your app must be publicly accessible for GitHub to reach it (use [ngrok](https://ngrok.com) for local dev)
2. Verify `GITHUB_WEBHOOK_SECRET` matches the secret in your GitHub webhook configuration

### WebSocket disconnects
1. Check that your JWT token hasn't expired (24h lifetime)
2. The app auto-reconnects after 3 seconds — check browser console for errors

### Tests failing
```powershell
# Run backend tests
python -m pytest tests/ -v

# Run frontend tests
cd web && npx vitest run
```
