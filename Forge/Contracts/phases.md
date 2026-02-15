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

---

## Phase 7 -- Python Audit Runner

**Objective:** Port the PowerShell audit checks (A1-A9, W1-W3) to a Python module, enabling hosted/containerized audit execution without a PowerShell dependency. This is the foundation for server-side build orchestration.

**Deliverables:**
- `app/audit/runner.py` -- Python implementation of all A1-A9 blocking checks and W1-W3 warnings, matching the logic in `Forge/scripts/run_audit.ps1`
  - A1 Scope compliance (git diff vs claimed files)
  - A2 Minimal-diff discipline (detect renames)
  - A3 Evidence completeness (test_runs_latest.md exists, Status: PASS)
  - A4 Boundary compliance (boundaries.json pattern matching)
  - A5 Diff log gate (updatedifflog.md exists, no TODO placeholders)
  - A6 Authorization gate (no unauthorized commits)
  - A7 Verification hierarchy order (Static, Runtime, Behavior, Contract in order)
  - A8 Test gate (test_runs_latest.md Status: PASS)
  - A9 Dependency gate (imports vs dependency manifest)
  - W1 No secrets in diff
  - W2 Audit ledger integrity
  - W3 Physics route coverage
- `app/audit/runner.py` must accept the same inputs as the PS1 script: claimed files list, phase identifier, and project root path
- CLI entrypoint: `python -m app.audit.runner --claimed-files "file1,file2" --phase "Phase N"` for local/CI use
- Audit runner returns structured results (list of check results with code, name, result, detail) and appends to `evidence/audit_ledger.md`
- `GET /audit/run` -- internal API endpoint to trigger an audit run programmatically (auth: bearer, admin-only or system-internal)
- Tests: one test per check (A1-A9, W1-W3) using fixtures with known-good and known-bad inputs
- Existing `app/audit/engine.py` remains unchanged -- it handles push-triggered repo checks (A4, A9, W1), while `runner.py` handles full governance audits

**Schema coverage:**
- No new tables (runner writes to evidence files and audit_ledger.md)

**Exit criteria:**
- `python -m app.audit.runner` produces identical PASS/FAIL results as `run_audit.ps1` for the same inputs
- All 12 audit checks (A1-A9, W1-W3) have dedicated tests
- CLI entrypoint works from project root
- Internal API endpoint returns structured audit results
- All existing tests still pass
- `run_audit.ps1` passes all checks (self-referential: the PS1 script audits the phase that replaces it)

---

## Phase 8 -- Project Intake & Questionnaire

**Objective:** Users can create a new project by answering a chat-based questionnaire. A lightweight LLM (Haiku) guides the conversation, collects requirements, and generates Forge contract files from templates.

**Deliverables:**
- `projects` table (id, user_id, name, description, status, repo_id FK nullable, created_at, updated_at)
- `project_contracts` table (id, project_id FK, contract_type, content, version, created_at, updated_at)
- Contract templates in `app/templates/contracts/` -- parameterized Markdown/YAML templates for: blueprint, manifesto, stack, schema, physics, boundaries, phases, ui, builder_contract, builder_directive
- `POST /projects` -- create a new project (name, description)
- `GET /projects` -- list user's projects
- `GET /projects/{id}` -- project detail with contract status
- `POST /projects/{id}/questionnaire` -- send a message to the questionnaire chat; Haiku processes the answer and returns the next question or a completion signal
- `GET /projects/{id}/questionnaire/state` -- current questionnaire progress (which sections are complete, which remain)
- `POST /projects/{id}/contracts/generate` -- generate all contract files from completed questionnaire answers; stores in project_contracts table
- `GET /projects/{id}/contracts` -- list generated contracts
- `GET /projects/{id}/contracts/{type}` -- view a single contract
- `PUT /projects/{id}/contracts/{type}` -- edit a contract before build
- Questionnaire flow covers: product intent, tech stack, database schema, API endpoints, UI requirements, architectural boundaries, deployment target, phase breakdown
- Frontend: new "Create Project" button on dashboard, questionnaire chat page, contract review page
- Haiku API integration via `app/clients/llm_client.py` (Anthropic Messages API, model configurable via env var `LLM_QUESTIONNAIRE_MODEL`)

**Schema coverage:**
- [x] users (Phase 1)
- [x] repos (Phase 2)
- [x] audit_runs (Phase 3)
- [x] audit_checks (Phase 3)
- [x] projects
- [x] project_contracts

**Exit criteria:**
- User can create a project and complete the questionnaire via chat
- All required contract sections are collected through the questionnaire
- Contracts generate from templates with user-provided values
- Contracts are viewable and editable before build
- Haiku responses are contextual and guide the user through each section
- All new endpoints have tests (mocked LLM responses)
- All existing tests still pass
- `run_audit.ps1` passes all checks

---

## Phase 9 -- Build Orchestrator

**Objective:** Integrate the Claude Agent SDK to spawn autonomous builder sessions. ForgeGuard manages the full build lifecycle: clone repo, inject contracts, start builder, stream progress, run audits inline, handle loopback, and advance through phases.

**Deliverables:**
- `builds` table (id, project_id FK, phase, status, started_at, completed_at, loop_count, error_detail, created_at)
- `build_logs` table (id, build_id FK, timestamp, source, level, message, created_at) -- captures streaming builder output
- `app/services/build_service.py` -- orchestration layer:
  - Clone target repo (or create fresh repo from project)
  - Write generated contracts into `Forge/` directory
  - Spawn Agent SDK session with builder directive as prompt
  - Stream builder output and store in build_logs
  - Detect phase completion signals from builder output
  - Invoke Python audit runner (Phase 7) inline when builder signals COMPLETE
  - Feed audit results back to builder (PASS: continue, FAIL: loopback items)
  - Enforce loop limit (3 consecutive fails = RISK_EXCEEDS_SCOPE)
  - Track phase progression and overall build status
