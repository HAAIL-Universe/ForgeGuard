# ForgeGuard — User Instructions

ForgeGuard is a repository audit monitoring dashboard and autonomous build orchestrator. It connects to your GitHub repos, listens for push events via webhooks, runs automated audit checks on each commit, and can build entire projects autonomously using AI agents governed by the Forge framework.

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
- `ANTHROPIC_API_KEY` — required for AI-powered builds (get one at [console.anthropic.com](https://console.anthropic.com))
- `LLM_BUILDER_MODEL` — model for builds (default: `claude-opus-4-6`)
- `LLM_QUESTIONNAIRE_MODEL` — model for questionnaire (default: `claude-3-5-haiku-20241022`)

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

---

## Project Creation & Build Workflow

ForgeGuard can build entire projects autonomously using AI agents. Here's the workflow:

### 1. Create a Project

From the dashboard, click **Create Project** and enter a name and description.

### 2. Complete the Questionnaire

Navigate to your project and start the questionnaire. An AI assistant will guide you through key decisions:
- Product intent and goals
- Technology stack preferences
- Database schema design
- API endpoint planning
- UI requirements
- Deployment target

### 3. Generate Contracts

Once the questionnaire is complete, click **Generate Contracts** to produce Forge governance files (blueprint, manifesto, stack, schema, physics, etc.). You can review and edit these before building.

### 4. Start a Build

Click **Start Build** to spawn an autonomous builder agent. The builder:
- Works through phases (Phase 0 → Phase N) defined in your contracts
- Streams real-time progress via WebSocket
- Runs governance audits after each phase
- Automatically retries on audit failures (up to 3 times)
- Stops with `RISK_EXCEEDS_SCOPE` after 3 consecutive failures

### 5. Monitor Progress

The **Build Progress** page shows:
- Phase progress bar (grey=pending, blue=active, green=pass, red=fail)
- Streaming terminal-style logs with color-coded severity
- Per-phase audit results (A1-A9 checklist)
- Cancel button with confirmation

### 6. Review Results

When the build completes, the **Build Complete** page shows:
- Build summary (phases completed, elapsed time, loopback count)
- Cost estimate (input/output tokens per phase, total USD)
- Deployment instructions tailored to your project's stack
- Links to the generated code and build logs

### Rate Limits

Build endpoints are rate-limited to prevent abuse:
- **5 builds per user per hour** — starting builds
- Concurrent builds per project are blocked (one at a time)

### API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/projects` | GET | List your projects |
| `/projects` | POST | Create a new project |
| `/projects/{id}` | GET | Project detail |
| `/projects/{id}/build` | POST | Start a build |
| `/projects/{id}/build/cancel` | POST | Cancel active build |
| `/projects/{id}/build/status` | GET | Current build status |
| `/projects/{id}/build/logs` | GET | Paginated build logs |
| `/projects/{id}/build/summary` | GET | Build summary with cost breakdown |
| `/projects/{id}/build/instructions` | GET | Deployment instructions |
