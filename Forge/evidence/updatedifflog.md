# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T01:09:26+00:00
- Branch: master
- HEAD: 210a82cd02a26464539c7bbff5f45c8918f63802
- BASE_HEAD: 6a6fb120d3a51a5b1c91fa7f92dc316b95a8c7ec
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- Phase 6 Integration Test: validate full audit pipeline with a minor code change
- Added GET /health/version endpoint returning { "version": "0.1.0", "phase": "6" }
- Added VERSION constant ("0.1.0") to app/config.py
- Added test_health_version_returns_version test to tests/test_health.py
- Frontend: version string "v0.1.0" shown in AppShell sidebar footer
- Updated physics.yaml with /health/version endpoint spec (physics-first gate)

## Verification
- Static: PASS -- compileall clean, no syntax errors in app/ or tests/
- Runtime: PASS -- GET /health/version returns 200 with correct payload
- Behavior: PASS -- 71 backend tests pass (pytest), 15 frontend tests pass (vitest), no regressions
- Contract: PASS -- physics.yaml updated with /health/version before implementation, boundary compliance intact

## Notes (optional)
- None -- clean integration test phase, no blockers

## Next Steps
- None -- Phase 6 is the final integration test phase

## Files Changed (staged)
- .gitignore
- Forge/Contracts/physics.yaml
- Forge/evidence/audit_ledger.md
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md
- Forge/scripts/watch_audit.ps1
- app/api/routers/health.py
- app/config.py
- tests/test_health.py
- web/src/components/AppShell.tsx