- `app/clients/agent_client.py` -- Agent SDK wrapper (claude_agent_sdk.query with configurable model, tools, permissions)
- `POST /projects/{id}/build` -- start a build (validates contracts exist, then spawns builder)
- `POST /projects/{id}/build/cancel` -- cancel an active build
- `GET /projects/{id}/build/status` -- current build status (phase, progress, active/idle)
- `GET /projects/{id}/build/logs` -- paginated build logs
- WebSocket extension: broadcast build progress events (phase_start, phase_complete, audit_pass, audit_fail, build_complete, build_error)
- Environment variables: `ANTHROPIC_API_KEY` (for Agent SDK), `LLM_BUILDER_MODEL` (default: claude-opus-4-6)
- Build runs in a background task (asyncio) -- the API endpoint returns immediately, progress streams via WebSocket

**Schema coverage:**
- [x] users (Phase 1)
- [x] repos (Phase 2)
- [x] audit_runs (Phase 3)
- [x] audit_checks (Phase 3)
- [x] projects (Phase 8)
- [x] project_contracts (Phase 8)
- [x] builds
- [x] build_logs

**Exit criteria:**
- Starting a build spawns an Agent SDK session that reads contracts and begins Phase 0
- Builder output streams to build_logs table in real-time
- Phase completion triggers the Python audit runner automatically
- Audit PASS advances to next phase; audit FAIL triggers loopback
- Build can be cancelled mid-execution
- 3 consecutive loopback failures stops the build with RISK_EXCEEDS_SCOPE
- All new endpoints have tests (mocked Agent SDK)
- All existing tests still pass
- `run_audit.ps1` passes all checks

---

## Phase 10 -- Live Build Dashboard

**Objective:** Real-time build progress visualization. Users watch their project being built phase-by-phase with streaming logs, audit results, and phase status indicators.

**Deliverables:**
- Frontend: Build Progress page (`/projects/{id}/build`)
  - Phase progress bar (Phase 0 through N, color-coded: grey=pending, blue=active, green=pass, red=fail)
  - Streaming log viewer (auto-scrolling terminal-style output, color-coded by level)
  - Per-phase audit result cards (A1-A9 checklist with PASS/FAIL badges)
  - Loopback indicator (shows iteration count and fix plan when in loopback)
  - Build summary header (elapsed time, current phase, overall status)
  - Cancel build button with confirmation dialog
- Frontend: Project list view on dashboard (shows all projects with their build status)
- Frontend: Project detail page (`/projects/{id}`) -- overview with links to questionnaire, contracts, build, and connected repo
- WebSocket: build progress events render in real-time (no polling)
- Skeleton loaders for build page during initial data fetch
- Empty states for projects with no builds
- Toast notifications for build completion (PASS or FAIL)
- Mobile-responsive layout for build progress page

**Schema coverage:**
- No new tables (reads from builds, build_logs, projects)

**Exit criteria:**
- Build progress page updates in real-time as the builder works
- Phase transitions are visually clear (progress bar updates)
- Streaming logs appear within 1 second of generation
- Audit results display correctly per phase
- Loopback iterations are visible with fix plans
- Build completion triggers a toast notification
- All empty and loading states render correctly
- All Vitest frontend tests pass
- All existing tests still pass
- `run_audit.ps1` passes all checks

---

## Phase 11 -- Ship & Deploy

**Objective:** Post-build UX -- deployment instructions, build artifacts summary, cost tracking, and final hardening for the hosted build pipeline.

**Deliverables:**
- Frontend: Build Complete page -- shown when all phases pass
  - Deployment instructions (generated from project's stack.md -- e.g., Render setup steps, env vars to configure)
  - Build summary (phases completed, total time, total audit checks, loopback count)
  - Cost estimate (token usage from Agent SDK, approximate API cost)
  - Link to generated repo with all code
  - "Download contracts" button (zip of Forge/ directory)
- `build_costs` table (id, build_id FK, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at)
- Token usage tracking in build_service.py (capture from Agent SDK streaming output)
- `GET /projects/{id}/build/summary` -- complete build summary with cost breakdown
- `GET /projects/{id}/build/instructions` -- generated deployment instructions
- Rate limiting on build endpoints (prevent concurrent builds per user, max builds per hour)
- Input validation on all new API endpoints
- Error handling audit for the full build pipeline (no unhandled exceptions)
- Update `USER_INSTRUCTIONS.md` with project intake, questionnaire, and build workflow documentation
- All tests pass (pytest + Vitest)

**Schema coverage:**
- [x] users (Phase 1)
- [x] repos (Phase 2)
- [x] audit_runs (Phase 3)
- [x] audit_checks (Phase 3)
- [x] projects (Phase 8)
- [x] project_contracts (Phase 8)
- [x] builds (Phase 9)
- [x] build_logs (Phase 9)
- [x] build_costs

**Exit criteria:**
- Build complete page shows deployment instructions tailored to the project's stack
- Cost breakdown is accurate (input/output tokens per phase, total USD estimate)
- Rate limiting prevents abuse of build endpoints
- No unhandled exceptions across the build pipeline
- `USER_INSTRUCTIONS.md` covers the full project creation and build workflow
- All tests pass
- `run_audit.ps1` passes all checks
