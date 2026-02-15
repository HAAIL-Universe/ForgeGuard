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
- `LLM_QUESTIONNAIRE_MODEL` — model for questionnaire / audits / planning (default: `claude-sonnet-4-5`)
- `LLM_PLANNER_MODEL` — model for inter-phase planning agent (default: `claude-sonnet-4-5`)
- `PAUSE_THRESHOLD` — consecutive audit failures before pausing (default: `3`)
- `BUILD_PAUSE_TIMEOUT_MINUTES` — how long a paused build waits before auto-aborting (default: `30`)
- `PHASE_TIMEOUT_MINUTES` — max time per phase before pause (default: `10`)
- `LARGE_FILE_WARN_BYTES` — threshold for large file warnings (default: `1048576` = 1 MB)
- `GIT_PUSH_MAX_RETRIES` — retry attempts for git push failures (default: `3`)

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

Click **Start Build** to spawn an autonomous builder agent. Choose a **build target**:

- **New GitHub Repo** — creates a new repository on GitHub and pushes the generated code
- **Existing GitHub Repo** — clones an existing repo and builds on top of it
- **Local Path** — writes files to a directory on your machine

The builder:
- Works through phases (Phase 0 → Phase N) defined in your contracts
- Streams real-time progress via WebSocket
- Runs governance audits after each phase
- Automatically retries on audit failures (up to the pause threshold)
- **Pauses** after too many failures or phase timeout — you decide what to do next

### 5. Monitor Progress

The **Build Progress** page shows:
- Phase progress bar (grey=pending, blue=active, green=pass, red=fail, amber=paused)
- Streaming terminal-style logs with color-coded severity
- Per-phase audit results (A1-A9 checklist)
- Cancel button with confirmation

### 6. Pause & Resume

When the builder hits the pause threshold (default: 3 consecutive audit failures on a phase) or a phase times out, the build **pauses** and shows a modal with your options:

- **Retry Phase** — reset the failure counter and try again
- **Skip Phase** — move to the next phase (use with caution)
- **Edit & Retry** — provide guidance text that gets injected into the builder's conversation, then retry
- **Abort Build** — cancel the build entirely

If you don't respond within the pause timeout (default: 30 minutes), the build auto-aborts.

### 7. Interjection

While a build is running, you can send messages to the builder using the **interjection bar** at the bottom of the activity feed. Your message gets injected as a `[User interjection]` block into the builder's next conversation turn.

Use interjections to:
- Steer the builder's approach ("use SQLite instead of PostgreSQL")
- Add context ("the API resume` | POST | Resume a paused build |
| `/projects/{id}/build/interject` | POST | Send message to builder |
| `/projects/{id}/build/status` | GET | Current build status |
| `/projects/{id}/build/logs` | GET | Paginated build logs (supports ?search= and ?level=)")

### 8. Review Results

When the build completes, the **Build Complete** page shows:
- Build summary (phases completed, elapsed time, loopback count, total turns, files written, git commits, interjections received)
- Cost estimate (input/output tokens per phase, total USD)
- Deployment instructions tailored to your project's stack
- Links to the generated code and build logs

### What Happens on Audit Failure

After each phase, the builder runs an inline audit (using an LLM-based auditor). If the audit **fails**:

1. The builder receives the audit findings as feedback
2. It attempts to fix the issues and re-submit the phase
3. This repeats up to the pause threshold (default 3 times)
4. If all retries fail, the build **pauses** for your input (see above)
5. If the audit **passes**, the builder commits the phase and moves on

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