## git status -sb
    ## master...origin/master
    M  Forge/Contracts/physics.yaml
    M  Forge/evidence/test_runs.md
    M  Forge/evidence/test_runs_latest.md
    M  Forge/evidence/updatedifflog.md
    M  app/api/routers/health.py
    M  app/config.py
    M  tests/test_health.py
    M  web/src/components/AppShell.tsx

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index 828c9b1..beb6fc8 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -17,6 +17,14 @@ paths:
           response:
             status: "ok"
     
    +  /health/version:
    +    get:
    +      summary: "Return application version and current phase"
    +      auth: none
    +      response:
    +        version: string
    +        phase: string
    +
       # -- Auth ---------------------------------------------------------
     
       /auth/github:
    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    index e6b58b4..d521216 100644
    --- a/Forge/evidence/test_runs.md
    +++ b/Forge/evidence/test_runs.md
    @@ -445,3 +445,34 @@ git unavailable
      7 files changed, 488 insertions(+), 79 deletions(-)
     ```
     
    +## Test Run 2026-02-15T01:08:47Z
    +- Status: PASS
    +- Start: 2026-02-15T01:08:47Z
    +- End: 2026-02-15T01:08:49Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: 210a82cd02a26464539c7bbff5f45c8918f63802
    +- import_sanity exit: 0
    +- pytest exit: 0
    +- compileall exit: 0
    +- git status -sb:
    +```
    +## master...origin/master
    + M Forge/Contracts/physics.yaml
    + M Forge/evidence/updatedifflog.md
    + M app/api/routers/health.py
    + M app/config.py
    + M tests/test_health.py
    + M web/src/components/AppShell.tsx
    +```
    +- git diff --stat:
    +```
    + Forge/Contracts/physics.yaml    |    8 +
    + Forge/evidence/updatedifflog.md | 1137 +--------------------------------------
    + app/api/routers/health.py       |    8 +
    + app/config.py                   |    2 +
    + tests/test_health.py            |    9 +
    + web/src/components/AppShell.tsx |   13 +
    + 6 files changed, 65 insertions(+), 1112 deletions(-)
    +```
    +
    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    index c245982..971f15d 100644
    --- a/Forge/evidence/test_runs_latest.md
    +++ b/Forge/evidence/test_runs_latest.md
    @@ -1,34 +1,30 @@
    -´╗┐Status: PASS
    -Start: 2026-02-14T23:56:12Z
    -End: 2026-02-14T23:56:13Z
    +Status: PASS
    +Start: 2026-02-15T01:08:47Z
    +End: 2026-02-15T01:08:49Z
     Branch: master
    -HEAD: e5f7dcd282338470cfdab5fa2dab9d27e544fac5
    +HEAD: 210a82cd02a26464539c7bbff5f45c8918f63802
     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +import_sanity exit: 0
    +pytest exit: 0
     compileall exit: 0
     git status -sb:
     ```
    -## master
    - M Forge/scripts/watch_audit.ps1
    - M USER_INSTRUCTIONS.md
    - M app/api/routers/repos.py
    - M app/api/routers/webhooks.py
    +## master...origin/master
    + M Forge/Contracts/physics.yaml
    + M Forge/evidence/updatedifflog.md
    + M app/api/routers/health.py
      M app/config.py
    - M app/main.py
    - M boot.ps1
    -?? app/api/rate_limit.py
    -?? tests/test_config.py
    -?? tests/test_hardening.py
    -?? tests/test_rate_limit.py
    + M tests/test_health.py
    + M web/src/components/AppShell.tsx
     ```
     git diff --stat:
     ```
    - Forge/scripts/watch_audit.ps1 |  99 ++++++++++++++++++++
    - USER_INSTRUCTIONS.md          | 142 +++++++++++++++++++++++++---
    - app/api/routers/repos.py      |  25 ++++-
    - app/api/routers/webhooks.py   |  22 ++++-
    - app/config.py                 |  49 ++++++++--
    - app/main.py                   |  21 ++++-
    - boot.ps1                      | 209 +++++++++++++++++++++++++++++++-----------
    - 7 files changed, 488 insertions(+), 79 deletions(-)
    + Forge/Contracts/physics.yaml    |    8 +
    + Forge/evidence/updatedifflog.md | 1137 +--------------------------------------
    + app/api/routers/health.py       |    8 +
    + app/config.py                   |    2 +
    + tests/test_health.py            |    9 +
    + web/src/components/AppShell.tsx |   13 +
    + 6 files changed, 65 insertions(+), 1112 deletions(-)
     ```
     
    diff --git a/Forge/evidence/updatedifflog.md b/Forge/evidence/updatedifflog.md
    index 5cd1eff..b9cf243 100644
    --- a/Forge/evidence/updatedifflog.md
    +++ b/Forge/evidence/updatedifflog.md
    @@ -1,1134 +1,47 @@
    -´╗┐# Diff Log (overwrite each cycle)
    +# Diff Log (overwrite each cycle)
     
     ## Cycle Metadata
    -- Timestamp: 2026-02-14T23:56:52+00:00
    +- Timestamp: 2026-02-15T01:06:47+00:00
     - Branch: master
    -- HEAD: e5f7dcd282338470cfdab5fa2dab9d27e544fac5
    -- BASE_HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
    +- HEAD: 210a82cd02a26464539c7bbff5f45c8918f63802
    +- BASE_HEAD: 6a6fb120d3a51a5b1c91fa7f92dc316b95a8c7ec
     - Diff basis: staged
     
     ## Cycle Status
    -- Status: COMPLETE
    +- Status: IN_PROCESS
     
     ## Summary
    -- Verification Evidence: Static analysis clean, Runtime endpoints tested, Behavior assertions pass, Contract boundaries enforced
    -- Phase 5 Ship Gate: env validation, rate limiting, input validation, error handling, boot.ps1, user instructions
    -- Config: fail-fast startup validation for required env vars (DATABASE_URL, JWT_SECRET, etc)
    -- Rate limiting: sliding-window limiter on webhook endpoint (30 req/60s per IP)
    -- Input validation: Pydantic Field constraints on ConnectRepoRequest (full_name regex, github_repo_id ge=1, default_branch length)
    -- Error handling: global exception handler prevents stack trace leaks, logging on all catch blocks
    -- CORS hardened: explicit method and header allowlists
    -- boot.ps1: full one-click setup with prereq checks, venv, npm, migration, server start
    -- USER_INSTRUCTIONS.md: prerequisites, setup, env vars, usage, troubleshooting
    -- Tests: 70 backend (14 new for rate limit, config, hardening), 15 frontend
    +- Phase 6 Integration Test: validate full pipeline with a minor code change
    +- Add GET /health/version endpoint returning version and phase info
    +- Add VERSION constant to app/config.py
    +- Add test for the new endpoint in tests/test_health.py
    +- Show version string in AppShell sidebar footer
    +- All existing tests must continue to pass
     
     ## Files Changed (staged)
    -- Forge/evidence/test_runs.md
    -- Forge/evidence/test_runs_latest.md
    -- Forge/evidence/updatedifflog.md
    -- USER_INSTRUCTIONS.md
    -- app/api/rate_limit.py
    -- app/api/routers/repos.py
    -- app/api/routers/webhooks.py
    +- Forge/Contracts/physics.yaml
     - app/config.py
    -- app/main.py
    -- boot.ps1
    -- tests/test_config.py
    -- tests/test_hardening.py
    -- tests/test_rate_limit.py
    +- app/api/routers/health.py
    +- tests/test_health.py
    +- web/src/components/AppShell.tsx
     
     ## git status -sb
    -    ## master
    -    M  Forge/evidence/test_runs.md
    -    M  Forge/evidence/test_runs_latest.md
    -    M  USER_INSTRUCTIONS.md
    -    A  app/api/rate_limit.py
    -    M  app/api/routers/repos.py
    -    M  app/api/routers/webhooks.py
    -    M  app/config.py
    -    M  app/main.py
    -    M  boot.ps1
    -    A  tests/test_config.py
    -    A  tests/test_hardening.py
    -    A  tests/test_rate_limit.py
    +    ## master...origin/master
     
     ## Minimal Diff Hunks
    -    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    -    index 6cb3261..e6b58b4 100644
    -    --- a/Forge/evidence/test_runs.md
    -    +++ b/Forge/evidence/test_runs.md
    -    @@ -410,3 +410,38 @@ git unavailable
    -     git unavailable
    -     ```
    -     
    -    +## Test Run 2026-02-14T23:56:12Z
    -    +- Status: PASS
    -    +- Start: 2026-02-14T23:56:12Z
    -    +- End: 2026-02-14T23:56:13Z
    -    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    -    +- Branch: master
    -    +- HEAD: e5f7dcd282338470cfdab5fa2dab9d27e544fac5
    -    +- compileall exit: 0
    -    +- git status -sb:
    -    +```
    -    +## master
    -    + M Forge/scripts/watch_audit.ps1
    -    + M USER_INSTRUCTIONS.md
    -    + M app/api/routers/repos.py
    -    + M app/api/routers/webhooks.py
    -    + M app/config.py
    -    + M app/main.py
    -    + M boot.ps1
    -    +?? app/api/rate_limit.py
    -    +?? tests/test_config.py
    -    +?? tests/test_hardening.py
    -    +?? tests/test_rate_limit.py
    -    +```
    -    +- git diff --stat:
    -    +```
    -    + Forge/scripts/watch_audit.ps1 |  99 ++++++++++++++++++++
    -    + USER_INSTRUCTIONS.md          | 142 +++++++++++++++++++++++++---
    -    + app/api/routers/repos.py      |  25 ++++-
    -    + app/api/routers/webhooks.py   |  22 ++++-
    -    + app/config.py                 |  49 ++++++++--
    -    + app/main.py                   |  21 ++++-
    -    + boot.ps1                      | 209 +++++++++++++++++++++++++++++++-----------
    -    + 7 files changed, 488 insertions(+), 79 deletions(-)
    -    +```
    -    +
    -    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    -    index a856761..c245982 100644
    -    --- a/Forge/evidence/test_runs_latest.md
    -    +++ b/Forge/evidence/test_runs_latest.md
    -    @@ -1,17 +1,34 @@
    -     ┬┤ÔòùÔöÉStatus: PASS
    -    -Start: 2026-02-14T23:40:07Z
    -    -End: 2026-02-14T23:40:08Z
    -    -Branch: git unavailable
    -    -HEAD: git unavailable
    -    +Start: 2026-02-14T23:56:12Z
    -    +End: 2026-02-14T23:56:13Z
    -    +Branch: master
    -    +HEAD: e5f7dcd282338470cfdab5fa2dab9d27e544fac5
    -     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    -     compileall exit: 0
    -    -import_sanity exit: 0
    -     git status -sb:
    -     ```
    -    -git unavailable
    -    +## master
    -    + M Forge/scripts/watch_audit.ps1
    -    + M USER_INSTRUCTIONS.md
    -    + M app/api/routers/repos.py
    -    + M app/api/routers/webhooks.py
    -    + M app/config.py
    -    + M app/main.py
    -    + M boot.ps1
    -    +?? app/api/rate_limit.py
    -    +?? tests/test_config.py
    -    +?? tests/test_hardening.py
    -    +?? tests/test_rate_limit.py
    -     ```
    -     git diff --stat:
    -     ```
    -    -git unavailable
    -    + Forge/scripts/watch_audit.ps1 |  99 ++++++++++++++++++++
    -    + USER_INSTRUCTIONS.md          | 142 +++++++++++++++++++++++++---
    -    + app/api/routers/repos.py      |  25 ++++-
    -    + app/api/routers/webhooks.py   |  22 ++++-
    -    + app/config.py                 |  49 ++++++++--
    -    + app/main.py                   |  21 ++++-
    -    + boot.ps1                      | 209 +++++++++++++++++++++++++++++++-----------
    -    + 7 files changed, 488 insertions(+), 79 deletions(-)
    -     ```
    -     
    -    diff --git a/USER_INSTRUCTIONS.md b/USER_INSTRUCTIONS.md
    -    index f3bebc2..bdc641e 100644
    -    --- a/USER_INSTRUCTIONS.md
    -    +++ b/USER_INSTRUCTIONS.md
    -    @@ -1,38 +1,158 @@
    -    -# USER_INSTRUCTIONS.md
    -    +# ForgeGuard ├ö├ç├Â User Instructions
    -     
    -    -> Setup and usage guide for ForgeGuard.
    -    -> This file will be fully populated in the final build phase.
    -    +ForgeGuard is a repository audit monitoring dashboard. It connects to your GitHub repos, listens for push events via webhooks, and runs automated audit checks on each commit.
    -     
    -     ---
    -     
    -     ## Prerequisites
    -     
    -    -_To be completed._
    -    +| Tool | Version | Purpose |
    -    +|------|---------|---------|
    -    +| Python | 3.12+ | Backend runtime |
    -    +| Node.js | 18+ | Frontend build |
    -    +| PostgreSQL | 15+ | Database |
    -    +| Git | 2.x | Version control |
    -    +
    -    +You also need a **GitHub OAuth App** (for login) and a **webhook secret** (for repo monitoring).
    -    +
    -    +---
    -     
    -     ## Install
    -     
    -    -_To be completed._
    -    +### Quick Start (one command)
    -    +
    -    +```powershell
    -    +pwsh -File boot.ps1
    -    +```
    -    +
    -    +This creates the venv, installs all deps, validates `.env`, runs DB migrations, and starts both servers.
    -    +
    -    +### Manual Install
    -    +
    -    +```powershell
    -    +# Backend
    -    +python -m venv .venv
    -    +.venv\Scripts\Activate.ps1        # Windows
    -    +# source .venv/bin/activate       # Linux/macOS
    -    +pip install -r requirements.txt
    -    +
    -    +# Frontend
    -    +cd web && npm install && cd ..
    -    +
    -    +# Database
    -    +psql $DATABASE_URL -f db/migrations/001_initial_schema.sql
    -    +```
    -    +
    -    +---
    -     
    -     ## Credential / API Setup
    -     
    -    -_To be completed._
    -    +### GitHub OAuth App
    -    +
    -    +1. Go to **GitHub > Settings > Developer Settings > OAuth Apps > New OAuth App**
    -    +2. Fill in:
    -    +   - **Application name:** ForgeGuard
    -    +   - **Homepage URL:** `http://localhost:5173`
    -    +   - **Authorization callback URL:** `http://localhost:5173/auth/callback`
    -    +3. Copy the **Client ID** and **Client Secret** into your `.env`.
    -    +
    -    +### Webhook Secret
    -    +
    -    +Generate a random string (e.g. `openssl rand -hex 32`) and use it as `GITHUB_WEBHOOK_SECRET` in `.env`. ForgeGuard will register this secret when connecting repos.
    -    +
    -    +---
    -     
    -     ## Configure `.env`
    -     
    -    -_To be completed._
    -    +Create a `.env` file in the project root (or copy `.env.example`):
    -    +
    -    +```env
    -    +DATABASE_URL=postgresql://user:pass@localhost:5432/forgeguard
    -    +GITHUB_CLIENT_ID=your_client_id
    -    +GITHUB_CLIENT_SECRET=your_client_secret
    -    +GITHUB_WEBHOOK_SECRET=your_webhook_secret
    -    +JWT_SECRET=your_jwt_secret
    -    +FRONTEND_URL=http://localhost:5173
    -    +APP_URL=http://localhost:8000
    -    +```
    -    +
    -    +**Required** (app will not start without these):
    -    +- `DATABASE_URL` ├ö├ç├Â PostgreSQL connection string
    -    +- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` ├ö├ç├Â GitHub OAuth app credentials
    -    +- `GITHUB_WEBHOOK_SECRET` ├ö├ç├Â shared secret for webhook signature validation
    -    +- `JWT_SECRET` ├ö├ç├Â secret for signing session tokens
    -    +
    -    +**Optional** (defaults shown):
    -    +- `FRONTEND_URL` ├ö├ç├Â `http://localhost:5173`
    -    +- `APP_URL` ├ö├ç├Â `http://localhost:8000`
    -    +
    -    +---
    -     
    -     ## Run
    -     
    -    -_To be completed._
    -    +```powershell
    -    +# Option 1: Quick start (installs + runs)
    -    +pwsh -File boot.ps1
    -    +
    -    +# Option 2: Backend only
    -    +pwsh -File boot.ps1 -SkipFrontend
    -    +
    -    +# Option 3: Manual
    -    +uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload   # Terminal 1
    -    +cd web && npm run dev                                        # Terminal 2
    -    +```
    -    +
    -    +Open `http://localhost:5173` and sign in with GitHub.
    -    +
    -    +---
    -     
    -     ## Stop
    -     
    -    -_To be completed._
    -    +Press `Ctrl+C` in the terminal running the backend. If the frontend was started via `boot.ps1`, it runs as a background job and will stop when the PowerShell session ends.
    -    +
    -    +---
    -     
    -     ## Key Settings Explained
    -     
    -    -_To be completed._
    -    +| Setting | Purpose |
    -    +|---------|---------|
    -    +| `DATABASE_URL` | PostgreSQL connection string with credentials |
    -    +| `GITHUB_CLIENT_ID` | Identifies your OAuth app to GitHub |
    -    +| `GITHUB_CLIENT_SECRET` | Authenticates your OAuth app to GitHub |
    -    +| `GITHUB_WEBHOOK_SECRET` | Validates incoming webhook payloads are from GitHub |
    -    +| `JWT_SECRET` | Signs session tokens ├ö├ç├Â keep this secret and random |
    -    +| `FRONTEND_URL` | Used for CORS and OAuth redirect ├ö├ç├Â must match your frontend URL |
    -    +| `APP_URL` | Backend URL for generating webhook callback URLs |
    -    +
    -    +---
    -     
    -     ## Troubleshooting
    -     
    -    -_To be completed._
    -    +### App refuses to start: "missing required environment variables"
    -    +Your `.env` file is missing one or more required variables. Check the console output for which ones.
    -    +
    -    +### Database connection errors
    -    +1. Ensure PostgreSQL is running: `pg_isready`
    -    +2. Verify `DATABASE_URL` format: `postgresql://user:pass@host:port/dbname`
    -    +3. Run migrations: `psql $DATABASE_URL -f db/migrations/001_initial_schema.sql`
    -    +
    -    +### OAuth login fails
    -    +1. Verify `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` match your GitHub OAuth app
    -    +2. Ensure the callback URL in GitHub settings is exactly `http://localhost:5173/auth/callback`
    -    +
    -    +### Webhooks not arriving
    -    +1. Your app must be publicly accessible for GitHub to reach it (use [ngrok](https://ngrok.com) for local dev)
    -    +2. Verify `GITHUB_WEBHOOK_SECRET` matches the secret in your GitHub webhook configuration
    -    +
    -    +### WebSocket disconnects
    -    +1. Check that your JWT token hasn't expired (24h lifetime)
    -    +2. The app auto-reconnects after 3 seconds ├ö├ç├Â check browser console for errors
    -    +
    -    +### Tests failing
    -    +```powershell
    -    +# Run backend tests
    -    +python -m pytest tests/ -v
    -    +
    -    +# Run frontend tests
    -    +cd web && npx vitest run
    -    +```
    -    diff --git a/app/api/rate_limit.py b/app/api/rate_limit.py
    -    new file mode 100644
    -    index 0000000..a041751
    -    --- /dev/null
    -    +++ b/app/api/rate_limit.py
    -    @@ -0,0 +1,45 @@
    -    +"""Simple in-memory rate limiter for webhook endpoints.
    -    +
    -    +Uses a sliding-window counter keyed by client IP.
    -    +Not shared across workers -- sufficient for single-process MVP.
    -    +"""
    -    +
    -    +import time
    -    +
    -    +
    -    +class RateLimiter:
    -    +    """Token-bucket rate limiter.
    -    +
    -    +    Args:
    -    +        max_requests: Maximum requests allowed in the window.
    -    +        window_seconds: Length of the sliding window in seconds.
    -    +    """
    -    +
    -    +    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
    -    +        self._max = max_requests
    -    +        self._window = window_seconds
    -    +        self._hits: dict[str, list[float]] = {}
    -    +
    -    +    def is_allowed(self, key: str) -> bool:
    -    +        """Check whether *key* is within the rate limit.
    -    +
    -    +        Returns True if the request is allowed, False if it should be rejected.
    -    +        """
    -    +        now = time.monotonic()
    -    +        cutoff = now - self._window
    -    +
    -    +        # Lazy-init or prune expired entries
    -    +        timestamps = self._hits.get(key, [])
    -    +        timestamps = [t for t in timestamps if t > cutoff]
    -    +
    -    +        if len(timestamps) >= self._max:
    -    +            self._hits[key] = timestamps
    -    +            return False
    -    +
    -    +        timestamps.append(now)
    -    +        self._hits[key] = timestamps
    -    +        return True
    -    +
    -    +
    -    +# Module-level singleton -- 30 requests per 60 seconds for webhooks.
    -    +webhook_limiter = RateLimiter(max_requests=30, window_seconds=60)
    -    diff --git a/app/api/routers/repos.py b/app/api/routers/repos.py
    -    index 1c1e8a0..42d77c5 100644
    -    --- a/app/api/routers/repos.py
    -    +++ b/app/api/routers/repos.py
    -    @@ -1,9 +1,10 @@
    -     """Repos router -- connect, disconnect, list repos, and audit results."""
    -     
    -    +import logging
    -     from uuid import UUID
    -     
    -     from fastapi import APIRouter, Depends, HTTPException, Query, status
    -    -from pydantic import BaseModel
    -    +from pydantic import BaseModel, Field
    -     
    -     from app.api.deps import get_current_user
    -     from app.services.audit_service import get_audit_detail, get_repo_audits
    -    @@ -14,15 +15,29 @@ from app.services.repo_service import (
    -         list_connected_repos,
    -     )
    -     
    -    +logger = logging.getLogger(__name__)
    -    +
    -     router = APIRouter(prefix="/repos", tags=["repos"])
    -     
    -     
    -     class ConnectRepoRequest(BaseModel):
    -         """Request body for connecting a GitHub repo."""
    -     
    -    -    github_repo_id: int
    -    -    full_name: str
    -    -    default_branch: str
    -    +    github_repo_id: int = Field(..., ge=1, description="GitHub repo numeric ID")
    -    +    full_name: str = Field(
    -    +        ...,
    -    +        min_length=3,
    -    +        max_length=200,
    -    +        pattern=r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$",
    -    +        description="GitHub full name, e.g. owner/repo",
    -    +    )
    -    +    default_branch: str = Field(
    -    +        ...,
    -    +        min_length=1,
    -    +        max_length=100,
    -    +        pattern=r"^[a-zA-Z0-9._/-]+$",
    -    +        description="Default branch name, e.g. main",
    -    +    )
    -     
    -     
    -     @router.get("")
    -    @@ -65,6 +80,7 @@ async def connect(
    -             )
    -             raise HTTPException(status_code=code, detail=detail)
    -         except Exception:
    -    +        logger.exception("Failed to register webhook for %s", body.full_name)
    -             raise HTTPException(
    -                 status_code=status.HTTP_502_BAD_GATEWAY,
    -                 detail="Failed to register webhook with GitHub",
    -    @@ -97,6 +113,7 @@ async def disconnect(
    -             )
    -             raise HTTPException(status_code=code, detail=detail)
    -         except Exception:
    -    +        logger.exception("Failed to disconnect repo %s", repo_id)
    -             raise HTTPException(
    -                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    -                 detail="Failed to disconnect repo",
    -    diff --git a/app/api/routers/webhooks.py b/app/api/routers/webhooks.py
    -    index 921f97a..da33a2d 100644
    -    --- a/app/api/routers/webhooks.py
    -    +++ b/app/api/routers/webhooks.py
    -    @@ -1,11 +1,16 @@
    -     """Webhook router -- receives GitHub push events."""
    -     
    -    +import logging
    -    +
    -     from fastapi import APIRouter, HTTPException, Request, status
    -     
    -    +from app.api.rate_limit import webhook_limiter
    -     from app.config import settings
    -     from app.services.audit_service import process_push_event
    -     from app.webhooks import verify_github_signature
    -     
    -    +logger = logging.getLogger(__name__)
    -    +
    -     router = APIRouter(tags=["webhooks"])
    -     
    -     
    -    @@ -14,7 +19,15 @@ async def github_webhook(request: Request) -> dict:
    -         """Receive a GitHub push webhook event.
    -     
    -         Validates the X-Hub-Signature-256 header, then processes the push.
    -    +    Rate-limited to prevent abuse.
    -         """
    -    +    client_ip = request.client.host if request.client else "unknown"
    -    +    if not webhook_limiter.is_allowed(client_ip):
    -    +        raise HTTPException(
    -    +            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    -    +            detail="Rate limit exceeded",
    -    +        )
    -    +
    -         signature = request.headers.get("X-Hub-Signature-256", "")
    -         body = await request.body()
    -     
    -    @@ -30,5 +43,12 @@ async def github_webhook(request: Request) -> dict:
    -         if event_type != "push":
    -             return {"status": "ignored", "event": event_type}
    -     
    -    -    await process_push_event(payload)
    -    +    try:
    -    +        await process_push_event(payload)
    -    +    except Exception:
    -    +        logger.exception("Error processing push event")
    -    +        raise HTTPException(
    -    +            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    -    +            detail="Internal error processing webhook",
    -    +        )
    -         return {"status": "accepted"}
    -    diff --git a/app/config.py b/app/config.py
    -    index 01eb98c..39cfd7b 100644
    -    --- a/app/config.py
    -    +++ b/app/config.py
    -    @@ -1,22 +1,55 @@
    -    -"""Application configuration loaded from environment variables."""
    -    +"""Application configuration loaded from environment variables.
    -    +
    -    +Validates required settings on import -- fails fast if critical vars are missing.
    -    +"""
    -     
    -     import os
    -    +import sys
    -     
    -     from dotenv import load_dotenv
    -     
    -     load_dotenv()
    -     
    -     
    -    -class Settings:
    -    -    """Application settings from environment."""
    -    +class _MissingVars(Exception):
    -    +    """Raised when required environment variables are absent."""
    -    +
    -    +
    -    +def _require(name: str) -> str:
    -    +    """Return env var value or record it as missing."""
    -    +    val = os.getenv(name, "")
    -    +    if not val:
    -    +        _missing.append(name)
    -    +    return val
    -    +
    -     
    -    -    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    -    -    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    -    -    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    -    -    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    -    -    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    -    +_missing: list[str] = []
    -    +
    -    +
    -    +class Settings:
    -    +    """Application settings from environment.
    -    +
    -    +    Required vars (must be set in production, may be blank in test):
    -    +      DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET,
    -    +      GITHUB_WEBHOOK_SECRET, JWT_SECRET
    -    +    """
    -    +
    -    +    DATABASE_URL: str = _require("DATABASE_URL")
    -    +    GITHUB_CLIENT_ID: str = _require("GITHUB_CLIENT_ID")
    -    +    GITHUB_CLIENT_SECRET: str = _require("GITHUB_CLIENT_SECRET")
    -    +    GITHUB_WEBHOOK_SECRET: str = _require("GITHUB_WEBHOOK_SECRET")
    -    +    JWT_SECRET: str = _require("JWT_SECRET")
    -         FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    -         APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")
    -     
    -     
    -    +# Validate at import time -- but only when NOT running under pytest.
    -    +if _missing and "pytest" not in sys.modules:
    -    +    print(
    -    +        f"[config] FATAL: missing required environment variables: "
    -    +        f"{', '.join(_missing)}",
    -    +        file=sys.stderr,
    -    +    )
    -    +    sys.exit(1)
    -    +
    -     settings = Settings()
    -    diff --git a/app/main.py b/app/main.py
    -    index 4d80f1b..14d135b 100644
    -    --- a/app/main.py
    -    +++ b/app/main.py
    -    @@ -1,9 +1,11 @@
    -     """ForgeGuard -- FastAPI application entry point."""
    -     
    -    +import logging
    -     from contextlib import asynccontextmanager
    -     
    -    -from fastapi import FastAPI
    -    +from fastapi import FastAPI, Request
    -     from fastapi.middleware.cors import CORSMiddleware
    -    +from fastapi.responses import JSONResponse
    -     
    -     from app.api.routers.auth import router as auth_router
    -     from app.api.routers.health import router as health_router
    -    @@ -13,6 +15,8 @@ from app.api.routers.ws import router as ws_router
    -     from app.config import settings
    -     from app.repos.db import close_pool
    -     
    -    +logger = logging.getLogger(__name__)
    -    +
    -     
    -     @asynccontextmanager
    -     async def lifespan(application: FastAPI):
    -    @@ -30,12 +34,23 @@ def create_app() -> FastAPI:
    -             lifespan=lifespan,
    -         )
    -     
    -    +    # Global exception handler -- never leak stack traces to clients.
    -    +    @application.exception_handler(Exception)
    -    +    async def _unhandled_exception_handler(
    -    +        request: Request, exc: Exception
    -    +    ) -> JSONResponse:
    -    +        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    -    +        return JSONResponse(
    -    +            status_code=500,
    -    +            content={"detail": "Internal server error"},
    -    +        )
    -    +
    -         application.add_middleware(
    -             CORSMiddleware,
    -             allow_origins=[settings.FRONTEND_URL],
    -             allow_credentials=True,
    -    -        allow_methods=["*"],
    -    -        allow_headers=["*"],
    -    +        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    -    +        allow_headers=["Authorization", "Content-Type"],
    -         )
    -     
    -         application.include_router(health_router)
    -    diff --git a/boot.ps1 b/boot.ps1
    -    index f158bbf..27434b1 100644
    -    --- a/boot.ps1
    -    +++ b/boot.ps1
    -    @@ -1,78 +1,183 @@
    -    -# boot.ps1 -- ForgeGuard one-click setup and run script
    -    -# Phase 0 stub. Full implementation in Phase 5.
    -    +# boot.ps1 -- ForgeGuard one-click setup and run script.
    -    +#
    -    +# Brings up the full stack from a fresh clone:
    -    +#   1. Validates prerequisites (Python 3.12+, Node 18+, psql)
    -    +#   2. Creates Python venv and installs backend deps
    -    +#   3. Installs frontend deps
    -    +#   4. Validates .env (fails fast if missing required vars)
    -    +#   5. Runs database migrations
    -    +#   6. Starts backend + frontend dev servers
    -    +#
    -    +# Usage:
    -    +#   pwsh -File boot.ps1
    -    +#   pwsh -File boot.ps1 -SkipFrontend
    -    +#   pwsh -File boot.ps1 -MigrateOnly
    -    +
    -    +[CmdletBinding()]
    -    +param(
    -    +  [switch]$SkipFrontend,
    -    +  [switch]$MigrateOnly
    -    +)
    -     
    -     Set-StrictMode -Version Latest
    -     $ErrorActionPreference = "Stop"
    -     
    -     function Info([string]$m) { Write-Host "[boot] $m" -ForegroundColor Cyan }
    -    +function Warn([string]$m) { Write-Host "[boot] $m" -ForegroundColor Yellow }
    -     function Err ([string]$m) { Write-Host "[boot] $m" -ForegroundColor Red }
    -    +function Ok  ([string]$m) { Write-Host "[boot] $m" -ForegroundColor Green }
    -    +
    -    +$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
    -    +if (-not $root) { $root = Get-Location }
    -    +Set-Location $root
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç 1. Check prerequisites ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -     
    -    -# -- 1. Check prerequisites -----------------------------------------------
    -     Info "Checking prerequisites..."
    -    +
    -     $pythonCmd = $null
    -    -foreach ($candidate in @("python", "python3")) {
    -    -    try {
    -    -        $ver = & $candidate --version 2>&1
    -    -        if ($ver -match "Python\s+3\.(\d+)") {
    -    -            $minor = [int]$Matches[1]
    -    -            if ($minor -ge 12) {
    -    -                $pythonCmd = $candidate
    -    -                Info "Found $ver"
    -    -                break
    -    -            }
    -    -        }
    -    -    } catch { }
    -    +foreach ($candidate in @("python3", "python")) {
    -    +  try {
    -    +    $ver = & $candidate --version 2>&1
    -    +    if ($ver -match "Python\s+3\.(\d+)") {
    -    +      $minor = [int]$Matches[1]
    -    +      if ($minor -ge 12) {
    -    +        $pythonCmd = $candidate
    -    +        Info "Found $ver"
    -    +        break
    -    +      }
    -    +    }
    -    +  } catch { }
    -     }
    -     if (-not $pythonCmd) {
    -    -    Err "Python 3.12+ is required but was not found. Please install it and try again."
    -    +  Err "Python 3.12+ is required but was not found."
    -    +  exit 1
    -    +}
    -    +
    -    +if (-not $SkipFrontend) {
    -    +  $nodeCmd = Get-Command "node" -ErrorAction SilentlyContinue
    -    +  if (-not $nodeCmd) {
    -    +    Err "Node.js 18+ is required for frontend. Use -SkipFrontend to skip."
    -         exit 1
    -    +  }
    -    +  Info "Node: $(node --version)"
    -     }
    -     
    -    -# -- 2. Create virtual environment -----------------------------------------
    -    -if (-not (Test-Path ".venv")) {
    -    -    Info "Creating virtual environment..."
    -    -    & $pythonCmd -m venv .venv
    -    -    if ($LASTEXITCODE -ne 0) {
    -    -        Err "Failed to create virtual environment."
    -    -        exit 1
    -    -    }
    -    +$psqlCmd = Get-Command "psql" -ErrorAction SilentlyContinue
    -    +if ($psqlCmd) { Info "psql: found on PATH" }
    -    +else { Warn "psql not on PATH -- you may need to run migrations manually." }
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç 2. Python virtual environment ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +$venvDir = Join-Path $root ".venv"
    -    +if (-not (Test-Path $venvDir)) {
    -    +  Info "Creating virtual environment..."
    -    +  & $pythonCmd -m venv $venvDir
    -    +  if ($LASTEXITCODE -ne 0) { Err "Failed to create virtual environment."; exit 1 }
    -    +  Ok "Virtual environment created."
    -     } else {
    -    -    Info "Virtual environment already exists."
    -    +  Info "Virtual environment already exists."
    -    +}
    -    +
    -    +$venvPython = Join-Path $venvDir "Scripts/python.exe"
    -    +$venvPythonUnix = Join-Path $venvDir "bin/python"
    -    +$activePython = if (Test-Path $venvPython) { $venvPython } elseif (Test-Path $venvPythonUnix) { $venvPythonUnix } else { $pythonCmd }
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç 3. Install backend dependencies ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +Info "Installing Python dependencies..."
    -    +& $activePython -m pip install -r (Join-Path $root "requirements.txt") --quiet
    -    +if ($LASTEXITCODE -ne 0) { Err "pip install failed."; exit 1 }
    -    +Ok "Backend dependencies installed."
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç 4. Validate .env ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +$envFile = Join-Path $root ".env"
    -    +$envExample = Join-Path $root ".env.example"
    -    +
    -    +if (-not (Test-Path $envFile)) {
    -    +  if (Test-Path $envExample) {
    -    +    Copy-Item $envExample $envFile
    -    +    Warn ".env created from .env.example -- fill in your secrets before continuing."
    -    +  } else {
    -    +    Err "No .env file found. Create one with the required variables."
    -    +    Err "Required: DATABASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET, JWT_SECRET"
    -    +    exit 1
    -    +  }
    -    +}
    -    +
    -    +$requiredVars = @("DATABASE_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_WEBHOOK_SECRET", "JWT_SECRET")
    -    +$envContent = Get-Content $envFile -Raw
    -    +$missingVars = @()
    -    +foreach ($v in $requiredVars) {
    -    +  if ($envContent -notmatch "(?m)^$v\s*=\s*.+") {
    -    +    $missingVars += $v
    -    +  }
    -     }
    -     
    -    -# -- 3. Activate environment -----------------------------------------------
    -    -Info "Activating virtual environment..."
    -    -$activateScript = Join-Path ".venv" "Scripts" "Activate.ps1"
    -    -if (-not (Test-Path $activateScript)) {
    -    -    $activateScript = Join-Path ".venv" "bin" "Activate.ps1"
    -    +if ($missingVars.Count -gt 0) {
    -    +  Err "Missing or empty vars in .env: $($missingVars -join ', ')"
    -    +  Err "Edit .env and fill in these values, then re-run boot.ps1."
    -    +  exit 1
    -     }
    -    -if (Test-Path $activateScript) {
    -    -    . $activateScript
    -    +
    -    +Ok ".env validated -- all required variables present."
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç 5. Database migration ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +$migrationFile = Join-Path $root "db/migrations/001_initial_schema.sql"
    -    +
    -    +if (Test-Path $migrationFile) {
    -    +  Info "Running database migration..."
    -    +  $dbUrl = ""
    -    +  $match = Select-String -Path $envFile -Pattern '^DATABASE_URL\s*=\s*(.+)' -ErrorAction SilentlyContinue
    -    +  if ($match) { $dbUrl = $match.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'") }
    -    +
    -    +  if ($dbUrl -and $psqlCmd) {
    -    +    & psql $dbUrl -f $migrationFile 2>&1 | Out-Null
    -    +    if ($LASTEXITCODE -eq 0) { Ok "Migration applied." }
    -    +    else { Warn "Migration may have already been applied (tables exist)." }
    -    +  } else {
    -    +    Warn "Cannot run migration automatically."
    -    +    Warn "Run: psql \`$DATABASE_URL -f db/migrations/001_initial_schema.sql"
    -    +  }
    -     } else {
    -    -    Err "Could not find activation script at $activateScript"
    -    -    exit 1
    -    +  Warn "Migration file not found at db/migrations/001_initial_schema.sql"
    -     }
    -     
    -    -# -- 4. Install dependencies -----------------------------------------------
    -    -Info "Installing Python dependencies..."
    -    -& pip install -r requirements.txt --quiet
    -    -if ($LASTEXITCODE -ne 0) {
    -    -    Err "Failed to install Python dependencies."
    -    -    exit 1
    -    +if ($MigrateOnly) {
    -    +  Ok "Migration complete. Exiting."
    -    +  exit 0
    -     }
    -     
    -    -# -- 5. Prompt for credentials (stub -- will be expanded in Phase 5) --------
    -    -if (-not (Test-Path ".env")) {
    -    -    Info "No .env file found. Copying from .env.example..."
    -    -    if (Test-Path ".env.example") {
    -    -        Copy-Item ".env.example" ".env"
    -    -        Info "Created .env from .env.example. Please edit it with your real credentials."
    -    -    } else {
    -    -        Err ".env.example not found. Please create a .env file manually."
    -    -        exit 1
    -    -    }
    -    +# ├ö├Â├ç├ö├Â├ç 6. Frontend setup ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +$webDir = Join-Path $root "web"
    -    +
    -    +if (-not $SkipFrontend -and (Test-Path $webDir)) {
    -    +  Info "Installing frontend dependencies..."
    -    +  Push-Location $webDir
    -    +  & npm install --silent 2>&1 | Out-Null
    -    +  if ($LASTEXITCODE -ne 0) { Err "npm install failed."; Pop-Location; exit 1 }
    -    +  Ok "Frontend dependencies installed."
    -    +  Pop-Location
    -     }
    -     
    -    -# -- 6. Start the app -------------------------------------------------------
    -    +# ├ö├Â├ç├ö├Â├ç 7. Start servers ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +Info ""
    -     Info "Starting ForgeGuard..."
    -    -& python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    -    +
    -    +if (-not $SkipFrontend -and (Test-Path $webDir)) {
    -    +  Info "Starting frontend dev server on port 5173..."
    -    +  $frontendJob = Start-Job -ScriptBlock {
    -    +    param($dir)
    -    +    Set-Location $dir
    -    +    & npm run dev 2>&1
    -    +  } -ArgumentList $webDir
    -    +  Info "Frontend started (background job $($frontendJob.Id))."
    -    +}
    -    +
    -    +Info "Starting backend server on port 8000..."
    -    +Info "Press Ctrl+C to stop."
    -    +& $activePython -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    -    diff --git a/tests/test_config.py b/tests/test_config.py
    -    new file mode 100644
    -    index 0000000..ba8e5d9
    -    --- /dev/null
    -    +++ b/tests/test_config.py
    -    @@ -0,0 +1,41 @@
    -    +"""Tests for config validation."""
    -    +
    -    +import importlib
    -    +import os
    -    +import sys
    -    +
    -    +
    -    +def test_config_loads_with_env_vars(monkeypatch):
    -    +    """Config should load successfully when all required vars are set."""
    -    +    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    -    +    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_id")
    -    +    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test_secret")
    -    +    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook_secret")
    -    +    monkeypatch.setenv("JWT_SECRET", "jwt_secret")
    -    +
    -    +    # Force reimport to test validation
    -    +    if "app.config" in sys.modules:
    -    +        mod = sys.modules["app.config"]
    -    +        # Settings class re-reads on attribute access via _require
    -    +        assert mod.settings is not None
    -    +
    -    +
    -    +def test_config_has_default_urls():
    -    +    """FRONTEND_URL and APP_URL should have sensible defaults."""
    -    +    from app.config import settings
    -    +
    -    +    assert "localhost" in settings.FRONTEND_URL or settings.FRONTEND_URL != ""
    -    +    assert "localhost" in settings.APP_URL or settings.APP_URL != ""
    -    +
    -    +
    -    +def test_config_settings_type():
    -    +    """Settings object should exist with expected attributes."""
    -    +    from app.config import settings
    -    +
    -    +    assert hasattr(settings, "DATABASE_URL")
    -    +    assert hasattr(settings, "GITHUB_CLIENT_ID")
    -    +    assert hasattr(settings, "GITHUB_CLIENT_SECRET")
    -    +    assert hasattr(settings, "GITHUB_WEBHOOK_SECRET")
    -    +    assert hasattr(settings, "JWT_SECRET")
    -    +    assert hasattr(settings, "FRONTEND_URL")
    -    +    assert hasattr(settings, "APP_URL")
    -    diff --git a/tests/test_hardening.py b/tests/test_hardening.py
    -    new file mode 100644
    -    index 0000000..a39f291
    -    --- /dev/null
    -    +++ b/tests/test_hardening.py
    -    @@ -0,0 +1,179 @@
    -    +"""Tests for Phase 5 hardening: rate limiting, input validation, error handling."""
    -    +
    -    +import json
    -    +from unittest.mock import AsyncMock, patch
    -    +from uuid import UUID
    -    +
    -    +import pytest
    -    +from fastapi.testclient import TestClient
    -    +
    -    +from app.auth import create_token
    -    +from app.main import app
    -    +from app.webhooks import _hmac_sha256
    -    +
    -    +
    -    +@pytest.fixture(autouse=True)
    -    +def _set_test_config(monkeypatch):
    -    +    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    -    +    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    -    +    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")
    -    +    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    -    +    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    -    +    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    -    +    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    -    +
    -    +
    -    +USER_ID = "22222222-2222-2222-2222-222222222222"
    -    +MOCK_USER = {
    -    +    "id": UUID(USER_ID),
    -    +    "github_id": 99999,
    -    +    "github_login": "octocat",
    -    +    "avatar_url": "https://example.com/avatar.png",
    -    +    "access_token": "gho_testtoken123",
    -    +}
    -    +
    -    +client = TestClient(app)
    -    +
    -    +
    -    +def _auth_header():
    -    +    token = create_token(USER_ID, "octocat")
    -    +    return {"Authorization": f"Bearer {token}"}
    -    +
    -    +
    -    +def _sign(payload_bytes: bytes) -> str:
    -    +    digest = _hmac_sha256(b"whsec_test", payload_bytes)
    -    +    return f"sha256={digest}"
    -    +
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç Rate limiting ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +
    -    +@patch("app.api.routers.webhooks.process_push_event", new_callable=AsyncMock)
    -    +def test_webhook_rate_limit_blocks_excess(mock_process):
    -    +    """Webhook endpoint should return 429 when rate limit is exceeded."""
    -    +    from app.api.routers.webhooks import webhook_limiter
    -    +
    -    +    # Reset limiter for test isolation
    -    +    webhook_limiter._hits.clear()
    -    +
    -    +    mock_process.return_value = {"id": "test"}
    -    +    payload = json.dumps({
    -    +        "ref": "refs/heads/main",
    -    +        "head_commit": {"id": "abc", "message": "test", "author": {"name": "bot"}},
    -    +        "repository": {"id": 1, "full_name": "o/r"},
    -    +        "commits": [],
    -    +    }).encode()
    -    +
    -    +    headers = {
    -    +        "Content-Type": "application/json",
    -    +        "X-Hub-Signature-256": _sign(payload),
    -    +        "X-GitHub-Event": "push",
    -    +    }
    -    +
    -    +    # Send up to the limit (30 requests)
    -    +    for _ in range(30):
    -    +        resp = client.post("/webhooks/github", content=payload, headers=headers)
    -    +        assert resp.status_code == 200
    -    +
    -    +    # 31st request should be rate-limited
    -    +    resp = client.post("/webhooks/github", content=payload, headers=headers)
    -    +    assert resp.status_code == 429
    -    +    assert "rate limit" in resp.json()["detail"].lower()
    -    +
    -    +    # Clean up
    -    +    webhook_limiter._hits.clear()
    -    +
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç Input validation ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +# Pydantic validation returns 422 BEFORE reaching the auth or DB layer,
    -    +# so we don't need to mock DB calls for these tests.
    -    +
    -    +
    -    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
    -    +def test_connect_repo_rejects_invalid_full_name(mock_get_user):
    -    +    """full_name must match owner/repo pattern."""
    -    +    resp = client.post(
    -    +        "/repos/connect",
    -    +        json={
    -    +            "github_repo_id": 1,
    -    +            "full_name": "not a valid repo name!!!",
    -    +            "default_branch": "main",
    -    +        },
    -    +        headers=_auth_header(),
    -    +    )
    -    +    assert resp.status_code == 422
    -    +
    -    +
    -    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
    -    +def test_connect_repo_rejects_zero_id(mock_get_user):
    -    +    """github_repo_id must be >= 1."""
    -    +    resp = client.post(
    -    +        "/repos/connect",
    -    +        json={
    -    +            "github_repo_id": 0,
    -    +            "full_name": "owner/repo",
    -    +            "default_branch": "main",
    -    +        },
    -    +        headers=_auth_header(),
    -    +    )
    -    +    assert resp.status_code == 422
    -    +
    -    +
    -    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
    -    +def test_connect_repo_rejects_empty_branch(mock_get_user):
    -    +    """default_branch must not be empty."""
    -    +    resp = client.post(
    -    +        "/repos/connect",
    -    +        json={
    -    +            "github_repo_id": 1,
    -    +            "full_name": "owner/repo",
    -    +            "default_branch": "",
    -    +        },
    -    +        headers=_auth_header(),
    -    +    )
    -    +    assert resp.status_code == 422
    -    +
    -    +
    -    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    -    +@patch("app.api.routers.repos.connect_repo", new_callable=AsyncMock)
    -    +def test_connect_repo_accepts_valid_input(mock_connect, mock_get_user):
    -    +    """Valid input should pass validation and reach the service layer."""
    -    +    mock_get_user.return_value = MOCK_USER
    -    +    mock_connect.return_value = {
    -    +        "id": UUID("11111111-1111-1111-1111-111111111111"),
    -    +        "full_name": "owner/repo",
    -    +        "webhook_active": True,
    -    +    }
    -    +    resp = client.post(
    -    +        "/repos/connect",
    -    +        json={
    -    +            "github_repo_id": 12345,
    -    +            "full_name": "owner/repo",
    -    +            "default_branch": "main",
    -    +        },
    -    +        headers=_auth_header(),
    -    +    )
    -    +    assert resp.status_code == 200
    -    +
    -    +
    -    +# ├ö├Â├ç├ö├Â├ç Error handling ├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç├ö├Â├ç
    -    +
    -    +
    -    +def test_global_error_handler_is_registered():
    -    +    """App should have a global exception handler for unhandled errors."""
    -    +    from app.main import app as test_app
    -    +
    -    +    handlers = test_app.exception_handlers
    -    +    assert Exception in handlers
    -    +
    -    +
    -    +def test_cors_allows_valid_origin():
    -    +    """CORS should accept requests from the configured frontend origin."""
    -    +    resp = client.options(
    -    +        "/health",
    -    +        headers={
    -    +            "Origin": "http://localhost:5173",
    -    +            "Access-Control-Request-Method": "GET",
    -    +        },
    -    +    )
    -    +    assert resp.status_code == 200
    -    diff --git a/tests/test_rate_limit.py b/tests/test_rate_limit.py
    -    new file mode 100644
    -    index 0000000..af30e8f
    -    --- /dev/null
    -    +++ b/tests/test_rate_limit.py
    -    @@ -0,0 +1,38 @@
    -    +"""Tests for rate limiter."""
    -    +
    -    +from app.api.rate_limit import RateLimiter
    -    +
    -    +
    -    +def test_allows_within_limit():
    -    +    """Requests within the limit should be allowed."""
    -    +    limiter = RateLimiter(max_requests=3, window_seconds=60)
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client1") is True
    -    +
    -    +
    -    +def test_blocks_over_limit():
    -    +    """Requests exceeding the limit should be blocked."""
    -    +    limiter = RateLimiter(max_requests=2, window_seconds=60)
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client1") is False
    -    +
    -    +
    -    +def test_separate_keys():
    -    +    """Different keys should have independent limits."""
    -    +    limiter = RateLimiter(max_requests=1, window_seconds=60)
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client2") is True
    -    +    assert limiter.is_allowed("client1") is False
    -    +
    -    +
    -    +def test_window_expiry():
    -    +    """Requests should be allowed again after the window expires."""
    -    +    import time
    -    +
    -    +    limiter = RateLimiter(max_requests=1, window_seconds=0.1)
    -    +    assert limiter.is_allowed("client1") is True
    -    +    assert limiter.is_allowed("client1") is False
    -    +    time.sleep(0.15)
    -    +    assert limiter.is_allowed("client1") is True
    +    (pending -- work in progress)
     
     ## Verification
    -- Static analysis: compileall pass on all modules
    -- Runtime: all endpoints verified via test client
    -- Behavior: pytest 70 passed, vitest 15 passed
    -- Contract compliance: boundaries.json respected, no forbidden imports
    +- Static: pending
    +- Runtime: pending
    +- Behavior: pending
    +- Contract: pending
     
     ## Notes (optional)
    -- No blockers. All Phase 5 features implemented and tested.
    +- Physics-first gate: /health/version added to physics.yaml before implementation
     
     ## Next Steps
    -- Project is ready for deployment to Render
    +- Implement all Phase 6 deliverables
    +- Run full test suite
    +- Finalize diff log and trigger audit
     
    diff --git a/app/api/routers/health.py b/app/api/routers/health.py
    index dfaec1e..f6d3f24 100644
    --- a/app/api/routers/health.py
    +++ b/app/api/routers/health.py
    @@ -2,6 +2,8 @@
     
     from fastapi import APIRouter
     
    +from app.config import VERSION
    +
     router = APIRouter()
     
     
    @@ -9,3 +11,9 @@ router = APIRouter()
     async def health_check() -> dict:
         """Return basic health status."""
         return {"status": "ok"}
    +
    +
    +@router.get("/health/version")
    +async def health_version() -> dict:
    +    """Return application version and current phase."""
    +    return {"version": VERSION, "phase": "6"}
    diff --git a/app/config.py b/app/config.py
    index 39cfd7b..d004eb2 100644
    --- a/app/config.py
    +++ b/app/config.py
    @@ -3,6 +3,8 @@
     Validates required settings on import -- fails fast if critical vars are missing.
     """
     
    +VERSION = "0.1.0"
    +
     import os
     import sys
     
    diff --git a/tests/test_health.py b/tests/test_health.py
    index dd4b2a6..7fd87b0 100644
    --- a/tests/test_health.py
    +++ b/tests/test_health.py
    @@ -14,3 +14,12 @@ def test_health_returns_ok():
         assert response.status_code == 200
         data = response.json()
         assert data == {"status": "ok"}
    +
    +
    +def test_health_version_returns_version():
    +    """GET /health/version returns 200 with version and phase."""
    +    response = client.get("/health/version")
    +    assert response.status_code == 200
    +    data = response.json()
    +    assert data["version"] == "0.1.0"
    +    assert data["phase"] == "6"
    diff --git a/web/src/components/AppShell.tsx b/web/src/components/AppShell.tsx
    index f3102d9..47a1098 100644
    --- a/web/src/components/AppShell.tsx
    +++ b/web/src/components/AppShell.tsx
    @@ -132,6 +132,8 @@ function AppShell({ children, sidebarRepos, onReposChange }: AppShellProps) {
                   overflowY: 'auto',
                   flexShrink: 0,
                   background: '#0F172A',
    +              display: 'flex',
    +              flexDirection: 'column',
                 }}
               >
                 <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
    @@ -185,6 +187,17 @@ function AppShell({ children, sidebarRepos, onReposChange }: AppShellProps) {
                     );
                   })
                 )}
    +            <div
    +              style={{
    +                marginTop: 'auto',
    +                padding: '12px 16px',
    +                borderTop: '1px solid #1E293B',
    +                color: '#64748B',
    +                fontSize: '0.7rem',
    +              }}
    +            >
    +              v0.1.0
    +            </div>
               </aside>
             )}
     