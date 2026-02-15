# ForgeGuard -- Phases

Canonical phase plan. Each phase is self-contained, shippable, and auditable. The builder contract (S1) requires completing phases in strict order. No phase may begin until the previous phase passes audit.

---

## Phase 0 -- Genesis

**Objective:** Skeleton project that boots, passes lint, has one test, and serves health.

**Deliverables:**
- `/health` returns `{ "status": "ok" }` (FastAPI)
- Root `boot.ps1` -- installs backend deps, starts uvicorn
- `pytest` passes with one health test
- Project structure matches blueprint layers
- `forge.json` at project root with initial phase metadata
- `.gitignore` for Python + Node

**Exit criteria:**
- `boot.ps1` runs without errors
- `GET /health` returns 200
- `pytest` passes (1 test)
- `run_audit.ps1` passes all checks

---

## Phase 1 -- Authentication

**Objective:** GitHub OAuth login flow. Users can sign in and out. JWT session management.

**Deliverables:**
- `POST /auth/github` -- accepts GitHub OAuth code, exchanges for access token, creates/updates user, returns JWT
- `GET /auth/github/callback` -- handles the OAuth redirect
- `GET /auth/me` -- returns current user from JWT
- `users` table (id, github_id, github_login, avatar_url, access_token_enc, created_at, updated_at)
- JWT encode/decode utility (HS256, env-configured secret)
- Auth middleware that protects all routes except `/health`, `/auth/*`
- Login page with "Sign in with GitHub" button

**Schema coverage:**
- [x] `users` table

**Exit criteria:**
- OAuth round-trip works (login -> GitHub -> callback -> JWT returned)
- `GET /auth/me` returns user info with valid JWT
- `GET /auth/me` returns 401 with missing/invalid JWT
- All protected routes return 401 without auth
- `run_audit.ps1` passes all checks

---

## Phase 2 -- Repo Management

**Objective:** Connect and disconnect GitHub repos. Manage webhook lifecycle.

**Deliverables:**
- `GET /repos` -- list connected repos for current user
- `POST /repos/connect` -- accepts `{ "github_repo_id": int }`, fetches repo metadata from GitHub, installs webhook, creates `repos` row
- `POST /repos/{id}/disconnect` -- removes webhook from GitHub, deletes `repos` row and associated audit data
- `repos` table (id, user_id FK, github_repo_id, full_name, default_branch, webhook_id, connected_at)
- GitHub API client: list user repos, create webhook, delete webhook
- Repo picker modal on frontend (searchable list of user's GitHub repos)
- Repo list on dashboard (shows connected repos with placeholder health badge)

**Schema coverage:**
- [x] `users` table (Phase 1)
- [x] `repos` table

**Exit criteria:**
- User can connect a repo and see it in the list
- User can disconnect a repo (webhook removed, data deleted)
- Connecting same repo twice returns 409
- Webhook appears on the GitHub repo settings page after connect
- `run_audit.ps1` passes all checks

---

## Phase 3 -- Audit Engine

**Objective:** Receive GitHub push events, run audit checks, store results.

**Deliverables:**
- `POST /webhooks/github` -- receives push event, validates signature, enqueues audit
- Audit runner: clones repo at commit SHA (shallow clone), runs registered checks, stores results
- Check registry: pluggable check functions (start with A4 boundary check, A9 dependency gate)
- `audit_runs` table (id, repo_id FK, commit_sha, commit_message, author, branch, status, started_at, completed_at)
- `audit_checks` table (id, audit_run_id FK, check_code, check_name, result, detail)
- `GET /repos/{id}/audits` -- list audit runs for a repo
- `GET /repos/{id}/audits/{auditId}` -- full audit detail with check results
- Commit timeline view on frontend (list of audits per repo)
- Audit detail view on frontend (check breakdown)

**Schema coverage:**
- [x] `users` table (Phase 1)
- [x] `repos` table (Phase 2)
- [x] `audit_runs` table
- [x] `audit_checks` table

**Exit criteria:**
- Push to connected repo triggers audit via webhook
- Audit results appear in DB within 30 seconds of push
- `GET /repos/{id}/audits` returns audit list with correct data
- Audit detail shows individual check results
- Invalid webhook signatures are rejected (401)
- `run_audit.ps1` passes all checks

---

## Phase 4 -- Dashboard & Real-Time

**Objective:** Polished frontend with real-time updates via WebSocket.

**Deliverables:**
- `GET /ws` -- WebSocket endpoint, sends audit updates to connected clients
- Real-time dashboard: new audit results appear without page refresh
- Health badge calculation: green (all pass), yellow (warnings only), red (any fail)
- Commit timeline with result badges and mini check summary
- Audit detail page with full check breakdown
- Skeleton loaders for all data views
- Empty states for repos and audits
- Toast notifications for errors
- Responsive sidebar collapse below 1024px

**Schema coverage:**
- [x] `users` table (Phase 1)
- [x] `repos` table (Phase 2)
- [x] `audit_runs` table (Phase 3)
- [x] `audit_checks` table (Phase 3)

**Exit criteria:**
- Dashboard updates in real-time when a new audit completes
- Health badges reflect actual audit results
- All empty states render correctly
- UI matches ui.md specification
- All Vitest frontend tests pass
- `run_audit.ps1` passes all checks

---

## Phase 5 -- Ship Gate

**Objective:** Final hardening, documentation, and deployment readiness.

**Deliverables:**
- `USER_INSTRUCTIONS.md` -- end-user setup and usage guide
- `boot.ps1` -- one-click full setup (backend + frontend + DB migration)
- Rate limiting on webhook endpoint
- Input validation on all API endpoints
- Error handling audit (no unhandled exceptions leak stack traces)
- All tests pass (pytest + Vitest)
- Environment variable validation on startup (fail fast if missing)

**Exit criteria:**
- `boot.ps1` brings up the full stack from a fresh clone
- `USER_INSTRUCTIONS.md` covers: prerequisites, setup, usage, troubleshooting
- No unhandled exceptions in any code path
- All tests pass
- `run_audit.ps1` passes all checks
- Project is deployable to Render

---

## Phase 6 -- Integration Test

**Objective:** Validate the full audit pipeline end-to-end with a minor code change, confirming the builder workflow, test suite, and governance audit all function correctly post-ship.

**Deliverables:**
- Add `GET /health/version` endpoint returning `{ "version": "0.1.0", "phase": "6" }`
- Add `VERSION` constant to `app/config.py`
- Add test for the new endpoint in `tests/test_health.py`
- Frontend: show version string in the AppShell sidebar footer
- All existing tests continue to pass (no regressions)

**Exit criteria:**
- `GET /health/version` returns 200 with correct payload
- New test passes
- All 85+ existing tests still pass (pytest + Vitest)
- Version visible in sidebar footer
- `run_audit.ps1` passes all checks
