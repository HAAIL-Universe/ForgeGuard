# Appendix -- DB-Backed Build Staging

**Objective:** Make build resume/recovery reliable by persisting manifests and generated files in Postgres (Neon) instead of relying on temp directories.

**Schema additions (Render/Neon compatible):**
- `build_phase_manifests`: build_id (uuid), phase_num (int), manifest_json (jsonb), created_at.
- `build_phase_files`: build_id (uuid), phase_num (int), path (text), status (enum pending|written|verified|pushed), content (bytea or text), sha (text), created_at, updated_at. Index on (build_id, phase_num, path) and partial index on status != 'pushed'.

**Write path:**
- After planner emits a manifest, upsert into `build_phase_manifests`.
- Each generated file immediately upserts into `build_phase_files` with content + sha, status `written`; set to `verified` after clean verification; set to `pushed` after successful git push/commit.

**Resume (/start, orphan recovery):**
- First query `build_phase_files` for latest build: if rows exist with status not `pushed`, rehydrate files onto disk (respect sha to avoid overwrites), and derive resume_from_phase from max verified/pushed phase or git log fallback.
- If manifest exists for the active phase, reuse; otherwise regenerate.
- If temp dir is missing, reconstruct workspace from DB rows before continuing.

**Cleanup:**
- After a phase push succeeds, delete or archive that phase's `build_phase_files` rows (keep manifest rows optionally); add a periodic purge window (e.g., 14–30 days) and clean on project deletion.

**Safety/perf notes:**
- Cap stored file size; skip generated/vendor paths (node_modules, __pycache__, hidden dirs); avoid storing secrets; use pooling for Render→Neon connections; compress text if needed.
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

---

## Phase 12 -- Build Target & File Writing

**Objective:** The builder actually writes files. Users choose a build target -- a new GitHub repo, an existing GitHub repo, or a local file path -- and the builder's output is parsed into discrete files and written to that target in real time. This replaces the current "stream text only" behaviour.

**Background:** Currently the builder streams its entire output as raw text chunks. No files are created anywhere. The user's intention was always that builds produce real, usable code in a real repository or directory.

**Deliverables:**

- **Build target selection** (backend + frontend):
  - `build_target_type` enum: `github_new`, `github_existing`, `local_path`
  - `POST /projects/{id}/build` extended: accepts `target_type` and `target_ref` (repo name for new, repo full_name for existing, absolute path for local)
  - For `github_new`: call GitHub API to create an empty repo under the user's account, clone it locally to a temp working directory
  - For `github_existing`: clone the repo to a temp working directory (shallow clone, default branch)
  - For `local_path`: validate the path exists (or create it), use directly as the working directory
  - Store target info on the `builds` table: `target_type`, `target_ref`, `working_dir`

- **DB migration 008** -- `build_targets`:
  - Add columns to `builds`: `target_type VARCHAR(20)`, `target_ref TEXT`, `working_dir TEXT`

- **File parser in build_service.py**:
  - Builder directive updated to instruct the LLM to emit file blocks in a canonical format:
    ```
    === FILE: path/to/file.py ===
    ```python
    <file contents>
    ```
    === END FILE ===
    ```
  - `_parse_file_blocks(accumulated_text)` -- extracts file path + content from builder output
  - When a file block is detected:
    1. Write the file to the working directory on disk
    2. Emit `file_created` WS event with `{ path, size_bytes, language }`
    3. Log the file write to build_logs
  - Handle file updates (same path written twice = overwrite with latest)

- **Git operations** (for GitHub targets):
  - After each phase completes audit, stage + commit all new/modified files with message `"forge: Phase N complete"`
  - After build completes, push all commits to the remote
  - `app/clients/git_client.py` -- thin wrapper around `asyncio.subprocess` for git clone, add, commit, push (no heavy git library dependency)

- **Frontend: Build target picker** (shown before build starts):
  - Three-option card selector: "New GitHub Repo" / "Existing GitHub Repo" / "Local Directory"
  - For new repo: text input for repo name (validated against GitHub naming rules)
  - For existing repo: repo picker modal (reuse existing component)
  - For local: text input for absolute path with validation
  - Selected target stored in build state and sent with `POST /projects/{id}/build`

- **Frontend: File tree panel** on BuildProgress page:
  - New collapsible panel (left column, below phase checklist) showing files created so far
  - Tree structure mirroring directory hierarchy
  - Each file shows: path, size, language icon
  - Updates in real-time via `file_created` WS events
  - Click a file to expand and see a syntax-highlighted preview of its content (read from build logs or a dedicated endpoint)

- **API for file retrieval**:
  - `GET /projects/{id}/build/files` -- list all files written during the build
  - `GET /projects/{id}/build/files/{path}` -- retrieve content of a specific file

**Schema coverage:**
- [x] builds (extended with target columns)
- [x] build_logs (Phase 9)
- [ ] No new tables -- file metadata derived from build_logs + filesystem

**Exit criteria:**
- User can select "New GitHub Repo" and the build creates the repo + writes files into it
- User can select "Existing GitHub Repo" and the build writes files into the cloned repo
- User can select "Local Directory" and files are written to that path
- Files appear in the file tree panel in real-time as the builder creates them
- After each phase audit pass, files are committed with a phase-specific message
- After build completion (GitHub targets), all commits are pushed to the remote
- File content is retrievable via API
- All existing tests still pass + new tests for file parsing, git operations, and target selection
- `run_audit.ps1` passes all checks

---

## Phase 13 -- Multi-Turn Builder & Structured Build Plan

**Objective:** Convert the builder from a single streaming call to a multi-turn conversation. The builder emits a structured plan at the start, and after audit failures the audit feedback is injected back into the conversation so the builder can self-correct. This gives the builder full context of what it's already done, what failed, and why.

**Background:** Currently `_run_build()` creates `messages = [{"role":"user","content": directive}]` and makes one `stream_agent()` call. The audit runs but its feedback is never sent back to the builder. The builder has no ability to self-correct -- it just gets a loop count incremented and the build eventually fails. Real builds took ~7 iterations because the diff log retained TODO placeholders that the auditor kept flagging.

**Deliverables:**

- **Structured build plan emission**:
  - Update builder directive (system prompt / builder_contract.md) to instruct the LLM:
    > Before writing any code, emit a `=== BUILD PLAN ===` block listing all tasks you will perform, grouped by phase. Each task should be a single line: `- [ ] Task description`. End the plan with `=== END BUILD PLAN ===`.
  - `_parse_build_plan(text)` in build_service.py -- extracts task list from the plan block
  - Emit `build_plan` WS event: `{ tasks: [{ id, description, phase, status }] }`
  - As the builder works, detect task completion signals (`=== TASK DONE: <id> ===`) and emit `task_complete` WS events
  - Frontend: Build Plan checklist panel in BuildProgress (right column, above activity feed), showing all tasks with check/pending/fail icons, updating in real-time

- **Multi-turn conversation loop**:
  - Replace single `stream_agent()` call with a conversation loop:
    ```
    messages = [{"role": "user", "content": directive}]
    while not build_complete:
        async for chunk in stream_agent(..., messages=messages):
            # accumulate, parse files, detect signals
        messages.append({"role": "assistant", "content": accumulated_text})
        
        if phase_complete_detected:
            audit_result = await _run_inline_audit(...)
            if audit_result == "PASS":
                # continue to next phase
                messages.append({"role": "user", "content": f"Audit PASSED for {phase}. Proceed to the next phase."})
            else:
                # loopback with audit feedback
                messages.append({"role": "user", "content": f"Audit FAILED for {phase}.\n\nAudit feedback:\n{audit_detail}\n\nFix the issues identified above and re-submit {phase}."})
    ```
  - The conversation grows with each turn -- the builder sees everything it's already produced + audit results
  - No separate database table for "state" -- the conversation history IS the state

- **Audit feedback forwarding**:
  - `_run_inline_audit()` updated to return audit detail (not just PASS/FAIL) -- the full auditor response including specific findings
  - Return type changes from `str` to `AuditResult` dataclass: `{ verdict: str, detail: str, findings: list[str] }`
  - On FAIL: the audit detail is appended to messages so the builder knows exactly what to fix
  - Emit `audit_fail` WS event extended with `{ findings: [...] }` so the frontend can show what failed

- **Context window management**:
  - Track total conversation tokens (input + output across turns)
  - If conversation approaches model context limit (200K for Opus), truncate earlier turns while preserving: (1) original directive, (2) last 2 turns, (3) all audit feedback messages
  - `_compact_messages(messages, max_tokens)` helper for intelligent truncation
  - Log a warning when compaction occurs

- **Builder directive updates**:
  - Update `Forge/Contracts/builder_contract.md` with file block format instructions
  - Add task signal format to builder_directive template
  - Instruct builder to emit `=== PHASE SIGN-OFF: PASS ===` only after ALL tasks for a phase are complete and the code is ready for audit

**Schema coverage:**
- No new tables -- conversation state is in-memory during build execution

**Exit criteria:**
- Builder emits a structured plan at the start of the build
- Plan tasks are shown in the UI and checked off as they're completed
- After an audit failure, the audit feedback is sent back to the builder as a new conversation turn
- The builder successfully self-corrects based on audit feedback (verifiable in test with mocked responses)
- Conversation history grows across turns with full context preservation
- Context compaction triggers when approaching model limits (tested with mocked large conversations)
- All existing tests still pass + new tests for plan parsing, multi-turn loop, audit feedback injection, context compaction
- `run_audit.ps1` passes all checks

---

## Phase 14 -- Build Pause, Resume & User Interjection

**Objective:** When the builder hits a configurable failure threshold on a single phase, the build pauses instead of dying. The user sees what went wrong and can choose to: retry (with or without a message to the builder), skip the phase, or abort. The user can also proactively interject at any time during the build to send guidance to the builder.

**Background:** Currently at `MAX_LOOP_COUNT = 3`, `_fail_build()` fires with "RISK_EXCEEDS_SCOPE" and the build is dead. The user can only start over. With Option D (multi-turn loopback + pause at threshold), the builder auto-corrects for the first N failures (Phase 13). When it still can't pass, it pauses and asks the human for help. The user can also press a button to interject mid-build at any time.

**Deliverables:**

- **Build pause mechanism** (backend):
  - New build status: `paused` (joins existing: `pending`, `running`, `completed`, `failed`, `cancelled`)
  - DB migration 009: add `paused_at TIMESTAMPTZ`, `pause_reason TEXT`, `pause_phase TEXT` to `builds` table
  - When `loop_count >= PAUSE_THRESHOLD` (configurable, default 3) on a single phase:
    1. Set build status to `paused` with reason and phase
    2. Emit `build_paused` WS event: `{ phase, loop_count, audit_findings, options: ["retry", "retry_with_message", "skip_phase", "abort"] }`
    3. The background task enters an `asyncio.Event` wait state (does not exit, preserving conversation context in memory)
  - `PAUSE_THRESHOLD` configurable via env var (default 3), replaces the hard `MAX_LOOP_COUNT` constant

- **Resume endpoints** (backend):
  - `POST /projects/{id}/build/resume` -- resume a paused build
    - Body: `{ "action": "retry" | "skip" | "abort", "message": "optional user message" }`
    - `retry`: append user message (if any) + "Please retry this phase, fixing the issues above." to conversation, set event, builder continues
    - `retry_with_message`: same as retry but user's message is mandatory and is prepended to the retry instruction
    - `skip`: append "Phase skipped by user. Proceed to the next phase." to conversation, advance phase, set event
    - `abort`: call `_fail_build()` with "Build aborted by user at {phase}"
  - The `asyncio.Event` is signalled, the background task wakes up and reads the action

- **Proactive interjection** (backend):
  - `POST /projects/{id}/build/interject` -- send a message to the builder mid-build (while status is `running`)
    - Body: `{ "message": "user guidance text" }`
    - Sets an interjection flag; at the next natural break point (after current chunk batch), the builder pauses streaming, appends the user message to conversation, and resumes with a new `stream_agent()` call that includes the user's guidance
    - Emit `build_interjection` WS event: `{ message, injected_at }`
  - Interjection queue: if multiple messages sent before the builder reaches a break point, they're concatenated into a single injection

- **Frontend: Pause UI** on BuildProgress page:
  - When `build_paused` event received, show a modal/overlay:
    - Header: "Build paused — audit failed {loop_count} times on {phase}"
    - Audit findings list (from the last audit failure)
    - Four action buttons:
      - **Retry** -- "Let the builder try again"
      - **Retry with guidance** -- expands a text input where user types a message, then sends `retry_with_message`
      - **Skip phase** -- "Skip this phase and continue" (with warning that skipped phases may cause issues)
      - **Abort build** -- "Stop the build entirely" (with confirmation dialog)
    - The user's typed message is sent as part of the resume request and injected into the builder's conversation

- **Frontend: Interjection button** on BuildProgress page:
  - Small "Send message to builder" button (or chat input) visible while build is `running`
  - Opens a text input; on submit, calls `POST /projects/{id}/build/interject`
  - Activity feed shows "You: {message}" entry when interjection is sent
  - Activity feed shows "Builder acknowledged interjection" when it's processed

- **Frontend: Paused state styling**:
  - Build status badge shows "Paused" in amber/yellow
  - Phase checklist shows the paused phase with a pause icon
  - Activity feed shows "Build paused — awaiting user input" entry

- **Timeout safeguard**:
  - If a build is paused for longer than 30 minutes (configurable via `BUILD_PAUSE_TIMEOUT_MINUTES`), automatically abort with "Build timed out while paused"
  - Background task uses `asyncio.wait_for(event.wait(), timeout=1800)`

**Schema coverage:**
- [x] builds (extended with pause columns)

**Exit criteria:**
- Build pauses after 3 audit failures on a single phase instead of dying
- Pause UI shows audit findings and four action options
- "Retry" resumes the build and the builder attempts the phase again
- "Retry with guidance" injects the user's message into the builder's conversation before retrying
- "Skip phase" advances to the next phase
- "Abort" stops the build with a clear status message
- Proactive interjection works mid-build -- user can send guidance at any time
- Interjection message appears in the builder's next conversation turn
- Paused build times out after 30 minutes if no action taken
- Activity feed shows all pause/resume/interjection events
- All existing tests still pass + new tests for pause/resume, interjection, timeout, and all action types
- `run_audit.ps1` passes all checks

---

## Phase 15 -- Builder Intelligence Hardening

**Objective:** End-to-end integration testing and refinement of the multi-turn builder pipeline. Validate that the full flow -- target selection → build plan → file writing → audit → self-correction → pause/resume → push -- works reliably. Harden edge cases, tune prompts, and ensure the builder produces genuinely usable code.

**Deliverables:**

- **Integration test suite** (`tests/test_build_integration.py`):
  - Full build lifecycle with mocked agent: target selection → plan emission → file blocks → phase sign-off → audit pass → next phase → completion → git push
  - Audit failure → self-correction loop (mock agent returns fixed output on second turn)
  - Audit failure → pause threshold → user retry with message → builder succeeds
  - Audit failure → pause → user skip → next phase
  - Audit failure → pause → user abort
  - Proactive interjection mid-build → message appears in conversation → builder adjusts
  - Context compaction trigger with large conversation
  - File overwrite (same path written twice)
  - Build target: local path (file writing verified on disk)
  - Build target: GitHub new repo (mocked GitHub API + git operations)

- **Prompt tuning**:
  - Refine builder_contract.md file block format instructions based on real LLM output patterns
  - Refine auditor_prompt.md to reduce false positives (especially the diff log TODO issue)
  - Add examples of correct file block format to the builder directive
  - Tune PAUSE_THRESHOLD default based on testing (may adjust from 3)

- **Edge case hardening**:
  - Builder emits malformed file blocks → graceful fallback (log warning, don't crash)
  - Builder never emits PHASE_COMPLETE_SIGNAL → timeout per phase (configurable, default 10 minutes)
  - Network error during git push → retry with backoff, then pause build with error details
  - Working directory cleanup on build completion/failure/cancellation
  - Concurrent builds per user prevention (existing rate limit enforcement)
  - Large file handling (>1MB file blocks) → warn but still write

- **Observability**:
  - Build summary endpoint enhanced with: total turns, total audit attempts, files written count, git commits made, interjections received
  - Build logs searchable/filterable in the frontend (search bar on activity feed)

- **Documentation**:
  - Update `USER_INSTRUCTIONS.md` with: build target options, how to use interjection, what happens on audit failure, pause/resume flow
  - Update `Forge/Contracts/builder_contract.md` with finalised file block format and plan format

**Exit criteria:**
- All integration tests pass with mocked agent responses
- A real build (manual test) successfully creates a GitHub repo, writes files, passes audits, and pushes
- No crashes on malformed builder output
- Phase timeout triggers correctly
- Git push failures are handled gracefully
- Working directories are cleaned up
- `USER_INSTRUCTIONS.md` is comprehensive and accurate
- All tests pass (backend + frontend)
- `run_audit.ps1` passes all checks
---

## Phase 16 -- Model Upgrade & Per-Phase Planning

**Objective:** Replace all Haiku usage with Sonnet (BYOK) and restructure the builder's planning to operate per-phase rather than as a monolithic upfront plan. The builder emits a detailed task list at the start of each phase and resets between phases.

**Background:** Currently the builder emits one `=== PLAN ===` block at the very start of the build and `plan_tasks` is never reset. This produces a massive, fragile plan that drifts over time. Separately, all "cheap" LLM calls (questionnaire, auditor) use Haiku. Moving to Sonnet improves reasoning quality across the board. All LLM costs become BYOK -- the user's API key pays for everything.

**Deliverables:**

- **Haiku → Sonnet swap**:
  - `app/config.py`: change `LLM_QUESTIONNAIRE_MODEL` default from `claude-haiku-4-5` to `claude-sonnet-4-5`
  - `app/config.py`: add `LLM_PLANNER_MODEL` setting (default: `claude-sonnet-4-5`)
  - `app/services/build_service.py`: update auditor comment and pricing table to include Sonnet rates
  - `web/src/pages/Settings.tsx`: update AI Models display -- questionnaire, auditor, and new planner row all show `claude-sonnet-4-5` with `BYOK` badge. Remove `FREE` badges.
  - `web/src/components/ContractProgress.tsx`: add Sonnet to model context window map (already present but verify)
  - `USER_INSTRUCTIONS.md`: update env var documentation to reflect Sonnet defaults and add `LLM_PLANNER_MODEL`

- **Per-phase plan lifecycle**:
  - At the start of the build, emit a high-level phase overview from the parsed phases contract (already available via `_parse_phases_contract()`). Broadcast as `build_overview` WS event: `{ phases: [{ number, name, objective }] }`
  - Reset `plan_tasks = []` and `accumulated_text = ""` after each phase audit pass (currently only `accumulated_text` resets)
  - Update the builder's system prompt to instruct: "At the start of each phase, emit a `=== PLAN ===` block covering only the current phase's deliverables. Do not plan ahead to future phases."
  - Plan detection (`_parse_build_plan()`) now runs fresh each phase since `plan_tasks` is reset
  - Broadcast `phase_plan` WS event (distinct from `build_plan`) with the per-phase tasks

- **Frontend: Phase overview bar**:
  - New component at the top of BuildProgress page showing all phases as a horizontal step indicator (grey = pending, blue = active, green = passed, red = failed, amber = paused)
  - Derived from the `build_overview` WS event at build start, updated by `phase_complete` / `audit_fail` / `build_paused` events
  - Per-phase task checklist below the overview bar resets when a new `phase_plan` event arrives

- **Tests**:
  - Test that `plan_tasks` resets between phases
  - Test `build_overview` event is emitted at build start with correct phase list
  - Test per-phase plan detection after reset
  - All existing tests updated for Sonnet model references

**Schema coverage:**
- No new tables

**Exit criteria:**
- All LLM calls use Sonnet (no Haiku references remain in code)
- `LLM_PLANNER_MODEL` config is wired up and documented
- Builder emits a fresh plan at the start of each phase (verified in test with mocked multi-phase conversation)
- `plan_tasks` resets between phases -- old tasks don't persist
- Phase overview bar shows all phases and updates correctly
- Settings page shows Sonnet + BYOK for all AI model rows
- All existing tests still pass + new tests for plan reset and overview emission
- `run_audit.ps1` passes all checks

---

## Phase 17 -- Recovery Planner

**Objective:** When an audit fails, instead of sending a generic "please fix these issues" message back to the builder, invoke a separate Sonnet call (the "recovery planner") that analyses the failure against the contracts and current project state, then produces a revised remediation strategy. The builder receives a concrete, alternative approach rather than just being told to try harder.

**Background:** Currently on audit failure, the builder receives: `"The audit for {phase} FAILED (attempt N/3). Please review the audit findings and fix the issues."` The builder retries with the same mental model, often repeating the same structural mistake. The recovery planner provides a second perspective -- it reads the failure, the contracts, and the actual files on disk, then proposes a different strategy.

**Deliverables:**

- **Planner prompt template**: `app/templates/contracts/planner_prompt.md`
  - System prompt for the recovery planner
  - Instructs Sonnet to: analyse the audit findings, compare against contracts, review the current file state, and produce a numbered remediation plan
  - Output format: `=== REMEDIATION PLAN ===\n1. Task...\n=== END REMEDIATION PLAN ===`
  - Rules: must stay within contract boundaries, must not invent new features, must address every audit finding specifically

- **`_gather_project_state()` in `app/services/build_service.py`**:
  - Walks `working_dir` recursively, builds a file tree string
  - Reads contents of key files (up to 200KB total): all `.py` files, all `.ts`/`.tsx` files, config files, migration files
  - Truncates individual files >10KB to first + last 2KB with a `[... truncated ...]` marker
  - Returns a structured string: file tree + file contents

- **`_run_recovery_planner()` in `app/services/build_service.py`**:
  - Parameters: `build_id`, `user_id`, `api_key`, `phase`, `audit_findings`, `builder_output`, `contracts`, `working_dir`, `files_written`
  - Loads `planner_prompt.md` as system prompt
  - Builds user message with: audit findings, relevant contracts, project state from `_gather_project_state()`, the builder's phase output
  - Calls Sonnet via `llm_client.chat()` with `LLM_PLANNER_MODEL`
  - Returns the remediation plan text
  - Logs the planner call to `build_logs` (source: `planner`)
  - Tracks token usage for cost recording

- **Build loop integration** (audit FAIL branch in `_run_build()`):
  - After audit returns FAIL, call `_run_recovery_planner()` with the audit findings
  - Replace the generic feedback message with the planner's remediation plan:
    ```
    f"The audit for {phase} FAILED (attempt {n}/{max}).\n\n"
    f"A recovery planner has analysed the failure and produced a revised strategy:\n\n"
    f"{remediation_plan}\n\n"
    f"Follow this remediation plan to fix the issues and re-submit {phase}."
    ```
  - If the planner call itself fails (API error, timeout), fall back to the existing generic message
  - Broadcast `recovery_plan` WS event: `{ phase, attempt, plan_text }`

- **Cost tracking**:
  - Record planner token usage as a separate cost entry: phase = `"{phase} (planner)"`, model = `LLM_PLANNER_MODEL`
  - Planner costs appear as distinct line items in the build summary

- **Frontend: Recovery plan display**:
  - When `recovery_plan` WS event arrives, show it in the activity feed as a distinct card (amber border, planner icon)
  - The remediation tasks are rendered as a numbered list within the card

- **Tests**:
  - Test `_gather_project_state()` with mock filesystem (correct tree output, file content inclusion, truncation at 10KB)
  - Test `_run_recovery_planner()` with mocked LLM response (correct prompt assembly, result parsing)
  - Test build loop audit-fail branch invokes the planner and injects its output into conversation
  - Test planner API failure falls back to generic feedback message
  - Test planner cost is recorded separately

**Schema coverage:**
- No new tables

**Exit criteria:**
- On audit failure, the recovery planner is called and its output injected into the builder conversation
- The builder receives a specific remediation strategy, not a generic retry message
- Planner prompt includes: audit findings, contracts, current project state, builder output
- Project state gathering walks the working directory and reads file contents
- Planner failure falls back gracefully to the generic feedback message
- Planner costs appear as separate line items in build summary
- Recovery plan appears in the frontend activity feed
- All existing tests still pass + new tests for planner, project state gathering, and fallback
- `run_audit.ps1` passes all checks

---

## Phase 18 -- Builder Tool Use (Foundation)

**Objective:** Enable the builder agent to use tools during its build session. Instead of blindly emitting code, the builder can read files, list directories, and search code within the working directory. This is the foundation for the agentic loop -- the builder becomes aware of what it has already written and can make informed decisions.

**Background:** Currently `stream_agent()` makes a single Anthropic Messages API call with no tools. The builder emits code based solely on the contracts it received at the start and its conversation history. It cannot check what files exist, read their contents, or verify its own output. IDE-based builders (Claude in VS Code) are effective because they have tools. This phase brings that capability to the API-driven builder.

**Deliverables:**

- **Tool definitions**:
  - `read_file` -- read a file from the working directory by relative path; returns file content (truncated at 50KB)
  - `list_directory` -- list files and folders in a directory within the working directory; returns names with `/` suffix for directories
  - `search_code` -- grep for a pattern across the working directory; returns matching file paths and line snippets (max 50 results)
  - `write_file` -- write/overwrite a file at a relative path in the working directory (alternative to file block format; builder can use either)
  - All tools enforce path sandboxing: paths must be within `working_dir`, no `..` traversal, no absolute paths

- **Tool execution engine** (`app/services/tool_executor.py`):
  - `execute_tool(tool_name, tool_input, working_dir) -> str` -- dispatches to the correct handler
  - `_exec_read_file(path, working_dir) -> str` -- reads file, enforces path sandboxing and size limits
  - `_exec_list_directory(path, working_dir) -> str` -- lists directory contents
  - `_exec_search_code(pattern, working_dir) -> str` -- runs grep-like search, returns formatted results
  - `_exec_write_file(path, content, working_dir) -> str` -- writes file, returns confirmation with size
  - All handlers return string results (tool results must be strings for the API)
  - All handlers catch exceptions and return error messages rather than raising

- **`stream_agent()` update in `app/clients/agent_client.py`**:
  - Accept optional `tools` parameter (list of tool definitions in Anthropic format)
  - Handle `tool_use` content blocks in the streaming response:
    1. When a `content_block_start` with `type: "tool_use"` is received, accumulate the tool input JSON
    2. When the `content_block_stop` arrives, yield a special tool-call signal (not raw text)
    3. The caller executes the tool and sends a `tool_result` message
    4. Resume streaming with the tool result appended to messages
  - The streaming loop becomes: stream → detect tool call → pause stream → execute tool → append tool result → continue streaming
  - Return type changes: yield either text chunks or `ToolCall` objects (dataclass with `id`, `name`, `input`)

- **Build loop integration** (`_run_build()` in `app/services/build_service.py`):
  - Define tool specs and pass to `stream_agent()`
  - When a tool call is yielded:
    1. Execute via `tool_executor.execute_tool()`
    2. Log the tool call and result to `build_logs` (source: `tool`)
    3. Broadcast `tool_use` WS event: `{ tool_name, input_summary, result_summary }`
    4. Append the tool result to messages and continue the agent loop
  - When `write_file` tool is used, also track the file in `files_written` and emit `file_created` WS event (same as file block parsing)
  - File block parsing still works as a fallback if the builder emits `=== FILE: ... ===` blocks instead of using the `write_file` tool

- **Builder system prompt update**:
  - Inform the builder that it has tools available: `read_file`, `list_directory`, `search_code`, `write_file`
  - Instruct the builder to use `read_file` to verify its work and `list_directory` to understand the current project state before starting each phase
  - Instruct the builder to use `write_file` for creating files (preferred over file block format)

- **Frontend: Tool use display**:
  - Tool calls appear in the activity feed with a distinct icon (wrench/tool icon)
  - Show tool name, abbreviated input, and abbreviated result
  - Collapsible detail for full tool input/output

- **Tests**:
  - Unit tests for each tool handler in `tool_executor.py` (path sandboxing, size limits, error handling, directory traversal prevention)
  - Test `stream_agent()` tool use flow with mocked API response containing tool_use blocks
  - Test build loop tool call handling (execution, logging, WS broadcast)
  - Test `write_file` tool triggers `file_created` event and `files_written` tracking
  - Test file block parsing still works alongside tool use

**Schema coverage:**
- No new tables

**Exit criteria:**
- Builder can read files, list directories, and search code during a build session
- Builder can write files via the `write_file` tool (alternative to file block format)
- Tool calls are logged to build_logs and broadcast via WebSocket
- Path sandboxing prevents directory traversal outside working_dir
- Tool execution errors are handled gracefully (returned as error strings, don't crash the build)
- File block parsing still works as a fallback
- Tool use appears in the frontend activity feed
- All existing tests still pass + new tests for tool executor, agent tool flow, and build loop integration
- `run_audit.ps1` passes all checks

---

## Phase 19 -- Builder Tool Use (Verification)

**Objective:** Add test execution and error checking tools so the builder can verify its own code before signing off a phase. The builder runs tests, reads failures, fixes issues, and re-runs -- the same workflow a developer uses in an IDE. This dramatically reduces audit failures because the builder self-verifies before requesting audit.

**Background:** Phase 18 gave the builder file awareness (read, write, search). This phase adds execution awareness. Currently the builder emits code and immediately signs off, hoping it works. The auditor then catches structural issues, but cannot run tests. With test execution tools, the builder can verify its own work, fix issues iteratively, and only sign off when tests pass -- just like Claude does in the IDE.

**Deliverables:**

- **New tools**:
  - `run_tests` -- execute the project's test suite (or a subset) in the working directory; returns stdout/stderr with pass/fail summary
    - Input: `{ "command": "pytest tests/test_foo.py -v", "timeout": 120 }`
    - Executes via `asyncio.create_subprocess_exec` with timeout
    - Returns: exit code, stdout (truncated at 50KB), stderr (truncated at 10KB)
    - Security: command must start with an allowed prefix (`pytest`, `python -m pytest`, `npm test`, `npx vitest`); arbitrary commands are rejected
  - `check_syntax` -- run a syntax/lint check on a specific file; returns errors with line numbers
    - Input: `{ "file_path": "app/services/foo.py" }`
    - For Python: uses `py_compile` or `ast.parse` to check syntax
    - For TypeScript/JavaScript: uses `npx tsc --noEmit` on the file
    - Returns: list of errors with file, line, message; or "No errors" if clean
  - `run_command` -- execute a sandboxed shell command in the working directory; returns output
    - Input: `{ "command": "pip install -r requirements.txt", "timeout": 60 }`
    - Allowlist of safe commands: `pip install`, `npm install`, `python -m`, `npx`, `cat`, `head`, `tail`, `wc`, `find`, `ls`
    - Rejects: `rm`, `del`, `curl`, `wget`, `ssh`, `git push`, or any command not on the allowlist
    - Returns: exit code + stdout/stderr (truncated)

- **Tool executor updates** (`app/services/tool_executor.py`):
  - `_exec_run_tests(command, timeout, working_dir) -> str`
  - `_exec_check_syntax(file_path, working_dir) -> str`
  - `_exec_run_command(command, timeout, working_dir) -> str`
  - Command allowlist validation for `run_tests` and `run_command`
  - Timeout enforcement via `asyncio.wait_for` on subprocess
  - Output truncation to prevent context window overflow

- **Builder system prompt update**:
  - Instruct the builder: "After writing code for a phase, ALWAYS run tests before emitting the phase sign-off signal. Use `run_tests` to execute the relevant test files. If tests fail, read the error output, fix the code using `write_file`, and re-run. Only emit `=== PHASE SIGN-OFF: PASS ===` when all tests pass."
  - Instruct the builder: "Use `check_syntax` after writing Python files to catch syntax errors immediately."
  - Instruct the builder: "Use `run_command` for setup tasks like `pip install -r requirements.txt` or `npm install` when needed."

- **Build loop: verification tracking**:
  - Track test runs per phase in build logs (source: `test`)
  - Broadcast `test_run` WS event: `{ command, exit_code, passed, failed, summary }`
  - If the builder runs tests 5+ times on a single phase without all passing, log a warning

- **Frontend: Test run display**:
  - Test results appear in the activity feed with pass/fail badge
  - Green checkmark for all-pass, red X for failures
  - Collapsible detail showing test output

- **Security hardening**:
  - All subprocess execution runs with restricted environment (no access to host env vars beyond PATH)
  - Working directory is enforced as cwd for all subprocess calls
  - File size limits on tool outputs (prevent a test that dumps 100MB of output from crashing the context)
  - Per-tool timeout defaults: `run_tests` 120s, `check_syntax` 30s, `run_command` 60s

- **Tests**:
  - Test `run_tests` with mock subprocess (pass case, fail case, timeout case)
  - Test `check_syntax` with valid and invalid Python files
  - Test `run_command` allowlist enforcement (allowed commands succeed, disallowed commands rejected)
  - Test command injection prevention (semicolons, pipes, backticks in command input are rejected)
  - Test output truncation at size limits
  - Test build loop handles test_run WS events correctly
  - Integration test: builder writes code → runs tests → tests fail → builder fixes → tests pass → sign-off

**Schema coverage:**
- No new tables

**Exit criteria:**
- Builder can run tests during a build session and read the results
- Builder can check syntax on individual files
- Builder can run safe shell commands (install deps, etc.)
- Command allowlisting prevents arbitrary command execution
- Timeout enforcement prevents runaway processes
- Builder self-verifies before phase sign-off (verified in integration test with mocked agent)
- Test runs appear in the frontend activity feed with pass/fail badges
- All command injection vectors are blocked
- All existing tests still pass + new tests for verification tools, security, and self-verification flow
- `run_audit.ps1` passes all checks

---

## Phase 20 -- End-to-End Builder Validation

**Objective:** Full integration testing and prompt refinement of the complete agentic builder pipeline. Validate that the full flow -- per-phase planning → tool-assisted building → self-verification → audit → recovery planning → retry/pause/resume → commit/push -- works reliably end-to-end. Harden edge cases, tune prompts, and ensure the builder produces genuinely usable, tested code.

**Background:** Phases 16-19 introduced per-phase planning, recovery planning, tool use, and self-verification as individual features. This phase validates they all work together as a cohesive system and tunes the prompts to produce optimal builder behaviour.

**Deliverables:**

- **Integration test suite** (`tests/test_builder_e2e.py`):
  - Full build lifecycle with mocked agent: per-phase plan → tool calls (read_file, write_file, list_directory) → run_tests → phase sign-off → audit pass → next phase plan → completion
  - Self-verification loop: builder writes code → runs tests → tests fail → builder reads error → fixes code → re-runs → passes → signs off
  - Recovery planner invocation: audit fail → planner produces remediation → builder follows remediaton → audit pass
  - Recovery planner failure fallback: planner API error → generic feedback message used → builder retries
  - Pause + resume with recovery: multiple audit failures → pause → user retries with message → recovery planner produces new strategy → builder succeeds
  - Tool error handling: tool returns error → builder adapts (doesn't crash)
  - Large project simulation: 50+ files written across 5+ phases, context compaction triggers, plans reset correctly
  - Security: builder attempts path traversal in tool calls → sandboxing blocks it
  - Concurrent tool calls: builder issues multiple tool calls in one response → all executed correctly

- **Prompt tuning**:
  - Refine builder system prompt based on observed agent behaviour with tools
  - Refine recovery planner prompt to produce more actionable remediation strategies
  - Add few-shot examples to the builder directive showing correct tool use patterns
  - Tune when the builder should use `write_file` tool vs file block format (prefer tools, fall back to blocks)
  - Ensure the builder consistently runs tests before sign-off (not just sometimes)

- **Edge case hardening**:
  - Tool call with missing/malformed input → return descriptive error, not crash
  - Builder emits both tool calls and file blocks in same response → handle both correctly
  - Builder calls `write_file` for a path it already wrote → overwrite correctly, update `files_written`
  - Test runner produces binary output → handle encoding gracefully
  - Working directory runs out of disk space → catch OSError, pause build with error details
  - Builder never calls `run_tests` → auditor catches untested code (existing behaviour, validate it works)

- **Observability enhancements**:
  - Build summary endpoint enhanced with: tool calls count (by type), test runs count (pass/fail), recovery planner invocations, files read by builder
  - Build logs include tool call duration (how long each tool execution took)

- **Documentation**:
  - Update `USER_INSTRUCTIONS.md` with: tool-assisted build explanation, what the builder can do during a session, security model for tool execution
  - Update `Forge/Contracts/builder_contract.md` with: tool use instructions, self-verification requirements, per-phase planning format

**Schema coverage:**
- No new tables

**Exit criteria:**
- All integration tests pass with mocked agent responses simulating multi-phase, tool-assisted builds
- Builder consistently self-verifies (runs tests) before phase sign-off in integration tests
- Recovery planner produces actionable remediations that lead to successful retries in tests
- No crashes on malformed tool calls, binary output, or disk errors
- Tool call security (sandboxing, allowlisting) holds under adversarial test inputs
- Build summary includes complete observability data (tool calls, test runs, planner invocations)
- `USER_INSTRUCTIONS.md` and `builder_contract.md` are updated and accurate
- All tests pass (backend + frontend)
- `run_audit.ps1` passes all checks

---

## Phase 21 -- Plan-Then-Execute: Independent File Generation

**Objective:** Replace the single long multi-turn conversation loop with a **plan-then-execute** architecture where each file is generated by an independent, short-lived API call. This eliminates the quadratic context growth that causes compaction at turn 5-6, slashes token waste from ~300K input / ~3K output per phase to ~40K input / ~4K output per file, and produces a balanced in/out token ratio comparable to the questionnaire/contract generation flow.

**Background — why the current architecture fails:**

The current `_run_build()` loop uses a single Anthropic Messages API conversation. Every API call resends the **entire conversation history** — system prompt + all previous user/assistant messages + all tool call inputs/outputs. This means:

1. **Quadratic growth**: Turn 1 sends 33K. Turn 2 sends 33K + Turn 1's content (~10K). Turn 3 sends 33K + Turns 1-2 (~25K). By turn 5, the conversation is 80K+ tokens. By turn 6, it hits the 150K compaction threshold.
2. **Tool results are the killer**: A single `read_file` returning 200 lines costs ~3K tokens. That 3K is **permanent** — resent in every subsequent API call at full price. The builder reads 5 files exploring the workspace and that's 15K tokens added to every future call forever.
3. **Output is tiny**: The builder spends most turns doing tool calls (short JSON input/output), not generating large files. Ratio: 300K input to 3K output.
4. **Compaction destroys context**: When `_compact_conversation()` fires, it replaces the middle of the conversation with a summary. The builder loses track of what it wrote and starts exploring again — consuming more turns, inflating context further, triggering more compactions.
5. **The fundamental mismatch**: This architecture is designed for **exploration** (debugging, investigating unknowns). Greenfield code generation from contracts is NOT exploration — we already know exactly what to build. The contracts are the complete specification.

**Why the questionnaire works but the builder doesn't**: The questionnaire/contract flow has no tools, no accumulating history, and dense output (the model generates full documents per turn). The builder has the opposite profile: heavy tool use, accumulating history, and sparse output. The fix is to make the builder look like the questionnaire: **each file is its own self-contained API call that generates dense output**.

**How IDE-based builders solve this**: Cursor, Windsurf, and Copilot agent mode do NOT use a single long conversation. They use:
- **Independent file generation**: Each file gets its own API call with just the relevant context
- **Plan → Execute separation**: One call produces a plan; execution calls are independent
- **Context selection, not accumulation**: Each call receives only what's relevant, not the full history

---

### Deliverables

#### 21.1 — Planning Call (File Manifest Generation)

A single API call that takes the contracts and produces a structured file manifest — the complete list of files to create, with metadata, ordered by dependency.

- **New method: `_generate_file_manifest()`** in `build_service.py`
  - Input: `contracts` dict, `current_phase` (Phase object with deliverables), `workspace_info` (existing file listing)
  - Makes ONE API call to `LLM_PLANNER_MODEL` (Sonnet) — NOT Opus. Sonnet is cheaper, faster, and planning doesn't need Opus-level reasoning.
  - System prompt instructs: "You are a build planner. Given the project contracts and the current phase deliverables, produce a structured JSON manifest of every file that must be created or modified for this phase."
  - Output format (strict JSON, parsed by the orchestrator):
    ```json
    {
      "phase": "Phase 0",
      "files": [
        {
          "path": "app/config.py",
          "action": "create",
          "purpose": "Application configuration with env var loading",
          "depends_on": [],
          "context_files": [],
          "estimated_lines": 45,
          "language": "python"
        },
        {
          "path": "app/main.py",
          "action": "create",
          "purpose": "FastAPI application entry point with /health endpoint",
          "depends_on": ["app/config.py"],
          "context_files": ["app/config.py"],
          "language": "python"
        },
        {
          "path": "tests/test_health.py",
          "action": "create",
          "purpose": "Health endpoint test",
          "depends_on": ["app/main.py"],
          "context_files": ["app/main.py"],
          "language": "python"
        }
      ]
    }
    ```
  - `depends_on`: files from THIS manifest that must be written first (ordering constraint)
  - `context_files`: files the builder should read when generating this file (already-written files or existing workspace files that inform this one)
  - Topological sort on `depends_on` to determine generation order
  - Emit `file_manifest` WS event: `{ phase, files: [{path, purpose, status: "pending"}] }`
  - Store manifest in `build_manifests` list for the current phase

- **Planning prompt template**: `app/templates/contracts/planner_build_prompt.md`
  - Includes: full contracts (except phases — only current phase), schema.md, blueprint.md, stack.md
  - Instructs the planner to:
    - List ALL files needed for the phase's deliverables
    - Include test files alongside implementation files
    - Include config/migration files
    - Order by dependency (foundational files first, dependent files later)
    - Estimate line counts to help the orchestrator anticipate API cost
    - Mark `context_files` — which already-written files should be provided as context when generating each file
  - Validate JSON output with `json.loads()` — retry once on parse failure before falling back to the old conversation loop

- **Manifest validation**:
  - Every file must have a `path`, `action`, `purpose`
  - Paths must be within the working directory (same sandboxing as tool_executor)
  - Duplicate paths rejected
  - Circular `depends_on` references detected (topological sort will fail) — fallback to linear order

#### 21.2 — Per-File Generation Calls

Each file in the manifest is generated by an **independent** API call. No conversation history carries over between files.

- **New method: `_generate_single_file()`** in `build_service.py`
  - Input: `file_entry` (from manifest), `contracts` (relevant subset), `context_files` (dict of path→content for files this one depends on), `phase_deliverables` (text), `system_prompt`
  - Makes ONE API call to `LLM_BUILDER_MODEL` (Opus) with:
    - **System prompt** (~2K tokens): Concise builder instructions — "You are writing a single file. Output ONLY the file content. No explanation, no markdown fences, no preamble."
    - **User message** (~5-30K tokens, depending on context):
      ```
      ## Project contracts (reference)
      {relevant_contracts — NOT all contracts, only the ones relevant to this file type}
      
      ## Current phase deliverables
      {phase_deliverables — just the bullet points for this phase}
      
      ## File to write
      Path: {path}
      Purpose: {purpose}
      Language: {language}
      
      ## Context files (already written — DO NOT rewrite these, reference them)
      ### app/config.py
      {content of config.py}
      
      ### app/models.py
      {content of models.py}
      
      ## Instructions
      Write the complete content of {path}. Output ONLY the raw file content.
      Do not wrap in markdown code fences. Do not add explanation before or after.
      ```
  - The API response IS the file content — no parsing needed, no tool calls, no conversation loop
  - Input: ~5-35K tokens (system prompt + relevant contracts + context files)
  - Output: ~500-5K tokens (the actual file content — DENSE output)
  - **Ratio: roughly 1:7 input-to-output instead of 100:1**
  - `max_tokens`: Calculated from `estimated_lines * 15` (avg ~15 tokens/line), minimum 4096, maximum 16384
  - **NO tools provided** — the model just generates code, it doesn't explore
  - Uses prompt caching on the system prompt (same prefix across all files in a phase)

- **Per-file execution flow**:
  1. Resolve context: read `context_files` from disk (already-written files) — these are the files listed in the manifest's `context_files` field
  2. Select relevant contracts: not ALL contracts, just the ones useful for this file type:
     - Python backend file → blueprint.md, schema.md, stack.md, boundaries.json
     - React frontend file → ui.md, blueprint.md, stack.md
     - Test file → same as its target file + the target file's content
     - Migration file → schema.md
     - Config file → stack.md, boundaries.json
  3. Build the user message with context + contracts + instructions
  4. Make ONE `stream_agent()` call (no tools, no tool definitions)
  5. Accumulate the full response text
  6. Write to disk via `tool_executor._exec_write_file(path, content, working_dir)`
  7. Git add + commit (batch — not push yet)
  8. Emit `file_generated` WS event: `{ path, size_bytes, language, tokens_in, tokens_out }`
  9. Update manifest status: `"pending"` → `"done"`
  10. Return the file content (so it can be used as context for subsequent files)

- **Error handling per file**:
  - API error (rate limit, 500, etc.) → retry with backoff (existing logic in `stream_agent`)
  - Empty response → retry once with an appended "Please write the complete file content"
  - Response wrapped in markdown fences → strip the fences automatically (`re.sub(r'^```\w*\n|\n```$', '', content)`)
  - Malformed/short response (<10 lines for a file estimated at 100+) → log warning, retry once with expanded context

- **Concurrency**: Files without mutual dependencies CAN be generated in parallel (e.g., two unrelated utility files). However, start with **sequential** execution for simplicity and correctness. Parallel generation is a future optimisation (Phase 22+) gated by rate limits.

#### 21.3 — Post-Generation Verification

After all manifest files are written, run verification before requesting audit.

- **New method: `_verify_phase_output()`** in `build_service.py`
  - Runs `check_syntax` on all generated Python files (via `tool_executor._exec_check_syntax`)
  - Runs `check_syntax` on all generated TypeScript/JavaScript files
  - If any syntax errors found:
    - Makes a **targeted fix call** — a single API call with: the broken file + the error message + "Fix the syntax errors. Output the corrected file."
    - Writes the fixed content
    - Re-runs syntax check to confirm fix
    - Max 2 fix attempts per file before moving on
  - Runs `run_tests` if test files were generated:
    - `pytest tests/test_*.py -x -q` for Python
    - `npx vitest run` for frontend
  - If tests fail:
    - Makes a **targeted fix call** with: the failing test file + the implementation file + the error output + "Fix the code so the test passes"
    - Writes the fix, re-runs the specific failing test
    - Max 2 fix attempts per test before moving on
  - Emit `verification_result` WS event: `{ syntax_errors: int, tests_passed: int, tests_failed: int, fixes_applied: int }`

- **Fix calls use the same pattern as generation calls** — short, independent, no conversation history. Input: broken file + error + instruction. Output: fixed file content.

#### 21.4 — Phase Orchestration (Replacing the Conversation Loop)

The main `_run_build()` method is restructured from a conversation loop to a phase-driven orchestrator.

- **New `_run_build()` flow** (replaces the current `while True` conversation loop):
  ```
  for each phase in phases:
      1. manifest = await _generate_file_manifest(contracts, phase, workspace_info)
      2. for each file in topological_order(manifest.files):
             context = {f: read_file(f) for f in file.context_files if f is already written}
             content = await _generate_single_file(file, contracts, context, phase)
             write_to_disk(file.path, content)
             git_add(file.path)
             written_files[file.path] = content
      3. git_commit(f"forge: Phase {N} files generated")
      4. verification = await _verify_phase_output(phase, manifest, working_dir)
      5. if verification has failures:
             # targeted fixes (up to 2 per file, short independent calls)
      6. audit_result = await _run_inline_audit(phase, contracts, accumulated_output)
      7. if audit PASS:
             git_push()
             advance to next phase
      8. if audit FAIL:
             recovery_plan = await _run_recovery_planner(...)
             # Re-manifest: ask the planner what files need to change
             fix_manifest = await _generate_fix_manifest(recovery_plan, current_files)
             for each fix_file in fix_manifest:
                 content = await _generate_single_file(fix_file, ..., error_context=recovery_plan)
                 write_to_disk + commit
             re-audit (max PAUSE_THRESHOLD attempts)
  ```

- **Key difference from current loop**: There is NO accumulating conversation. Each file generation is a fresh API call. The "memory" is the **filesystem** — files already written to disk serve as context for subsequent files, not conversation history.

- **Phase state tracking**:
  - `phase_state` dataclass: `{ phase_num, manifest, files_generated, files_fixed, verification_result, audit_attempts }`
  - Stored in `build_phases` list on the build record (not in messages)
  - Used for resume after pause: if build is paused and resumed, the orchestrator knows exactly which files were already generated and picks up from there

- **Backward compatibility**: The old conversation loop is NOT deleted immediately. Add a feature flag `BUILD_MODE` env var:
  - `BUILD_MODE=plan_execute` (new default): Uses the plan-then-execute architecture
  - `BUILD_MODE=conversation` (legacy): Uses the existing conversation loop
  - This allows A/B comparison and safe rollback during testing

#### 21.5 — Context Budget Calculator

Precisely calculate how much context to send per file to stay well within limits and maximise cache hits.

- **New utility: `_calculate_context_budget()`** in `build_service.py`
  - Input: `file_entry`, `system_prompt_tokens`, `contract_tokens`, `available_context_files`
  - Model context window: 200K tokens for Opus
  - Reserved for output: `max_tokens` (up to 16K)
  - Reserved for safety margin: 5K tokens
  - Available for input: `200K - max_tokens - 5K = ~179K` (worst case)
  - System prompt: ~2K tokens (fixed, cached)
  - Contracts: ~5-15K tokens depending on which are included (partially cached — same contracts across files)
  - **Remaining for context files**: `179K - 2K - 15K = ~162K tokens`
  - If context files exceed budget: prioritise by relevance (direct dependencies first, then transitive), truncate the largest files
  - Return: `{ files_to_include: list, contracts_to_include: list, max_tokens: int }`

- **Cache optimisation**:
  - System prompt is identical across all files in a phase → 100% cache hit after file 1
  - Contracts subset is the same for files of the same type (all Python backend files get the same contracts) → high cache hit rate
  - Context files vary per file → no caching, but they're small (already-written project files, not 150K conversation histories)
  - **Expected cache rate: 60-80%** (vs current 49% that degrades as conversation grows)

#### 21.6 — Manifest-Aware WS Events & Frontend

Update the BuildProgress frontend to display the plan-then-execute flow.

- **New WS events**:
  - `file_manifest`: `{ phase, files: [{path, purpose, status, language, estimated_lines}] }` — emitted after planning call
  - `file_generating`: `{ path }` — emitted when a file generation call starts
  - `file_generated`: `{ path, size_bytes, language, tokens_in, tokens_out, duration_ms }` — emitted on completion
  - `file_fix_attempt`: `{ path, error_type, attempt }` — emitted when a fix call starts
  - `verification_result`: `{ syntax_errors, tests_passed, tests_failed, fixes_applied }` — after verification pass

- **Frontend: File manifest panel** (replaces or augments the existing file tree):
  - Shows the planned files as a checklist at the start of each phase
  - Each file shows: path, purpose (tooltip), status icon (pending → generating spinner → done checkmark / error X)
  - Progress bar: `{completed} / {total} files generated`
  - Token usage per file (small, non-intrusive) — clicking shows in/out breakdown

- **Frontend: Phase cost summary**:
  - After each phase completes, show: total input tokens, total output tokens, estimated cost, time elapsed
  - Compare with previous phases to show efficiency trend
  - Target metric displayed: "Input/Output ratio: 5:1" (vs current 100:1)

#### 21.7 — Contract Relevance Mapping

Not all contracts are useful for all files. Sending everything wastes tokens.

- **New method: `_select_contracts_for_file()`** in `build_service.py`
  - Input: `file_entry` (path, language, purpose), `contracts` dict
  - Returns: subset of contracts relevant to this file
  - Mapping rules:
    | File type | Contracts included |
    |-----------|-------------------|
    | Python backend (`app/**/*.py`) | `blueprint`, `schema`, `stack`, `boundaries`, `builder_contract` |
    | Python test (`tests/**/*.py`) | `blueprint`, `schema`, `stack` + the target file's implementation contract context |
    | React component (`web/src/components/*.tsx`) | `ui`, `blueprint`, `stack` |
    | React page (`web/src/pages/*.tsx`) | `ui`, `blueprint`, `stack`, `schema` (for API types) |
    | SQL migration (`db/**/*.sql`) | `schema` only |
    | Config files (`*.json`, `*.yaml`, `.env*`) | `stack`, `boundaries` |
    | Documentation (`*.md`) | `blueprint`, `manifesto` |
    | Catch-all | `blueprint`, `stack` |
  - Total contract tokens per file: ~5-15K (vs current 33K cached prefix with ALL contracts)

#### 21.8 — Audit Feedback → Fix Manifest Loop

When an audit fails, the recovery planner produces a remediation plan. Instead of injecting it into a conversation, convert it to a **fix manifest** — a list of specific files to regenerate or patch.

- **New method: `_generate_fix_manifest()`** in `build_service.py`
  - Input: `recovery_plan` (from `_run_recovery_planner()`), `existing_files` (current file contents from disk), `audit_findings` (specific failures)
  - Makes ONE API call to Sonnet (planner model):
    - "Given these audit findings and this recovery plan, produce a JSON manifest of files that need to be created or modified to fix the issues."
  - Output format:
    ```json
    {
      "fixes": [
        {
          "path": "app/main.py",
          "action": "modify",
          "reason": "Missing input validation on /repos endpoint",
          "context_files": ["app/main.py", "app/config.py"],
          "fix_instructions": "Add Pydantic model validation for the request body"
        }
      ]
    }
    ```
  - For `"action": "modify"`: the generation call receives the current file content + the fix instructions
  - For `"action": "create"`: standard generation call with context
  - Each fix is an independent API call — no accumulating conversation

- **Fix generation prompt** (for modifications):
  ```
  ## Current file content
  {existing content of the file}

  ## Fix required
  {fix_instructions from the fix manifest}

  ## Audit finding
  {specific audit finding this fix addresses}

  ## Instructions
  Output the COMPLETE corrected file content. Apply the fix while preserving all existing functionality.
  ```

- **Max fix iterations per phase**: `PAUSE_THRESHOLD` (default 3). Each iteration:
  1. Generate fix manifest from recovery plan
  2. Execute fixes (independent per-file calls)
  3. Re-verify (syntax + tests)
  4. Re-audit
  5. If still failing → next iteration with updated recovery plan
  6. If max reached → pause for user input (existing pause mechanism)

#### 21.9 — Token Usage Projections

Provide concrete expected improvements to validate after implementation.

- **Current architecture** (single conversation loop):
  - Phase 0 (6 files): ~300K input, ~3K output, ~$4.60, compaction at turn 5-6
  - Cache rate: starts at 49%, degrades as conversation grows
  - Effective ratio: **100:1 input:output**
  - Files produced: 1-2 before looping

- **Plan-then-execute architecture** (projected):
  - Planning call: ~40K input (Sonnet), ~2K output → ~$0.12
  - Per-file generation (6 files × ~35K input, ~3K output each): ~210K total input, ~18K total output → ~$1.60
  - Verification calls (2 fixes estimated): ~30K input, ~4K output → ~$0.25
  - Audit call: ~40K input (Sonnet), ~2K output → ~$0.12
  - **Phase 0 total: ~320K input, ~26K output, ~$2.10**
  - Cache rate: ~70% (system prompt + contracts cached across all 6 file calls)
  - With caching: **~$1.00-1.50 per phase**
  - Effective ratio: **12:1 input:output** (vs 100:1 currently)
  - No compaction needed — each call is self-contained at ~35K tokens max

- **Tracking**: Add `build_mode` field to `build_costs` table to compare `"conversation"` vs `"plan_execute"` costs for the same phase types.

#### 21.10 — Migration & Backward Compatibility

- **Feature flag**: `BUILD_MODE` env var (`plan_execute` | `conversation`, default: `plan_execute`)
- **DB migration 011**: Add `build_mode VARCHAR(20) DEFAULT 'plan_execute'` to `builds` table
- **Old code preserved**: The existing `_run_build()` conversation loop is renamed to `_run_build_conversation()` and kept as the `BUILD_MODE=conversation` path
- **New code**: `_run_build_plan_execute()` implements the new flow, called by `_run_build()` when `BUILD_MODE=plan_execute`
- **`_run_build()` becomes a dispatcher**:
  ```python
  async def _run_build(self, ...):
      if settings.BUILD_MODE == "plan_execute":
          await self._run_build_plan_execute(...)
      else:
          await self._run_build_conversation(...)
  ```
- **Settings UI**: Add `BUILD_MODE` toggle (Plan/Execute vs Conversation) with explanation tooltip
- **All existing WS events still emitted** — the frontend works with both modes. New events (`file_manifest`, `file_generated`, etc.) are additive.

#### 21.11 — Tests

- **Unit tests**:
  - `test_generate_file_manifest()`: Mocked Sonnet call returns JSON manifest; verify topological sort, validation, WS event emission
  - `test_generate_file_manifest_invalid_json()`: Planner returns invalid JSON → retry once → fall back to conversation mode
  - `test_generate_file_manifest_circular_deps()`: Circular `depends_on` → fallback to linear order
  - `test_generate_single_file()`: Mocked Opus call returns file content; verify write to disk, git add, WS event
  - `test_generate_single_file_fenced_output()`: Response wrapped in markdown fences → stripped correctly
  - `test_generate_single_file_empty_response()`: Empty response → retry once
  - `test_generate_single_file_short_response()`: Response much shorter than estimated → retry with expanded context
  - `test_select_contracts_for_file()`: Verify correct contract subset for each file type (Python backend, React component, SQL migration, etc.)
  - `test_calculate_context_budget()`: Verify budget calculation with various file sizes; verify truncation when context exceeds budget
  - `test_verify_phase_output()`: Syntax check + test run with mocked subprocesses; verify fix calls for failures
  - `test_generate_fix_manifest()`: Recovery plan → JSON fix manifest; verify fix generation calls
  - `test_build_mode_dispatch()`: Verify `BUILD_MODE` flag routes to correct implementation

- **Integration tests**:
  - `test_plan_execute_full_phase()`: Plan → generate 5 files → verify → audit pass → push (all mocked API calls)
  - `test_plan_execute_audit_fail_and_fix()`: Generate → audit fail → recovery plan → fix manifest → fix files → re-audit pass
  - `test_plan_execute_pause_after_max_fixes()`: Generate → audit fail × 3 → pause → user retry → succeed
  - `test_plan_execute_context_files_flow()`: File B depends on File A → File A content is included in File B's generation context
  - `test_plan_execute_cost_comparison()`: Verify total tokens are significantly lower than conversation mode for the same phase

- **Frontend tests**:
  - File manifest panel renders with correct statuses
  - File generation progress updates in real-time
  - Phase cost summary displays correct data
  - Verification result badge shows pass/fail counts

**Schema coverage:**
- [x] builds (extended with `build_mode` column)
- No new tables — manifests are ephemeral (in-memory during build, logged to build_logs)

**Exit criteria:**
- Planning call produces a valid, topologically-sorted file manifest from contracts + phase deliverables
- Each file is generated by an independent API call with only relevant context (no conversation history)
- Generated files are written to disk and committed to git
- Syntax verification catches and fixes errors before audit
- Test verification runs tests and fixes failures before audit
- Audit failure triggers recovery planner → fix manifest → independent fix calls (no conversation loop)
- Total token usage per phase is at least 5x more efficient than conversation mode (measured via `build_costs`)
- Input/output token ratio is below 20:1 (vs current 100:1)
- Cache hit rate is above 60% (vs current ~49% that degrades)
- No context compaction is ever needed (each call is self-contained at <50K tokens)
- `BUILD_MODE` feature flag correctly switches between old and new architectures
- All existing tests still pass + all new unit/integration/frontend tests pass
- `run_audit.ps1` passes all checks

---

## Phase 22 -- Headless IDE Runtime: Tool Contract & Scaffold

**Objective:** Define the strict input/output JSON schema for every IDE tool, create the `app/ide/` package structure, and establish the dispatch mechanism. This is the foundation for deterministic autonomy — every tool call and result is structured, validated, and predictable.

**Background — why a headless IDE:**

The plan-execute architecture (Phase 21) eliminated the quadratic context growth problem, but the builder still operates blind: in plan-execute mode it has **zero runtime tools** — it's a one-shot text generator that can't read existing code, can't search, can't run diagnostics, and can't verify its own output. Verification and audit happen as post-hoc batch steps where the auditor *guesses* whether things work because it can't actually inspect file contents.

A headless IDE gives the builder and auditor structured, deterministic tools for reading, searching, patching, running, and diagnosing code — without a user interface. Every tool returns structured JSON (never raw strings), every invocation is logged, and every output is stable and reproducible.

**What exists today vs what the IDE provides:**

| Capability | Today | After IDE |
|-----------|-------|-----------|
| File reading | Raw strings (conversation mode only, none in plan-execute) | Structured JSON with line ranges, language, encoding |
| Code search | Basic regex, 50-result cap, raw text output | Ripgrep-powered, structured matches with context lines |
| File writing | Full-file rewrites only | Unified diff patches for modifications, full writes for creates |
| Verification | Post-hoc `ast.parse` + pytest batch | Inline diagnostics via pyright/ruff, parsed test results |
| Audit insight | LLM guessing from file list | Structured diagnostics, test results, import validation |
| Context assembly | Raw file dumps with char-based budget | Intelligent context packs with relevance scoring |
| Command execution | Raw stdout strings | Structured JSON with deterministic log parsing |
| Audit trail | Build logs (text) | Per-operation structured records in DB |

---

### Deliverables

#### 22.1 — Package scaffold

- `app/ide/__init__.py` — package docstring, public API exports
- `app/ide/contracts.py` — Pydantic models for every tool request/response:
  - `ToolRequest(name: str, params: dict)` → `ToolResponse(success: bool, data: dict, error: str | None, duration_ms: int)`
  - Response `data` is always structured JSON — never raw strings
  - Standard response fields (only populated when relevant to the tool):
    - `paths: list[str]`
    - `line_ranges: list[LineRange]`
    - `snippets: list[Snippet]`
    - `diffs: list[UnifiedDiff]`
    - `exit_codes: list[int]`
    - `diagnostics: list[Diagnostic]`
  - Shared sub-models: `LineRange(start, end)`, `Snippet(path, start_line, end_line, content)`, `Diagnostic(file, line, column, message, severity, code)`, `UnifiedDiff(path, hunks, insertions, deletions)`

#### 22.2 — Tool registry

- `app/ide/registry.py` — tool registry mapping tool names to async handler functions
  - `Registry.register(name, handler, request_schema, response_schema)` — registers a tool with strict schema validation
  - `Registry.dispatch(name, params, workspace) → ToolResponse` — validates input against schema, calls handler, validates output against schema, measures duration
  - `Registry.list_tools() → list[ToolDefinition]` — returns Anthropic-compatible tool definitions for LLM calls
  - Schema validation on both input and output — malformed results are caught early, not silently passed to the LLM

#### 22.3 — Error hierarchy

- `app/ide/errors.py` — typed error classes:
  - `IDEError(Exception)` — base class
  - `SandboxViolation(IDEError)` — path outside workspace
  - `ToolTimeout(IDEError)` — command exceeded timeout
  - `ParseError(IDEError)` — failed to parse tool output
  - `PatchConflict(IDEError)` — diff hunk doesn't match target
  - `ToolNotFound(IDEError)` — unknown tool name

#### 22.4 — Backward compatibility layer

- Define contracts for all 7 existing tools (`read_file`, `write_file`, `list_directory`, `search_code`, `run_tests`, `check_syntax`, `run_command`) in the new schema
- Create adapter functions that wrap existing `tool_executor.py` handlers and return `ToolResponse` objects
- Existing call sites continue to work unchanged — the new registry calls the same underlying handlers but wraps results in structured responses

#### 22.5 — Tests

- Schema validation (valid + invalid inputs/outputs)
- Registry dispatch (happy path, unknown tool, schema mismatch)
- Error hierarchy (each error type, serialisation)
- Backward compat (all 7 existing tools through the new registry)

**Exit criteria:**
- Every tool has a Pydantic request and response model
- The registry dispatches by name with validated input/output
- Existing tools work through the compat layer with identical behaviour
- All new + existing tests pass

---

## Phase 23 -- Headless IDE Runtime: Workspace & Sandbox Primitives

**Objective:** Centralise workspace management — root enforcement, safe path resolution, git operations, and workspace metadata caching. Currently path sandboxing is scattered across `tool_executor.py` with no shared workspace concept.

---

### Deliverables

#### 23.1 — Workspace class

- `app/ide/workspace.py` — `Workspace` class:
  - Constructor: `Workspace(root_path: str | Path)` — validates the root exists and is a directory
  - `resolve(relative_path: str) → Path` — safe path resolution with sandbox enforcement. Replaces `_resolve_sandboxed` from tool_executor.py. Rejects absolute paths, `..` traversal, symlink escapes, and paths outside `root_path`.
  - `file_tree(ignore_patterns: list[str] | None = None) → list[FileEntry]` — recursive directory listing respecting `.gitignore` + configurable ignores (`.venv`, `node_modules`, `__pycache__`, `.git`). Result cached with TTL (default 30s), invalidated on file write.
  - `workspace_summary() → WorkspaceSummary` — `{file_count, total_size_bytes, languages: dict[str, int], last_modified: datetime}`. Cached with TTL.
  - `is_within(path: str | Path) → bool` — constant-time sandbox check (no I/O)
  - `FileEntry` model: `{path: str, is_dir: bool, size_bytes: int, language: str, last_modified: datetime}`

#### 23.2 — Structured git operations

- `app/ide/git_ops.py` — wraps existing `app/clients/git_client.py` but returns structured `ToolResponse` objects:
  - `git_clone(url, dest, branch?, shallow?) → ToolResponse{data: {path, branch, commit}}`
  - `git_init(path) → ToolResponse{data: {path, branch}}`
  - `git_branch_create(name) → ToolResponse{data: {name, from_ref}}`
  - `git_branch_checkout(name) → ToolResponse{data: {name}}`
  - `git_status() → ToolResponse{data: {staged: [], unstaged: [], untracked: []}}`
  - `git_diff(ref_a?, ref_b?) → ToolResponse{data: {files_changed, insertions, deletions, patches: [UnifiedDiff]}}`
  - `git_commit(message) → ToolResponse{data: {sha, message, files_changed}}`
  - `git_push(remote?, branch?, force?) → ToolResponse{data: {remote, branch, pushed}}`
  - `git_pull(remote?, branch?) → ToolResponse{data: {remote, branch, updated}}`
  - All return structured JSON — not raw output strings

#### 23.3 — Refactor tool_executor.py

- Replace `_resolve_sandboxed` calls with `Workspace.resolve()`
- One `Workspace` instance per build, passed through to all tools
- No functional changes to existing tool behaviour — only the path resolution implementation changes

#### 23.4 — Tests

- Sandbox enforcement edge cases: symlinks, `..` chains, absolute paths, null bytes, Unicode paths
- Git operation structure: each op returns correct JSON shape
- Workspace caching: TTL expiry, invalidation on write
- File tree: `.gitignore` filtering, configurable ignores
- Backward compat: all existing tool_executor tests still pass

**Exit criteria:**
- Single `Workspace` instance per build
- All path resolution goes through `Workspace.resolve()`
- Git operations return structured `ToolResponse` objects
- File tree respects `.gitignore` with caching
- All tests pass

---

## Phase 24 -- Headless IDE Runtime: Read & Search Primitives

**Objective:** Structured file reading with line ranges, ripgrep-powered code search, and an import graph index for Python. These are the perception tools — the builder's eyes.

---

### Deliverables

#### 24.1 — Structured file reader

- `app/ide/reader.py`:
  - `read_file(path) → ToolResponse{data: {path, content, line_count, size_bytes, language, encoding}}`
  - `read_range(path, start_line, end_line) → ToolResponse{data: {path, start_line, end_line, content, lines: [str]}}`
  - `read_symbol(path, symbol_name) → ToolResponse{data: {path, symbol, kind, start_line, end_line, content}}` — uses `ast.parse` for Python, regex for TS/JS. Full symbol resolution improved in Phase 27.
  - Language detection from file extension
  - Encoding detection: UTF-8 default, fallback to latin-1, binary file detection
  - Size limit enforcement: configurable, default 100KB
  - All results as structured JSON with explicit line numbers

#### 24.2 — Code search

- `app/ide/searcher.py`:
  - `search(pattern, glob?, is_regex?, max_results?, context_lines?) → ToolResponse{data: {matches: [Match], total_count, truncated}}`
  - `Match` model: `{path, line, column, snippet, context_before: [str], context_after: [str]}`
  - Uses `ripgrep` (`rg`) subprocess if available on PATH, falls back to Python `re` module
  - `.gitignore`-aware by default (via ripgrep's built-in support, or manual parsing in fallback)
  - Configurable context lines: default 2 before/after each match
  - Result count capping with `truncated: bool` indicator (default 100)
  - Structured JSON output per match — not raw grep lines

#### 24.3 — File index & import graph

- `app/ide/file_index.py`:
  - Background file indexing: `FileIndex.build(workspace) → {path → FileMetadata}`
  - `FileMetadata`: `{path, language, size_bytes, last_modified, imports: [str], exports: [str]}`
  - Import graph for Python files via `ast.parse` of import statements: `import foo`, `from foo import bar`, `from . import baz`
  - `get_importers(module_path) → [str]` — which files import this module
  - `get_imports(file_path) → [str]` — what does this file import
  - Cached per workspace, invalidated on file write (selective — only re-index changed files)
  - Index stored in-memory (not DB) — rebuilds fast enough for workspace sizes < 10K files

#### 24.4 — Tests

- Read range boundaries: first line, last line, beyond EOF, single line, empty file
- Search accuracy: literal vs regex, case sensitivity, glob filtering
- `.gitignore` filtering: nested ignores, negation patterns
- Encoding edge cases: UTF-8 BOM, latin-1, binary detection
- Import graph: circular imports, relative imports, re-exports, conditional imports
- Ripgrep fallback: test both paths (rg available / not available)

**Exit criteria:**
- Builder can read arbitrary line ranges, search with context, and query the import graph
- All results are structured JSON with explicit line numbers and metadata
- Search uses ripgrep when available for performance
- Import graph correctly maps Python module dependencies
- All tests pass

---

## Phase 25 -- Headless IDE Runtime: Command Runner

**Objective:** General-purpose command execution with structured output capture, timeout management, and deterministic log summarisation. No LLM involved — parsers are pure regex/structural.

---

### Deliverables

#### 25.1 — Command runner

- `app/ide/runner.py`:
  - `run(command, timeout_s?, cwd?, env?) → RunResult`
  - `RunResult` model: `{exit_code, stdout, stderr, duration_ms, truncated: bool, killed: bool}`
  - Streaming capture with configurable buffer sizes (default 50KB stdout, 10KB stderr)
  - Process kill on timeout: SIGTERM → wait 5s → SIGKILL (platform-aware: Windows uses `taskkill`)
  - Command allowlist enforcement: inherits and extends current `_validate_command` logic from tool_executor.py
  - Command injection prevention: block `;`, `|`, `&`, backticks, `$()`, `{}`
  - Environment variable isolation: can pass custom env without leaking host secrets

#### 25.2 — Deterministic log parsers

- `app/ide/log_parser.py` — no LLM, just regex/structural parsing:
  - `summarise_pytest(stdout) → PytestSummary{total, passed, failed, errors, skipped, failures: [{test_name, file, line, message}], duration_s, collection_errors: [str]}`
  - `summarise_npm_test(stdout) → NpmTestSummary{total, passed, failed, failures: [{test_name, file, message}]}`
  - `summarise_build(stdout) → BuildSummary{success: bool, errors: [{file, line, message}], warnings: [{file, line, message}]}`
  - `summarise_generic(stdout, stderr, max_lines?) → GenericSummary` — truncation strategy: first 50 lines + last 50 lines + all lines containing "error", "fail", "exception" (case-insensitive)
  - All parsers return structured models — never raw text

#### 25.3 — Combined run + summarise

- `run_and_summarise(command, parser?) → {result: RunResult, summary: ParsedSummary}` — detects parser from command (pytest → pytest parser, npm test → npm parser, etc.), combines execution + parsing in one call

#### 25.4 — Refactor existing tools

- Replace `_exec_run_tests` and `_exec_run_command` in tool_executor.py with calls to the new runner
- Test result parsing replaces raw stdout truncation in `_verify_phase_output`
- Runner registered in IDE tool registry

#### 25.5 — Tests

- Timeout handling: process killed on timeout, killed flag set, partial output captured
- Signal propagation: SIGTERM then SIGKILL sequence
- Pytest parser: passing run, failing run, collection errors, no tests found, verbose output, parametrized tests
- Npm test parser: vitest output, jest output, passing/failing
- Truncation: output exceeding buffer size, error line preservation
- Exit codes: 0 (success), 1 (failure), 137 (killed), -1 (crash)
- Command allowlist: allowed commands pass, blocked commands rejected

**Exit criteria:**
- Every command returns structured JSON with `RunResult` model
- Pytest and npm test output are parsed into structured summaries with individual test failures
- No raw stdout dumps are ever sent to the LLM
- Timeout and process kill work correctly on Windows
- All tests pass

---

## Phase 26 -- Headless IDE Runtime: Patch Engine

**Objective:** Apply surgical code changes via unified diffs instead of full-file rewrites. A one-line fix no longer requires regenerating the entire file — the single biggest token savings in the builder.

---

### Deliverables

#### 26.1 — Patch applicator

- `app/ide/patcher.py`:
  - `apply_patch(path, unified_diff) → PatchResult`
  - `PatchResult` model: `{success, path, hunks_applied, hunks_failed, post_content, pre_content, diff_summary}`
  - `apply_multi_patch(patches: [{path, diff}]) → list[PatchResult]` — atomic: all succeed or all roll back
  - Unified diff parsing: standard `---`/`+++`/`@@` format
  - Fuzzy hunk matching: tolerates ±3 line offset (configurable) — handles cases where prior edits shifted line numbers
  - Conflict detection: if hunk doesn't match at expected location AND fuzzy match fails, report `PatchConflict` with the expected vs actual content
  - Rollback on failure: backup original content in memory, restore if any hunk fails
  - Post-apply diff summary: net lines added/removed, files touched

#### 26.2 — Diff generator

- `app/ide/diff_generator.py`:
  - `generate_diff(path, old_content, new_content) → UnifiedDiff` — for audit trail and LLM display
  - `generate_multi_diff(changes: [{path, old, new}]) → list[UnifiedDiff]`
  - 3 context lines per hunk (standard unified diff format)
  - Line endings normalised to LF before diffing

#### 26.3 — Wire into builder

- Update `_generate_single_file`: for `action: "modify"` entries in the file manifest, the system prompt instructs the builder to emit a unified diff instead of full file content
- Diff detection: if the LLM response starts with `---` or contains `@@ -`, treat as diff and apply via patch engine; otherwise treat as full file content
- Fallback: if patch application fails (conflict), re-request as full file content

#### 26.4 — Tests

- Clean apply: single hunk, multi-hunk, hunk at start/end of file
- Fuzzy matching: hunks offset by 1-3 lines, exact match preferred over fuzzy
- Conflict detection: modified target content, deleted lines, conflicting hunks
- Rollback: multi-patch where second patch fails, first patch rolled back
- Atomic multi-file: 3 files, second fails, all rolled back
- Empty diff: no changes, no error
- Binary file rejection: binary files detected and rejected
- Diff generation: round-trip (generate diff → apply diff = same result)

**Exit criteria:**
- Builder can emit diffs for modifications, reducing token usage per fix from full-file to patch-sized
- Patches apply cleanly with fuzzy matching for minor line offsets
- Failed patches roll back automatically — no corrupted files
- Full audit trail of every change via generated diffs
- All tests pass

---

## Phase 27 -- Headless IDE Runtime: Language Intelligence

**Objective:** Real diagnostics and symbol resolution for Python and TypeScript/JavaScript, replacing the current `ast.parse`-only syntax check with full language server diagnostics.

---

### Deliverables

#### 27.1 — Language server abstraction

- `app/ide/lang/__init__.py` — `LanguageIntel` protocol:
  - `get_diagnostics(path) → list[Diagnostic]`
  - `get_symbol_outline(path) → list[Symbol]`
  - `resolve_imports(path) → list[ImportInfo]`
  - `Symbol` model: `{name, kind, start_line, end_line, parent: str | None}`
  - `ImportInfo` model: `{module, names: [str], resolved_path: str | None, is_stdlib: bool}`

#### 27.2 — Python intelligence

- `app/ide/lang/python_intel.py`:
  - **Diagnostics**: run `pyright --outputjson {path}` OR `ruff check --output-format json {path}` → parse JSON → `list[Diagnostic]`
  - Preference: ruff for linting (fast, covers style + imports), pyright for type checking (optional, heavier)
  - Fallback: `ast.parse` if neither tool available (current behaviour preserved)
  - **Symbol outline**: `ast.parse` → extract classes, functions, methods, module-level variables with `{name, kind, start_line, end_line, parent}`
  - **Import resolution**: parse import statements → resolve each against:
    1. Workspace files (relative + absolute imports)
    2. Installed packages (check `site-packages` or `pip show`)
    3. Standard library (hardcoded module list per Python version)
  - **Dead code detection** (informational): symbols defined in a file but never imported/referenced by any other workspace file (using the import graph from Phase 24)

#### 27.3 — TypeScript/JavaScript intelligence

- `app/ide/lang/typescript_intel.py`:
  - **Diagnostics**: `npx tsc --noEmit --pretty false` → parse output → `list[Diagnostic]`
  - **ESLint**: `npx eslint --format json {path}` → parse JSON → `list[Diagnostic]` (merged with tsc diagnostics)
  - **Symbol outline**: regex-based extraction of exports, functions, classes, interfaces, type aliases
  - Fallback: `node --check {path}` for basic syntax validation if tsc unavailable

#### 27.4 — Unified diagnostics interface

- `app/ide/diagnostics.py`:
  - `get_diagnostics(paths?: list[str]) → DiagnosticReport{files: dict[str, list[Diagnostic]], error_count, warning_count, info_count}`
  - Auto-detects language from file extension, routes to correct language intel
  - Severity levels: `error`, `warning`, `info`, `hint`
  - Diagnostic cache: per-file, invalidated when the file is written/patched
  - `get_diagnostics()` with no args: all files with cached diagnostics (no full workspace scan unless requested)

#### 27.5 — Replace current check_syntax

- Current `_exec_check_syntax` in tool_executor.py (which only does `ast.parse` for Python) replaced by the full diagnostic pipeline
- `_verify_phase_output` updated to use `get_diagnostics()` for richer error reporting

#### 27.6 — Tests

- Pyright JSON parsing: errors, warnings, information-level diagnostics
- Ruff JSON parsing: lint violations, import sorting issues
- TSC output parsing: type errors, missing module declarations
- ESLint JSON parsing: rule violations, fixable issues
- Symbol extraction: Python classes/functions/methods, TS exports/interfaces/types
- Import resolution: relative imports, absolute imports, third-party, stdlib, unresolved
- Dead code detection: unused function, unused class, unused variable
- Diagnostic cache: cache hit, invalidation on write, TTL expiry

**Exit criteria:**
- `get_diagnostics()` returns structured errors/warnings for Python (via ruff/pyright) and TS (via tsc/eslint)
- Symbol outlines available for Python + TS files for context assembly
- Import validation detects unresolved imports (missing packages, typos)
- All diagnostics are structured `Diagnostic` objects — never raw text
- All tests pass

---

## Phase 28 -- Headless IDE Runtime: Context Pack Generator

**Objective:** Replace raw file dumps with compact, relevant context packs assembled using the IDE's workspace intelligence. This is the token efficiency win — the builder receives exactly what it needs, nothing more.

---

### Deliverables

#### 28.1 — Context pack generator

- `app/ide/context_pack.py`:
  - `generate_pack(task_description, target_files, workspace, budget_tokens?) → ContextPack`
  - `ContextPack` model:
    ```
    {
      repo_summary: {file_count, languages: dict, structure_tree: str},
      target_files: [{path, content, diagnostics: [Diagnostic]}],
      dependency_files: [{path, relevant_snippet, why: str}],
      related_snippets: [{path, start_line, end_line, content, relevance_score}],
      diagnostics_summary: {errors: [Diagnostic], warnings: [Diagnostic]},
      test_failures: [{test_name, file, line, message}],
      git_diff: {files_changed, insertions, deletions, patches: [UnifiedDiff]},
      token_estimate: int,
    }
    ```
  - **Repo summary**: cached, invalidated on file write — includes top-level file tree (not full recursive tree for large repos)
  - **Target files**: full content of files being created/modified
  - **Dependency files**: files referenced in manifest `depends_on` + `context_files` — only relevant snippets extracted (not full files unless small)
  - **Related snippets**: discovered via import graph + relevance scoring (not manually declared)
  - **Diagnostics summary**: current diagnostic state of target files
  - **Test failures**: parsed from last test run (if available)
  - **Git diff**: uncommitted changes relevant to the target files
  - **Token budget enforcement**: pack is trimmed to fit within budget — lowest-relevance items dropped first

#### 28.2 — Relevance scoring

- `app/ide/relevance.py`:
  - `find_related(target_path, workspace, max_results?) → list[RelatedFile]`
  - `RelatedFile`: `{path, score: float, reason: str}`
  - Scoring factors:
    - Import graph distance: direct import = 1.0, transitive (2 hops) = 0.5, none = 0.0
    - Directory proximity: same directory = 0.3, parent/child = 0.2
    - Filename similarity: test file ↔ implementation file = 0.4
    - File recency: recently modified files score higher (they're likely relevant to current work)
  - Scores are additive (a file can score high on multiple factors)
  - Replaces `_select_contracts_for_file` and `_calculate_context_budget` with a smarter system that considers code relationships, not just path prefixes

#### 28.3 — Wire into plan-execute builder

- Replace the current context injection in `_generate_single_file`:
  - Instead of raw `context_files` content, generate a `ContextPack`
  - Pack is serialised into the user message sent to the builder
  - Token budget automatically managed by the pack generator
- Contract selection still uses `_CONTRACT_RELEVANCE` mapping (it's orthogonal — contracts are project-level, context packs are code-level)
- Measure token usage before/after to validate savings

#### 28.4 — Tests

- Relevance scoring: import-based, directory-based, name-based, recency-based
- Token budget enforcement: pack trimmed to fit, lowest-relevance items dropped
- Pack generation: correct fields populated, empty workspace handling
- Caching: repo summary cached, invalidated correctly
- Integration: pack used in `_generate_single_file`, correct context delivered

**Exit criteria:**
- Builder receives context packs instead of raw file dumps
- Token usage per file call drops measurably (target: 30-50% reduction in input tokens)
- Context is relevant (import-graph-aware) not exhaustive (raw dumps)
- Pack respects token budget — no context overflow
- All tests pass

---

## Phase 29 -- Headless IDE Runtime: Builder Orchestration Integration

**Objective:** Wire the complete IDE runtime into the plan-execute orchestrator. The builder uses IDE tools, emits patches for modifications, runs inline diagnostics, and receives structured output. The IDE becomes the builder's hands and eyes.

---

### Deliverables

#### 29.1 — Updated file generation

- Update `_generate_single_file` system prompt:
  - For `action: "create"` — outputs full file content (unchanged)
  - For `action: "modify"` — emits unified diff, applied by patch engine
  - Context comes from `ContextPack` instead of raw file content
- Diff detection: if the LLM response starts with `---`/`+++` or contains `@@ -`, treat as diff and apply via patch engine; otherwise treat as full file content
- Fallback: if patch application fails (conflict), re-request as full file content with error context

#### 29.2 — Inline diagnostics in file loop

- After each file is written/patched:
  - Run `get_diagnostics()` on the file
  - If diagnostics show errors: auto-fix loop using patch (not full rewrite) — up to 2 attempts
  - Diagnostics summary included in the NEXT file's context pack (so the builder sees the current state of the codebase)
- This replaces the post-hoc batch `_verify_phase_output` syntax check — errors are caught and fixed immediately, not at end of phase

#### 29.3 — Updated phase verification

- `_verify_phase_output` updated:
  - Uses `get_diagnostics()` for all files in the manifest (replaces per-file `check_syntax`)
  - Uses `run_and_summarise("pytest ...")` for test execution with parsed `PytestSummary`
  - Returns structured `{diagnostics: DiagnosticReport, test_summary: PytestSummary, fixes_applied: int}` instead of raw counts
  - Test output sent to frontend verification bar as structured data

#### 29.4 — IDE tools in conversation mode

- Update `BUILDER_TOOLS` in tool_executor.py with IDE tool definitions from the registry
- Update `execute_tool_async` to route IDE tools through the registry dispatcher
- All tool results are structured JSON via `ToolResponse` — replaces raw string returns
- Backward compat: old tool names still work, mapped to new implementations

#### 29.5 — Rate limiting & backoff

- Exponential backoff on LLM API calls: 1s → 2s → 4s → 8s (max 30s)
- Rate limiter: max N concurrent LLM calls (configurable, default 3)
- Applied to fix loops (prevent runaway retry storms)
- Applied to `_generate_single_file` calls (prevent overwhelming the API during rapid manifest execution)

#### 29.6 — Tests

- Integration: IDE-powered build loop generates files correctly
- Patch-based modifications: modify action emits diff, applied correctly
- Inline diagnostics: error detected after write, auto-fix attempt, fix applied
- Structured verification: `_verify_phase_output` returns correct models
- Conversation mode: IDE tools accessible via BUILDER_TOOLS, structured responses
- Rate limiting: backoff timing, concurrent call limiting
- Fallback: patch conflict → full file regeneration

**Exit criteria:**
- Plan-execute mode uses context packs for file generation
- Modifications use patches instead of full rewrites (measurable token savings)
- Inline diagnostics catch and fix errors immediately per file
- Conversation mode has access to all IDE tools with structured responses
- Rate limiting prevents API overload during fix loops
- All tests pass

---

## Phase 30 -- Headless IDE Runtime: Determinism Hardening & Audit Trail

**Objective:** Ensure every IDE operation produces stable, reproducible output with a complete audit trail. No flaky ordering, no leaked secrets, no non-deterministic noise.

---

### Deliverables

#### 30.1 — Audit trail storage

- `app/ide/audit_trail.py`:
  - Every tool invocation logged: `{timestamp, tool_name, input_params, output_data, duration_ms, build_id, phase, success}`
  - `IDEOperation` Pydantic model for structured records
  - Stored in PostgreSQL via new `ide_operations` table
  - Queryable: filter by build_id, phase, tool_name, success/failure
  - Async writes — logging never blocks the tool execution
- DB migration: `db/migrations/012_ide_operations.sql`:
  - `ide_operations` table: `id, build_id, phase, tool_name, input_params (jsonb), output_data (jsonb), success, error_message, duration_ms, created_at`
  - Index on `(build_id, phase)` for efficient per-build queries
  - Index on `(tool_name, created_at)` for tool usage analytics

#### 30.2 — Output stability

- All IDE tool outputs are deterministically ordered:
  - File listings: sorted alphabetically by path
  - Search results: sorted by file path, then line number
  - Diagnostics: sorted by file path, then line number, then severity
  - Git diffs: sorted by file path
  - Symbol outlines: sorted by start_line
- All timestamps in UTC ISO 8601 format
- All line endings normalised to LF in tool outputs
- All paths use forward slashes (even on Windows)

#### 30.3 — Noise filtering

- `.gitignore`-aware everywhere (already implemented in Phase 23)
- Auto-ignore generated/transient files: `*.pyc`, `__pycache__/`, `node_modules/`, `.venv/`, `dist/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- Test output sanitisation: strip non-deterministic content:
  - Timestamps in pytest output → replaced with `[timestamp]`
  - Process IDs → replaced with `[pid]`
  - Temp file paths → replaced with `[tmpdir]`
  - Absolute workspace paths → replaced with relative paths

#### 30.4 — Secret redaction

- `app/ide/redactor.py`:
  - Scan tool inputs/outputs for sensitive patterns before logging and before sending to LLM
  - Pattern list (regex): API keys (`sk-`, `key-`, `ghp_`, `gho_`), JWT tokens, connection strings (`postgresql://`, `mysql://`), passwords (`:password@`, `PASSWORD=`), AWS keys (`AKIA`), base64-encoded secrets (long alphanumeric strings in env vars)
  - Redaction: replace matched values with `[REDACTED]`
  - Configurable: additional patterns can be added via settings
  - Applied at the `ToolResponse` level — redaction happens after tool execution but before logging/broadcast

#### 30.5 — Consistent diff formatting

- All diffs use unified format with 3 context lines
- Line endings normalised to LF before diffing
- Trailing whitespace stripped from diff output
- File paths in diffs always use forward slashes
- No-newline-at-end-of-file marker (`\ No newline at end of file`) preserved

#### 30.6 — Tests

- Sort stability: same input always produces same output order
- Secret redaction: each pattern type detected and redacted, false positive avoidance (short strings not redacted)
- Noise filtering: timestamps stripped, PIDs stripped, temp paths normalised
- Audit trail: operations logged, queryable by build/phase/tool, async write doesn't block
- Diff consistency: same content always produces identical diffs across platforms
- Path normalisation: Windows backslashes → forward slashes in all outputs
- Migration: `012_ide_operations.sql` applies cleanly, indexes created

**Exit criteria:**
- Every IDE operation is logged with structured data in the `ide_operations` table
- All outputs are deterministically ordered — same input always produces identical output
- No secrets leak to LLM context or build logs
- Non-deterministic noise (timestamps, PIDs, temp paths) is sanitised from tool outputs
- Audit trail is queryable: "show me all tool calls for build X, phase Y"
- All tests pass

---

## Phase 31 — Contract Version History

**Objective:** Preserve previous contract generations as numbered snapshots so users can compare consistency across regeneration runs. Before each regeneration, the current set of contracts is archived into a `contract_snapshots` table as a versioned batch (V1, V2, V3…). The UI exposes a collapsible "Previous Versions" section under the contracts panel.

**Deliverables:**

#### 31.1 — Database schema

- DB migration: `db/migrations/013_contract_snapshots.sql`:
  - `contract_snapshots` table: `id (UUID PK), project_id (FK → projects), batch (INTEGER), contract_type (VARCHAR 50), content (TEXT), created_at (TIMESTAMPTZ)`
  - Index on `(project_id, batch)` for efficient batch queries
  - Index on `(project_id)` for project-level queries
- `schema.md` updated with the new table definition

#### 31.2 — Repository layer

- `app/repos/project_repo.py`:
  - `snapshot_contracts(project_id)` — copies all current `project_contracts` rows into `contract_snapshots` with `batch = max(existing batch) + 1` (or 1 if no snapshots exist)
  - `get_snapshot_batches(project_id)` — returns list of `{batch, created_at, count}` for all snapshots
  - `get_snapshot_contracts(project_id, batch)` — returns all contracts for a given batch

#### 31.3 — Service layer

- `app/services/project_service.py`:
  - Before the generation loop in `generate_contracts()`, call `snapshot_contracts()` if contracts already exist for this project
  - `list_contract_versions(user_id, project_id)` — ownership check + delegates to repo
  - `get_contract_version(user_id, project_id, batch)` — ownership check + delegates to repo

#### 31.4 — API endpoints

- `app/api/routers/projects.py`:
  - `GET /projects/{id}/contracts/history` — returns list of snapshot batches
  - `GET /projects/{id}/contracts/history/{batch}` — returns all contracts for a specific batch

#### 31.5 — Frontend

- `web/src/pages/ProjectDetail.tsx`:
  - Below the current contracts list in the expanded panel, show a collapsible "Previous Versions" section
  - Each version row: "V{batch}" | {count} contracts | {date} | expand/collapse toggle
  - Expanding a version shows the 9 contract type names with content viewable in a read-only modal or inline
  - Lazy-load version content only when expanded (calls history/{batch} endpoint)

#### 31.6 — Tests

- `tests/test_contract_snapshots.py`:
  - Snapshot creation: first snapshot is batch 1, subsequent increments
  - Snapshot preserves correct content from live contracts
  - Regeneration auto-snapshots before overwriting
  - History list endpoint returns correct batches
  - History detail endpoint returns correct contracts
  - Ownership checks prevent cross-user access
  - Empty project (no contracts) does not create empty snapshots
- Frontend tests: existing 61 tests still pass

**Schema coverage:**
- [x] contract_snapshots (new table)

**Exit criteria:**
- Migration applies cleanly
- Regenerating contracts automatically snapshots the previous set as a new batch
- `GET /projects/{id}/contracts/history` returns correct batch list
- `GET /projects/{id}/contracts/history/{batch}` returns correct contracts
- UI shows "Previous Versions" with expand/collapse per batch
- All existing backend tests pass + new snapshot tests pass
- All existing frontend tests pass
- `run_audit.ps1` passes all checks

---

## Phase 32 — Scout Dashboard: UI Foundation & Sidebar Navigation

**Objective:** Add Scout as a first-class navigation item in the sidebar and create the Scout landing page with repo listing, run history, and the ability to trigger Scout runs against connected repos. Scout wraps the existing audit engine (`app/audit/runner.py`) into a user-facing on-demand feature.

**Deliverables:**

#### 32.1 — Sidebar navigation section

- `web/src/components/AppShell.tsx`:
  - Add a **nav section** between the repo list and the user box (above `marginTop: auto`)
  - Nav section has its own `borderTop` separator
  - First nav item: Scout icon + "Scout" label
  - Active state highlight when on `/scout` routes (blue left border, dark bg — matches repo active style)
  - Extensible pattern for future nav items

#### 32.2 — Scout page with repo selector

- `web/src/pages/Scout.tsx`:
  - Landing state: lists connected repos with health badges and "Scout Repo →" button per row
  - Filter/search input for repos (client-side filtering)
  - "Recent Scout Runs" section below the repo list showing last N runs across all repos
  - Each history row: repo name, timestamp, pass/fail count, status badge

#### 32.3 — Route registration

- `web/src/App.tsx`:
  - Add `/scout` route → `Scout` page (protected)

#### 32.4 — Database schema for Scout runs

- DB migration `db/migrations/015_scout_runs.sql`:
  - `scout_runs` table: `id (UUID PK DEFAULT gen_random_uuid()), repo_id (UUID FK → repos), user_id (UUID FK → users), status (VARCHAR 20 DEFAULT 'pending'), hypothesis (TEXT), results (JSONB), checks_passed (INTEGER DEFAULT 0), checks_failed (INTEGER DEFAULT 0), checks_warned (INTEGER DEFAULT 0), started_at (TIMESTAMPTZ DEFAULT now()), completed_at (TIMESTAMPTZ)`
  - Indexes: `(repo_id, started_at DESC)`, `(user_id, started_at DESC)`
- `Forge/Contracts/schema.md` updated with `scout_runs` table definition

#### 32.5 — Repository layer

- `app/repos/scout_repo.py`:
  - `create_scout_run(repo_id, user_id, hypothesis) → dict` — inserts pending run
  - `update_scout_run(run_id, status, results, checks_passed, checks_failed, checks_warned) → dict` — updates run with results
  - `get_scout_runs_by_user(user_id, limit=20) → list[dict]` — recent runs across all repos
  - `get_scout_runs_by_repo(repo_id, user_id, limit=20) → list[dict]` — recent runs for one repo
  - `get_scout_run(run_id) → dict | None` — single run detail

#### 32.6 — Service layer

- `app/services/scout_service.py`:
  - `start_scout_run(user_id, repo_id, hypothesis=None) → dict` — validates ownership, creates run, kicks off audit in background task, streams progress via WS
  - `_execute_scout(run_id, repo_id, user_id, hypothesis)` — clones repo, runs `run_audit()` with hypothesis context, updates run record, sends WS completion event
  - `get_scout_history(user_id, repo_id=None) → list[dict]` — returns run history
  - `get_scout_detail(user_id, run_id) → dict` — returns full run detail with check results
  - WS events: `scout_progress` (per-check streaming), `scout_complete` (final result)

#### 32.7 — API endpoints

- `app/api/routers/scout.py`:
  - `POST /scout/{repo_id}/run` — body: `{ "hypothesis": "optional text" }` — triggers Scout run
  - `GET /scout/history` — returns recent runs for current user
  - `GET /scout/{repo_id}/history` — returns recent runs for a specific repo
  - `GET /scout/runs/{run_id}` — returns full run detail
- Register router in `app/main.py`

#### 32.8 — Scout page live states

- `web/src/pages/Scout.tsx` (continued):
  - **Running state**: after clicking "Scout Repo", show live check progress (✅/❌/⏳/⬜ per check), driven by `scout_progress` WS events
  - **Results state**: check breakdown with pass/fail/warn per check, expandable detail, action buttons: "Re-Scout", "Full Report"
  - **History state**: expandable past runs, click to view full results

#### 32.9 — Tests

- `tests/test_scout_repo.py`: CRUD tests for scout_runs table
- `tests/test_scout_service.py`: service orchestration tests (mock audit runner)
- `tests/test_scout_router.py`: endpoint tests (auth, validation, responses)
- Frontend: existing tests still pass + Scout page renders

**Schema coverage:**
- [x] scout_runs (new table)

**Exit criteria:**
- Migration applies cleanly
- Scout nav item appears in sidebar, navigates to `/scout`
- Scout page lists connected repos with "Scout Repo" buttons
- Clicking "Scout Repo" triggers an audit run and streams results live
- Results page shows pass/fail/warn per check with detail
- History section shows past Scout runs
- All existing backend tests pass + new Scout tests pass
- All existing frontend tests pass
- `run_audit.ps1` passes all checks

---

## Phase 33 — Scout Remediation: Scoped Micro-Builds

**Objective:** When Scout finds failing checks, offer one-click scoped remediation that triggers a minimal-diff builder session targeted at the specific failures. The user reviews a proposed fix plan before execution. Multi-issue remediation runs in a single scoped session.

**Deliverables:**

#### 33.1 — Remediation plan generation

- `app/services/scout_service.py` additions:
  - `generate_remediation_plan(user_id, run_id) → dict` — analyses Scout failures, generates a remediation plan (which files, what approach, estimated scope) using Sonnet as NLU
  - Returns structured plan: `{ "issues": [...], "files_to_modify": [...], "approach": str, "estimated_phases": int }`

#### 33.2 — Remediation builder mode

- `app/services/build_service.py` additions:
  - Support `build_mode: "remediation"` — compressed cycle: plan → fix → test → verify
  - Receives: failing checks, audit report, relevant files, hypothesis
  - Produces minimal diff targeting only the failures
  - Runs tests to confirm fix
  - Evidence written to standard structure

#### 33.3 — Scout UI action buttons

- `web/src/pages/Scout.tsx` additions:
  - Results state shows action buttons: "Fix All Issues", "Fix Selected" (checkboxes per failing check)
  - Clicking fix shows remediation plan for review before execution
  - "Approve & Fix" button triggers the remediation build
  - Live progress of remediation build (reuses build progress streaming pattern)
  - Completion card: "All checks pass, evidence committed"

#### 33.4 — Tests

- Service tests for remediation plan generation
- Integration tests for remediation builder mode
- Frontend tests for action buttons and plan review

**Exit criteria:**
- Scout results show "Fix" action buttons on failing checks
- Clicking fix generates and displays a remediation plan
- Approving the plan triggers a scoped micro-build
- Micro-build produces minimal diff and runs tests
- All existing tests pass + new remediation tests pass

---

## Phase 34 — Phase Transition Governance Gate

**Objective:** Add deterministic, non-LLM governance checks that run at every phase boundary before commit. Mirrors the Forge `run_audit.ps1` checks (A1/A4/A9/W1/W3) but runs in-process against the target project's contracts. This replaces the need for a separate "final audit phase" — governance runs inline at every phase transition, alongside the existing parallel per-file audit and syntax verification.

**Execution note:** Should be built BEFORE Phase 32 (Scout Dashboard) since it improves the build pipeline that subsequent phases will use.

**Deliverables:**

#### 34.1 — `_run_governance_checks()` function

- `app/services/build_service.py`:
  - New async function `_run_governance_checks(build_id, user_id, api_key, manifest, working_dir, contracts, touched_files) → dict`
  - Returns `{ "passed": bool, "checks": [...], "blocking_failures": int, "warnings": int, "fixes_applied": int }`
  - Runs 7 deterministic checks (no LLM calls) — reuses existing functions from `app/audit/runner.py` where possible

#### 34.2 — Governance checks (7 checks)

| Check | Description | Blocking? | Reuses |
|---|---|---|---|
| G1 Scope compliance | `set(files_written)` == `set(manifest_paths)` — flag phantom or unclaimed files | Yes | new |
| G2 Boundary compliance | Load target project's `boundaries` contract, run forbidden-pattern check on touched files | Yes | `check_a4_boundary_compliance` |
| G3 Dependency gate | Parse imports in touched .py/.ts files, verify against `requirements.txt`/`package.json` on disk | Yes | `check_a9_dependency_gate` |
| G4 Secrets scan | Scan content of all generated files for secret patterns (`sk-`, `AKIA`, `password=`, etc.) | Yes | `check_w1_secrets_in_diff` adapted |
| G5 Physics route coverage | If target project has a `physics` contract, verify declared paths have handler files | No (warn) | `check_w3_physics_route_coverage` |
| G6 Rename detection | Check `git diff --summary` in working dir for unexpected renames | No (warn) | new |
| G7 TODO placeholder scan | Scan generated files for `TODO:`, `FIXME:`, `HACK:` in code (not comments) | No (warn) | new |

#### 34.3 — Pipeline integration

- Insert governance gate between verification (step 5) and commit (step 6) in the phase loop
- On blocking failure: enter fix loop (2 rounds via `_fix_single_file`), then pause if still failing
- On warn: log + broadcast, never block
- On all-pass: broadcast `governance_pass` and proceed to commit
- Governance results included in the commit message metadata

#### 34.4 — WS events for BuildProgress.tsx

- `governance_check` — per-check result streamed as they run (path, check_code, result, detail)
- `governance_pass` — all blocking checks passed
- `governance_fail` — one or more blocking checks failed with fix plan
- Frontend: `BuildProgress.tsx` renders governance check results in the phase activity panel (✅/❌/⚠ per check, expandable detail)

#### 34.5 — Tests

- `tests/test_build_service.py` additions:
  - `test_governance_checks_all_pass` — clean files, no violations
  - `test_governance_g1_scope_phantom` — file on disk not in manifest
  - `test_governance_g1_scope_missing` — manifest file not on disk
  - `test_governance_g2_boundary_violation` — SQL in router file
  - `test_governance_g3_dependency_missing` — import not in requirements.txt
  - `test_governance_g4_secrets_detected` — sk- pattern in generated code
  - `test_governance_g5_physics_coverage` — missing route handler
  - `test_governance_g6_rename_detected` — git rename in diff
  - `test_governance_g7_todo_placeholder` — TODO: in code
  - `test_governance_blocking_triggers_fix` — blocking failure enters fix round
  - `test_governance_warn_does_not_block` — warn check doesn't block commit
- Frontend: existing tests still pass + governance events render

**Schema coverage:** No new tables — governance results stored as build logs.

**Exit criteria:**
- Governance gate runs at every phase transition (between verify and commit)
- G1-G4 blocking checks prevent commit on failure
- G5-G7 warnings logged but don't block
- Blocking failures trigger fix loop (2 rounds), then pause
- WS events stream per-check results to BuildProgress.tsx
- All existing backend tests pass + 11 new governance tests pass
- All existing frontend tests pass
- `run_audit.ps1` passes all checks

---

## Phase 35 — Build Spend Cap / Circuit Breaker / Cost Gate

**Status:** ✅ COMPLETE (committed `4f3b9ad`)

**Objective:** Protect users from runaway API costs. User-configurable per-build spend cap, real-time cost ticker via WebSocket, circuit breaker for immediate kill, warning/exceeded banners in the UI.

**Deliverables:** DB migration 017 (build_spend_cap column), cost gate system in build_service.py (~165 lines), circuit-break + live-cost REST endpoints, spend-cap auth endpoints, frontend cost ticker + circuit breaker UI in BuildProgress.tsx, spend cap settings in Settings.tsx. 19 new tests. 685 total tests passing.

---

## Phase 36 — Scout Enhancement: Project Intelligence Report  *(DONE)*

**Status:** ✅ COMPLETE

**Objective:** Transform Scout from a compliance-only checker into a full **project intelligence engine**. When pointed at any connected GitHub repo, Scout should produce a comprehensive "Project Dossier" — a structured report covering the complete stack, architecture, dependencies, patterns, risk areas, and contract foundations. The dossier becomes the upstream input for the Forge Upgrade Advisor (Phase 37) and the Forge Seal certificate (Phase 38).

**Background:** Today's Scout fetches one commit's changed files and runs A/W audit checks. It has no concept of what the project *is* — its stack, architecture, patterns, or health. This phase adds a deep analysis layer on top of the existing GitHub API plumbing.

### 36.1 — GitHub Client: Repository Introspection APIs

New async functions in `app/clients/github_client.py`:

| Function | GitHub API | Returns |
|---|---|---|
| `get_repo_tree(token, full_name, sha, recursive=True)` | `GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1` | Full file tree (paths + sizes) |
| `get_repo_languages(token, full_name)` | `GET /repos/{owner}/{repo}/languages` | `{ "Python": 45000, "TypeScript": 32000, ... }` (byte counts) |
| `get_repo_metadata(token, full_name)` | `GET /repos/{owner}/{repo}` | Stars, forks, size, license, topics, created_at, updated_at, default_branch |
| `get_repo_package_json(token, full_name, ref)` | wrapper on `get_repo_file_content` | Parsed `package.json` or `None` |
| `get_repo_requirements(token, full_name, ref)` | wrapper on `get_repo_file_content` | Parsed `requirements.txt` / `pyproject.toml` / `Pipfile` or `None` |

Also add `list_directory(token, full_name, path, ref)` — fetches a directory listing at a specific path (for targeted exploration without pulling the whole tree).

**Tests:** 6 new tests mocking httpx responses for each new function.

### 36.2 — Stack Detector Module

New module `app/services/stack_detector.py`:

- **Input:** file tree (list of paths), language byte counts, manifest contents (package.json, requirements.txt, etc.)
- **Output:** `StackProfile` dict:
  ```
  {
    "languages": {"Python": 52.3, "TypeScript": 38.1, ...},  # percentages
    "primary_language": "Python",
    "backend": {
      "framework": "FastAPI",          # detected from imports/deps
      "version": "0.104.1",           # from requirements
      "runtime": "Python 3.12",        # from pyproject/Dockerfile
      "orm": "SQLAlchemy" | "raw SQL" | null,
      "db": "PostgreSQL" | "SQLite" | null,
    },
    "frontend": {
      "framework": "React",
      "version": "18.2.0",
      "bundler": "Vite",
      "language": "TypeScript",
      "ui_library": "Tailwind" | "MUI" | null,
    } | null,
    "infrastructure": {
      "containerized": true/false,      # Dockerfile present
      "ci_cd": "GitHub Actions" | null, # .github/workflows/
      "hosting": "Render" | null,       # render.yaml
    },
    "testing": {
      "backend_framework": "pytest",
      "frontend_framework": "vitest" | "jest",
      "has_tests": true/false,
    },
    "project_type": "web_app" | "api" | "cli" | "library" | "monorepo",
    "manifest_files": ["requirements.txt", "package.json", ...],
  }
  ```
- Detection is **heuristic-based** (no LLM) — pattern matching on filenames, directory structure, dependency names, import patterns.
- Framework detection rules:
  - FastAPI: `fastapi` in requirements
  - Django: `django` in requirements
  - Express: `express` in package.json
  - Next.js: `next` in package.json
  - React: `react` in package.json + no `next`
  - Vue: `vue` in package.json
  - etc. (extensible registry pattern)

**Tests:** 10+ tests covering detection of common stacks (Python/FastAPI, Node/Express, React/Vite, Django, Next.js, monorepo, bare HTML).

### 36.3 — Architecture Mapper Module

New module `app/services/architecture_mapper.py`:

- **Input:** file tree, stack profile, selected file contents (entry points, config, route files)
- **Output:** `ArchitectureMap` dict:
  ```
  {
    "structure_type": "layered" | "flat" | "monorepo" | "microservices",
    "entry_points": ["app/main.py", "web/src/main.tsx"],
    "directories": {
      "app/api/routers/": "API route handlers",
      "app/services/": "Business logic layer",
      "app/repos/": "Data access layer",
      ...
    },
    "route_map": [
      {"method": "GET", "path": "/api/projects", "handler": "app/api/routers/projects.py"},
      ...
    ],
    "data_models": ["users", "projects", "builds", ...],  # from migrations or ORM models
    "external_integrations": ["GitHub API", "Anthropic API", "PostgreSQL"],
    "config_sources": [".env", "app/config.py"],
    "boundaries": { ... } | null,   # if boundaries.json found
    "file_count": 142,
    "total_lines": 28500,          # estimated from tree sizes
    "test_coverage_indicator": "high" | "medium" | "low" | "none",
  }
  ```
- Route detection: scan for `@router.get`, `@app.route`, `app.get(`, `router.post(` patterns in route files identified by directory convention.
- Data model detection: scan migration files or ORM model files for table/model names.
- Integration detection: scan imports for known client libraries (httpx→external API, asyncpg→Postgres, boto3→AWS, etc.).

**Tests:** 8+ tests covering different architecture patterns.

### 36.4 — Dossier Generator (LLM-Assisted Summary)

New function in `app/services/scout_service.py`: `_generate_project_dossier()`

- Collects: stack profile + architecture map + raw file samples (README, main entry point, config, 1-2 route files, 1-2 service files — capped at ~8K tokens total).
- Makes **one LLM call** (via existing `llm_client`) with a structured prompt:
  > "You are a senior software architect. Given the following project analysis data and code samples, produce a Project Dossier in the specified JSON schema. Include: executive summary, intent assessment, quality assessment, risk areas, and recommendations."
- LLM output parsed into `ProjectDossier`:
  ```
  {
    "executive_summary": "ForgeGuard is a full-stack web application...",
    "intent": "Automated code auditing and build governance platform",
    "quality_assessment": {
      "score": 82,                    # 0-100
      "strengths": ["Comprehensive test coverage", "Clean layered architecture"],
      "weaknesses": ["No type hints in some modules", "Missing CI/CD pipeline"],
    },
    "risk_areas": [
      {"area": "Security", "severity": "medium", "detail": "API keys stored in .env without rotation"},
      ...
    ],
    "recommendations": [
      {"priority": "high", "suggestion": "Add GitHub Actions CI pipeline"},
      ...
    ],
    "contracts_detected": {
      "has_boundaries": true/false,
      "has_physics": true/false,
      "has_forge_json": true/false,
    },
  }
  ```
- LLM call is **optional** — if user has no API key or opts out, the dossier returns everything except `executive_summary`, `intent`, and `quality_assessment` (pure heuristic mode).
- Cost tracking: the LLM call goes through `_record_phase_cost()` / `_accumulate_cost()` so it shows up in spend tracking.

**Tests:** 5 tests (mock LLM response, heuristic-only mode, malformed LLM response fallback, cost tracking, empty-repo edge case).

### 36.5 — Scout Service: New `deep_scan` Mode

Extend `scout_service.py`:

- New function `start_deep_scan(user_id, repo_id, hypothesis=None, include_llm=True)` — replaces the shallow commit-only scan with a full-repo analysis.
- Execution flow:
  1. Fetch repo metadata + languages via new GitHub client functions
  2. Fetch full file tree
  3. Run stack detector → `StackProfile`
  4. Identify key files to fetch (entry points, configs, routes, tests) — **cap at 20 files, 100KB total**
  5. Fetch key file contents
  6. Run architecture mapper → `ArchitectureMap`
  7. Run audit engine checks (existing A/W checks on fetched files)
  8. If `include_llm`: generate dossier via LLM
  9. Merge all results into `ScoutReport`
  10. Stream progress via WS (`scout_progress` with step names)
  11. Store in `scout_runs.results` JSONB
- The existing `start_scout_run()` remains as "quick scan" (single-commit audit).
- Add `scan_type` column to `scout_runs` table: `'quick'` (default, existing) or `'deep'`.

**DB migration 018:** `ALTER TABLE scout_runs ADD COLUMN scan_type VARCHAR(10) NOT NULL DEFAULT 'quick';`

**Tests:** 8 tests covering deep scan flow, file cap enforcement, WS streaming, fallback on errors.

### 36.6 — Scout Router: Deep Scan Endpoint

New endpoint in `app/api/routers/scout.py`:

- `POST /scout/{repo_id}/deep-scan` — triggers `start_deep_scan()`, returns `{ id, status, scan_type: "deep" }`
- `GET /scout/runs/{run_id}/dossier` — returns the full dossier from a completed deep scan (parsed from `results` JSONB)

**Tests:** 4 tests (trigger, auth, dossier retrieval, 404 on quick-scan run).

### 36.7 — Frontend: Scout Deep Scan UI

Update Scout-related pages:

- Add "Deep Scan" button alongside existing "Quick Scan" on the repo detail / Scout dashboard
- Deep scan progress: show step-by-step progress (fetching tree → detecting stack → mapping architecture → running checks → generating dossier)
- Dossier view: structured display of the full report — stack profile cards, architecture tree, quality gauge, risk table, recommendations list
- Quick scan results remain unchanged

**Tests:** Frontend component tests for new UI elements.

### 36.8 — Tests & Exit Criteria

**New test files:**
- `tests/test_stack_detector.py` — 10+ tests
- `tests/test_architecture_mapper.py` — 8+ tests
- `tests/test_scout_deep_scan.py` — 12+ tests (service + router + integration)
- Frontend tests for deep scan UI

**Exit criteria:**
- `POST /scout/{repo_id}/deep-scan` triggers full analysis and streams progress via WS
- Stack detector correctly identifies Python/FastAPI, Node/Express, React/Vite, Django, Next.js, bare HTML, monorepo stacks
- Architecture mapper produces route map, data models, integrations, structure classification
- Dossier includes LLM-generated summary when API key available, graceful fallback to heuristic-only
- File fetching capped at 20 files / 100KB (no runaway API calls)
- Results stored in `scout_runs.results` JSONB, retrievable via `/dossier` endpoint
- Existing quick scan unaffected
- All existing tests pass + 30+ new tests pass
- `run_audit.ps1` passes all checks

---

## Phase 37 — Forge Upgrade Advisor  *(DONE — commit pending)*

**Status:** 🔲 NOT STARTED

**Objective:** Build a tool that takes Scout's Project Dossier as input and produces a prioritized **Renovation Plan** — identifying outdated dependencies, legacy patterns, missing best practices, and modernization opportunities. The plan should be actionable: each recommendation includes what to change, why, estimated effort, risk level, and optionally a `forge.json`-compatible spec so Forge can execute the renovation.

**Background:** Scout (Phase 36) tells you *what* a project is. The Upgrade Advisor tells you *what it should become*. It compares the detected stack against known current versions, identifies anti-patterns, and produces a structured modernization roadmap.

**Depends on:** Phase 36 (Scout Enhancement) — requires StackProfile and ArchitectureMap as inputs.

### 37.1 — Version Currency Database

New module `app/services/version_db.py`:

A static, in-memory registry of **known latest stable versions** for major frameworks, libraries, and runtimes. No external API calls — just a curated dict that we update periodically.

```python
LATEST_VERSIONS: dict[str, VersionInfo] = {
    # Python ecosystem
    "python":       {"latest": "3.12",  "eol": {"3.8": "2024-10", "3.9": "2025-10"}, "category": "runtime"},
    "fastapi":      {"latest": "0.115", "min_recommended": "0.100", "category": "backend"},
    "django":       {"latest": "5.1",   "min_recommended": "4.2",  "category": "backend"},
    "flask":        {"latest": "3.1",   "min_recommended": "2.3",  "category": "backend"},
    "sqlalchemy":   {"latest": "2.0",   "min_recommended": "2.0",  "category": "orm"},
    "pydantic":     {"latest": "2.10",  "min_recommended": "2.0",  "category": "validation"},
    "pytest":       {"latest": "8.3",   "min_recommended": "7.0",  "category": "testing"},
    "asyncpg":      {"latest": "0.30",  "min_recommended": "0.28", "category": "database"},
    "uvicorn":      {"latest": "0.32",  "min_recommended": "0.20", "category": "server"},

    # Node ecosystem
    "node":         {"latest": "22",    "lts": "20",               "category": "runtime"},
    "react":        {"latest": "19.0",  "min_recommended": "18.0", "category": "frontend"},
    "next":         {"latest": "15.1",  "min_recommended": "14.0", "category": "frontend"},
    "vue":          {"latest": "3.5",   "min_recommended": "3.3",  "category": "frontend"},
    "vite":         {"latest": "6.1",   "min_recommended": "5.0",  "category": "bundler"},
    "typescript":   {"latest": "5.7",   "min_recommended": "5.0",  "category": "language"},
    "express":      {"latest": "5.0",   "min_recommended": "4.18", "category": "backend"},
    "vitest":       {"latest": "3.0",   "min_recommended": "1.0",  "category": "testing"},
    "tailwindcss":  {"latest": "4.0",   "min_recommended": "3.4",  "category": "styling"},

    # Databases
    "postgresql":   {"latest": "17",    "min_recommended": "15",   "category": "database"},
}
```

Functions:
- `check_version_currency(dep_name: str, detected_version: str | None) -> CurrencyResult` — returns `{"status": "current" | "outdated" | "eol" | "unknown", "latest": "...", "detail": "..."}`
- `get_all_version_info() -> dict` — dump the full registry (for UI display)

**Tests:** 10 tests — current/outdated/eol detection, unknown packages, edge cases.

### 37.2 — Pattern Analyzer

New module `app/services/pattern_analyzer.py`:

Scans the StackProfile, ArchitectureMap, and file contents to detect **anti-patterns** and **missing best practices**. Pure heuristic — no LLM.

**Anti-pattern registry** (extensible list):

| ID | Pattern | How Detected | Severity |
|---|---|---|---|
| `AP01` | No TypeScript (JS-only frontend) | `stack_profile.frontend.language == "JavaScript"` | medium |
| `AP02` | No test framework detected | `stack_profile.testing.has_tests == false` | high |
| `AP03` | No CI/CD pipeline | `stack_profile.infrastructure.ci_cd is None` | high |
| `AP04` | No containerization | `stack_profile.infrastructure.containerized == false` | medium |
| `AP05` | No ORM (raw SQL everywhere) | `backend.orm == "raw SQL (asyncpg)"` or similar | low |
| `AP06` | Missing `.env.example` | `.env` in tree but no `.env.example` | medium |
| `AP07` | No type hints (Python) | heuristic: scan sample files for `def foo(x):` without annotations | medium |
| `AP08` | Class components (React) | `extends React.Component` or `extends Component` in code | medium |
| `AP09` | Callback hell (Node) | deep nesting of callbacks (`function(err,` patterns) | medium |
| `AP10` | No error handling middleware | no `@app.exception_handler` / `app.use(errorHandler)` | medium |
| `AP11` | Secrets in code | `.env` values hardcoded, API keys in source | high |
| `AP12` | No README | `README.md` not in tree | low |
| `AP13` | No license | `LICENSE` not in tree | low |
| `AP14` | Flat project structure | `architecture.structure_type == "flat"` and `file_count > 20` | medium |
| `AP15` | No dependency pinning | `requirements.txt` present but no version specifiers | medium |

Function:
- `analyze_patterns(stack_profile, arch_map, file_contents) -> list[PatternFinding]`
  ```
  PatternFinding = {
    "id": "AP01",
    "name": "No TypeScript",
    "severity": "medium",
    "category": "quality" | "security" | "maintainability" | "devops",
    "detail": "Frontend uses plain JavaScript. TypeScript adds...",
    "affected_files": [...] | None,
  }
  ```

**Tests:** 15 tests — one per anti-pattern + combinations.

### 37.3 — Migration Path Recommender

New module `app/services/migration_advisor.py`:

Takes the StackProfile + PatternFindings and produces **concrete migration paths** — known upgrade trajectories from older/weaker patterns to modern equivalents.

**Migration registry** (extensible):

| From | To | Effort | Risk | Category |
|---|---|---|---|---|
| JavaScript (frontend) | TypeScript | medium | low | quality |
| Class components | Functional + Hooks | medium | low | modernization |
| Webpack | Vite | low | low | modernization |
| Express 4.x | Express 5.x or Fastify | low-medium | medium | modernization |
| jQuery | Vanilla JS / React | high | medium | modernization |
| No tests | pytest / vitest scaffold | medium | low | quality |
| No CI/CD | GitHub Actions template | low | low | devops |
| No Docker | Dockerfile + compose | low | low | devops |
| SQLAlchemy 1.x | SQLAlchemy 2.x | medium | medium | modernization |
| Pydantic v1 | Pydantic v2 | medium | medium | modernization |
| No linter | Ruff / ESLint config | low | low | quality |
| `requirements.txt` (unpinned) | `pyproject.toml` with pins | low | low | quality |
| No `.env.example` | Add `.env.example` template | low | low | devops |
| React class → hooks | Hooks + functional components | medium | low | modernization |

Function:
- `recommend_migrations(stack_profile, pattern_findings, version_currency) -> list[MigrationRecommendation]`
  ```
  MigrationRecommendation = {
    "id": "MIG01",
    "from_state": "JavaScript frontend",
    "to_state": "TypeScript frontend",
    "effort": "medium",        # "low" | "medium" | "high"
    "risk": "low",             # "low" | "medium" | "high"
    "priority": "high",        # computed from severity + effort + risk
    "category": "quality",
    "rationale": "TypeScript catches type errors at compile time...",
    "steps": [
      "Add tsconfig.json with strict mode",
      "Rename .js/.jsx to .ts/.tsx",
      "Add type annotations incrementally",
      "Enable strict null checks",
    ],
    "forge_automatable": true,  # can Forge builder do this?
  }
  ```
- Priority is computed: high severity + low effort = high priority; low severity + high effort = low priority.

**Tests:** 10 tests — migration matching, priority ranking, empty inputs, combinations.

### 37.4 — Renovation Plan Generator

New function in `app/services/upgrade_service.py`:

The main orchestrator that combines all sub-analyses into a single **Renovation Plan**.

```python
async def generate_renovation_plan(
    user_id: UUID,
    run_id: UUID,          # scout deep-scan run ID
    include_llm: bool = True,
) -> RenovationPlan
```

**Flow:**
1. Load the Scout dossier from `run_id` (via `get_scout_dossier()`)
2. Extract `stack_profile`, `architecture`, `dossier`, `file_contents` (from stored results)
3. Run version currency checks on all detected dependencies
4. Run pattern analyzer on stack + architecture + files
5. Run migration recommender using findings from steps 3-4
6. If `include_llm`: make one LLM call to generate an **executive renovation brief** — a narrative summary of the top 3-5 priorities, explaining why they matter for this specific project
7. Assemble into `RenovationPlan`:

```
RenovationPlan = {
  "id": UUID,
  "scout_run_id": UUID,
  "repo_name": "org/repo",
  "generated_at": "2026-02-17T...",
  "executive_brief": "This project is a FastAPI+React application... The top priorities are..." | null,
  "version_report": [
    {"package": "fastapi", "current": "0.95", "latest": "0.115", "status": "outdated", "detail": "..."},
    ...
  ],
  "pattern_findings": [PatternFinding, ...],
  "migration_recommendations": [MigrationRecommendation, ...],
  "summary_stats": {
    "total_findings": 12,
    "high_priority": 3,
    "medium_priority": 5,
    "low_priority": 4,
    "automatable_count": 6,       # how many Forge can do automatically
    "estimated_total_effort": "medium",  # overall effort bucket
  },
  "forge_spec": { ... } | null,    # optional forge.json fragment for automated renovation
}
```

**DB:** Store renovation plans in `scout_runs.results` as an additional key, or in a new `renovation_plans` table. Decision: extend `scout_runs.results` JSONB to include `"renovation_plan": {...}` — avoids a new table and keeps the data associated with the scan.

**Tests:** 8 tests — full plan generation, LLM fallback, empty dossier, priority sorting.

### 37.5 — Forge Spec Generator (Optional Auto-Fix)

New function `generate_forge_spec()` in `upgrade_service.py`:

For migration recommendations marked `forge_automatable: true`, generate a `forge.json`-compatible specification that Forge's builder could execute. This bridges the gap between "here's what to improve" and "let Forge do it for you".

```python
def generate_forge_spec(
    repo_name: str,
    migrations: list[MigrationRecommendation],
) -> dict | None
```

Output example:
```json
{
  "project": "org/repo",
  "type": "renovation",
  "tasks": [
    {
      "id": "MIG-CI01",
      "description": "Add GitHub Actions CI pipeline",
      "action": "create_file",
      "path": ".github/workflows/ci.yml",
      "template": "github_actions_python",
    },
    {
      "id": "MIG-ENV01",
      "description": "Add .env.example",
      "action": "create_file",
      "path": ".env.example",
      "derive_from": ".env",
    }
  ]
}
```

Only generates specs for **low-risk, template-able** tasks (CI setup, dockerfile, linter config, env template). Does NOT attempt risky refactors (framework migrations, ORM upgrades).

**Tests:** 5 tests — spec generation for automatable tasks, empty when no automatable, correct task structure.

### 37.6 — REST Endpoints

New endpoints in `app/api/routers/scout.py` (extends existing router):

| Method | Path | Description |
|---|---|---|
| `POST` | `/scout/runs/{run_id}/upgrade-plan` | Generate renovation plan from a completed deep scan |
| `GET`  | `/scout/runs/{run_id}/upgrade-plan` | Retrieve a previously generated renovation plan |

Request body for POST:
```json
{
  "include_llm": true
}
```

Response: the full `RenovationPlan` object.

**Auth:** Same user-ownership check as existing Scout endpoints.

**Tests:** 6 tests — generate, retrieve, 404, wrong user, not-deep-scan, already-exists.

### 37.7 — Frontend: Upgrade Advisor UI

New UI in `web/src/pages/Scout.tsx` (integrated into existing Scout page):

**Trigger:** After viewing a deep scan dossier, a "Generate Upgrade Plan" button appears. Clicking triggers `POST /scout/runs/{run_id}/upgrade-plan`.

**Plan View** (new view state `'upgrade_plan'`):

1. **Executive Brief** — narrative card at the top (if LLM available)
2. **Version Report Table** — sortable table showing each dependency, current vs latest, status badge (🟢 Current / 🟡 Outdated / 🔴 EOL)
3. **Pattern Findings** — cards grouped by category (quality / security / maintainability / devops), severity badges
4. **Migration Recommendations** — prioritized list with effort/risk badges, expandable detail showing steps
5. **Summary Bar** — total findings, high/medium/low breakdown, automatable count
6. **Forge Spec** (if generated) — preview of automatable tasks with "Send to Forge Builder" button (future integration, disabled for now)

**Navigation:** From dossier view → "Generate Upgrade Plan" → plan view. "← Back to Dossier" returns to dossier. History entries for deep scans show "View Plan" button if plan exists.

**Tests:** Frontend component tests for plan view rendering.

### 37.8 — Tests & Exit Criteria

**New test files:**
- `tests/test_version_db.py` — 10 tests
- `tests/test_pattern_analyzer.py` — 15 tests
- `tests/test_migration_advisor.py` — 10 tests
- `tests/test_upgrade_service.py` — 8 tests
- `tests/test_upgrade_endpoints.py` — 6 tests
- Frontend tests for upgrade plan UI

**Exit criteria:**
- `POST /scout/runs/{run_id}/upgrade-plan` generates a complete renovation plan
- Version currency check correctly identifies current/outdated/EOL packages
- Pattern analyzer detects at least 15 anti-patterns across Python and Node stacks
- Migration recommender produces prioritized, effort-estimated recommendations
- Priority ranking: high severity + low effort = urgent; low severity + high effort = deferred
- LLM executive brief provides project-specific narrative; graceful fallback to heuristic-only
- Forge spec generated only for safe, template-able tasks
- Frontend displays plan with sortable/filterable tables and expandable recommendations
- All existing tests pass + 49+ new tests pass
- `run_audit.ps1` passes all checks

---

## Phase 38 — Forge Seal: Build Certificate & Handoff  *(DONE)*

**Status:** ✅ COMPLETE  
**Commit:** *(pending)*  
**Tests:** 804 backend (68 new) + 68 frontend (7 new) = 872 total

**Objective:** Create a comprehensive **build certificate** tool that aggregates data from every ForgeGuard system (audit engine, test runner, governance gate, cost tracker, Scout dossier) into a formal handoff document. The certificate provides a quality benchmark, consistency score, and professional sign-off for any Forge-built application.

**Background:** When a user receives a completed build from Forge, they should get more than just code. The Forge Seal is a verifiable certificate that documents exactly what was built, how it was verified, and how confident the system is in the output. It's the "certificate of authenticity" for AI-built software.

**Depends on:** Phase 36 (Scout Enhancement) for project analysis capabilities. Phases 1-35 for all the systems being aggregated.

---

### 38.1 — Data Aggregator (`app/services/certificate_aggregator.py`)

Pulls data from every ForgeGuard subsystem into a unified `CertificateData` dict:

| Source | Table / Service | Data pulled |
|--------|----------------|-------------|
| **Project** | `project_repo.get_project_by_id` | name, description, repo, status |
| **Build** | `build_repo.get_latest_build_for_project`, `get_build_stats`, `get_build_cost_summary` | phase, status, loop_count, stats (turns, commits, files), cost (tokens, USD) |
| **Audit** | `audit_repo.get_audit_runs_by_repo` | recent audit runs, overall_result frequency |
| **Governance** | `audit_repo.get_audit_run_detail` → per check | check pass/fail/warn breakdown |
| **Scout** | `scout_repo.get_scout_runs_by_repo` → latest deep scan | stack_profile, architecture, dossier quality_assessment, checks |
| **Contracts** | `project_repo.get_contracts_by_project` | contract types and count |

Function: `async def aggregate_certificate_data(project_id: UUID, user_id: UUID) -> CertificateData`

### 38.2 — Scoring Engine (`app/services/certificate_scorer.py`)

Pure-function scoring across 6 dimensions, each 0–100:

| Dimension | Weight | Inputs | Scoring logic |
|-----------|--------|--------|---------------|
| **Build Integrity** | 20% | build status, loop_count, error_detail | completed=100, error penalties, loop bonus |
| **Test Coverage** | 20% | checks_passed / total, warnings | pass_rate × 100, warn penalty |
| **Audit Compliance** | 20% | audit pass rate across recent runs | avg(overall=="PASS") × 100 |
| **Governance** | 15% | per-check breakdown (A1-A9, W1-W3) | weighted pass rate |
| **Security** | 15% | W1 secrets scan, Scout patterns | clean=100, secrets found=penalty |
| **Cost Efficiency** | 10% | cost_usd, whether under cap | under_cap + reasonable_cost bonus |

Overall = weighted sum → Verdict: **CERTIFIED** (≥90) / **CONDITIONAL** (70–89) / **FLAGGED** (<70)

Function: `def compute_certificate_scores(data: CertificateData) -> CertificateScores`

### 38.3 — Certificate Renderer (`app/services/certificate_renderer.py`)

Produces three output formats from `CertificateScores`:

1. **JSON** — machine-readable, includes all scores, sections, metadata, integrity hash
2. **HTML** — styled certificate page with ForgeGuard branding, score gauges, section cards, verification hash
3. **Plain text** — compact summary for logs/CLI

Verification hash: `SHA-256(json_payload + Settings.SECRET_KEY)` — recipients can verify by recomputing

Function: `def render_certificate(scores: CertificateScores, format: str) -> str`

### 38.4 — REST Endpoints (in `app/api/routers/projects.py`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/projects/{id}/certificate` | Generate & return JSON certificate |
| `GET` | `/projects/{id}/certificate/html` | Return styled HTML certificate |

### 38.5 — Frontend Certificate UI (`web/src/pages/Projects.tsx` or new component)

- "View Certificate" button on project detail (if build completed)
- Certificate modal/page with:
  - Overall verdict badge (CERTIFIED / CONDITIONAL / FLAGGED)
  - Score radial gauges per dimension
  - Section breakdown cards
  - Download JSON / View HTML buttons
  - Verification hash display

### 38.6 — Tests + Audit + Commit

- `tests/test_certificate_aggregator.py` — ~8 tests  
- `tests/test_certificate_scorer.py` — ~12 tests  
- `tests/test_certificate_renderer.py` — ~6 tests  
- `tests/test_certificate_endpoint.py` — ~4 tests  
- Full suite regression, evidence, commit + push

---

## Phase 39 — Scout Intelligence Hardening: Metrics, Rubric & Risk Detection  *(DONE)*

**Status:** 🔨 IN PROGRESS

**Objective:** Transform Scout's quality assessment from an ungrounded LLM guess into a **metrics-first, evidence-backed system**. Compute hard numbers deterministically (free, reproducible, instant), detect concrete risks and code smells without the LLM, give the LLM a structured scoring rubric so its output is consistent and verifiable, and track scores across scans so users can see their repo improving over time.

**Principle:** *Measure first, narrate second.* The LLM becomes the storyteller, not the judge.

**Background:** Currently the deep scan sends ~6KB of code samples plus stack/architecture data to the LLM and asks it to produce a quality score 0–100 with no rubric. The score is non-deterministic and ungrounded. Risk areas are the LLM's opinion rather than detected facts. There is no scan-over-time comparison.

**Depends on:** Phase 36 (Scout deep scan), Phase 37 (Upgrade Advisor for dependency data).

---

### 39.1 — Deterministic Metrics Engine (`app/services/scout_metrics.py`)

A pure-function module that takes the deep scan artifacts (tree, stack_profile, architecture, file_contents, checks) and computes hard numbers — no LLM, no IO.

| Metric | Computation | Score contribution |
|--------|-------------|-------------------|
| **test_file_ratio** | Count files matching `test_*` / `*_test.*` / `*_spec.*` / `__tests__/` ÷ total source files | 0–20 |
| **doc_coverage** | Existence & size of README + docs/ + docstrings in entry points | 0–20 |
| **dependency_health** | Pinned vs unpinned deps, lock file presence, dep count | 0–20 |
| **code_organization** | Max file size (flag >500 lines), directory depth, separation of concerns, entry point clarity | 0–20 |
| **security_posture** | Secrets patterns, `.env` committed, raw SQL, `eval()`/`exec()`, missing `.gitignore` | 0–20 |

Function: `def compute_repo_metrics(tree_paths, file_contents, stack_profile, architecture, checks) -> RepoMetrics`

Returns: `{ scores: { dimension: {score, details} }, computed_score: 0-100, file_stats: {...}, smells: [...] }`

### 39.2 — Actionable Risk & Smell Detector (within `scout_metrics.py`)

Deterministic pattern detection — concrete, verifiable problems.

| Detector | What it finds | Severity |
|----------|--------------|----------|
| **secrets_in_source** | `AKIA`, `sk-`, `ghp_`, `password=`, hardcoded tokens | HIGH |
| **env_committed** | `.env` in tree, not gitignored | HIGH |
| **no_gitignore** | Missing `.gitignore` | MEDIUM |
| **no_license** | Missing LICENSE file | LOW |
| **unpinned_deps** | Deps without version pins | MEDIUM |
| **large_files** | Source files >500 lines | MEDIUM |
| **raw_sql** | f-string SQL construction | HIGH |
| **eval_exec** | `eval()` / `exec()` calls | HIGH |
| **no_tests** | Zero test files | HIGH |
| **missing_readme** | No README at root | MEDIUM |
| **todo_fixme_density** | >10 TODO/FIXME/HACK per 1K lines | LOW |

Function: `def detect_smells(tree_paths, file_contents) -> list[SmellReport]`

### 39.3 — Structured Scoring Rubric in LLM Prompt

Replace the unstructured system prompt with a rubric-based prompt that receives computed metrics and narrates them rather than guessing scores. The LLM uses `computed_score` as `quality_assessment.score` and turns detected smells into `risk_areas`.

Updated `_generate_dossier()` signature: adds `metrics: RepoMetrics` parameter.  
Updated user message: includes `Computed Metrics:` and `Detected Smells:` sections.

### 39.4 — Score-Over-Time Tracking

- **Migration:** Add `computed_score INTEGER` column to `scout_runs`
- **Repo function:** `get_score_history(repo_id, user_id) -> [{scan_date, computed_score, scan_type}]`
- **API endpoint:** `GET /scout/{repo_id}/score-history`
- **Deep scan integration:** Persist `computed_score` after metrics computation; include metrics in results JSONB
- **Frontend:** Score trend sparkline (inline SVG mini-chart) in the dossier view

### 39.5 — Frontend Enhancements (`web/src/pages/Scout.tsx`)

1. **Metrics breakdown card** — 5 dimensions as horizontal progress bars (0–20 each)
2. **Detected smells list** — severity-tagged expandable list with file references
3. **Score trend sparkline** — mini line chart showing computed_score across last N deep scans
4. **"Computed vs LLM" badges** — "📐 Measured" for deterministic sections, "🤖 AI Analysis" for LLM-narrated

### 39.6 — Tests + Audit + Commit

- `tests/test_scout_metrics.py` — ~25 tests (metrics + smell detection)
- `tests/test_scout_dossier_prompt.py` — ~5 tests (updated prompt, metrics passthrough)
- `tests/test_scout_score_history.py` — ~5 tests (repo function + endpoint)
- Frontend tests for new components
- Full suite regression, evidence, commit + push

---
---

# ForgeIDE — Cognitive Architecture Phases

The phases below encode the **five cognitive patterns** that enable a coding agent to maintain accuracy, momentum, and zero-regression across long multi-phase build sessions. These are the patterns observed in high-performance agentic workflows (Copilot agent mode, Cursor, Windsurf) — reverse-engineered and formalised as implementable ForgeIDE features.

Each phase is self-contained and builds on the previous. Together they transform the builder from a "fire-and-hope" single-call generator into a **stateful, self-correcting, context-aware autonomous developer**.

**Dependency chain:** Phase 40 → 41 → 42 → 43 → 44

---

## Phase 40 — Structured Reconnaissance

**Objective:** Before writing a single line of code, the builder performs a systematic inventory of the existing codebase — gathering imports, exports, schemas, test counts, file structure, and dependency graphs. This inventory becomes the **ground truth** that all subsequent planning and code generation references.

**Background — why blind generation fails:**

The current builder receives contracts (blueprint, schema, stack, phases) and starts generating code based on those specifications alone. It has no awareness of what already exists in the workspace. This causes:
1. **Import collisions** — generated code imports from modules that don't exist yet, or uses wrong import paths
2. **Schema drift** — generated models don't match the actual database tables
3. **Duplicate functionality** — builder recreates utilities that already exist
4. **Test fragility** — new code breaks existing tests the builder didn't know about

IDE-based agents (Copilot, Cursor) solve this by having full workspace access via tools. ForgeIDE's builder needs the same awareness, but gathered **once** upfront and maintained incrementally — not re-explored every turn.

**Deliverables:**

### 40.1 — Workspace Snapshot Engine (`forge_ide/workspace.py`)

A module that produces a structured `WorkspaceSnapshot` from any project directory.

- **`async def capture_snapshot(working_dir: Path) -> WorkspaceSnapshot`**
  - Walks the directory tree (respecting `.gitignore`, skipping `node_modules`, `__pycache__`, `.git`, `venv`)
  - For each source file (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`):
    - Extract: path, line count, language, top-level exports (functions, classes, constants)
    - For Python: parse with `ast` module — collect `class`, `def`, `import`, `from ... import` at module level
    - For TypeScript: regex-based extraction of `export function`, `export class`, `export const`, `export default`, `export interface`, `export type`
  - Produce a **symbol table**: `{ "app.config.Settings": "class", "app.main.app": "FastAPI instance", ... }`
  - Produce a **dependency graph**: `{ "app/main.py": ["app/config", "app/auth", "app/api.routers..."] }`
  - Produce a **file tree string**: indented directory listing with line counts
  - Produce **test inventory**: list of test files, count of test functions (functions starting with `test_`), total test count
  - Produce **schema inventory**: if `db/migrations/` exists, extract table names and columns from SQL files; if `alembic/versions/` exists, parse migration files

- **`WorkspaceSnapshot` dataclass:**
  ```python
  @dataclass
  class WorkspaceSnapshot:
      file_tree: str                              # indented directory listing
      symbol_table: dict[str, str]                # dotted.path -> kind (class/function/const)
      dependency_graph: dict[str, list[str]]      # file -> [imported modules]
      test_inventory: TestInventory               # test files, test count, frameworks
      schema_inventory: SchemaInventory           # tables, columns, migrations
      total_files: int
      total_lines: int
      languages: dict[str, int]                   # language -> line count
      captured_at: datetime
  ```

- **Incremental update**: `async def update_snapshot(snapshot: WorkspaceSnapshot, changed_files: list[Path]) -> WorkspaceSnapshot` — re-scans only changed files and updates the symbol table + dependency graph without a full walk

### 40.2 — Reconnaissance Phase in Build Loop

Insert a **recon step** at the start of each phase in `_run_build()`, before the planning call.

- Before calling `_generate_file_manifest()`, capture (or update) the workspace snapshot
- Pass the snapshot to the planner so it knows:
  - What files already exist (don't recreate them)
  - What symbols are available (use correct import paths)
  - What tests exist (don't break them)
  - What DB tables exist (match schema exactly)
- The planner's context now includes: contracts + snapshot + phase deliverables
- Emit `recon_complete` WS event: `{ total_files, total_lines, test_count, tables, symbols_count }`

### 40.3 — Context Pack Builder (`forge_ide/context_pack.py`)

Builds minimal, relevant context for each file generation call. Instead of sending ALL contracts to every call, selects only what's relevant.

- **`def build_context_pack(file_entry: ManifestEntry, snapshot: WorkspaceSnapshot, contracts: dict) -> ContextPack`**
  - For a Python backend file:
    - Include: `blueprint.md`, `schema.md`, `stack.md`, `boundaries.json`
    - Include: symbol table entries for modules this file will import from (from `depends_on` + dependency graph)
    - Include: full content of `context_files` listed in the manifest
    - Exclude: `ui.md`, frontend contracts, unrelated phases
  - For a React frontend file:
    - Include: `ui.md`, `blueprint.md`, `stack.md`
    - Include: TypeScript type exports from shared types/interfaces
    - Exclude: Python-specific contracts, schema.md (unless it defines API response shapes)
  - For a test file:
    - Include: the implementation file it tests (full content)
    - Include: existing test patterns (first test file from the same directory as an example)
    - Include: test framework info from snapshot
  - For a migration file:
    - Include: `schema.md`, existing migration files (to maintain numbering)
    - Include: current schema inventory from snapshot

- **Token budget enforcement**: each `ContextPack` has a max token budget (default 30K). If the selected context exceeds the budget, prioritise: (1) target file's direct dependencies, (2) contracts, (3) examples — and truncate the rest with `[... truncated ...]` markers.

### 40.4 — Tests

- Test `capture_snapshot` on a fixture directory with known Python + TypeScript files → verify symbol table, dependency graph, test count
- Test `update_snapshot` with changed files → verify incremental update correctness
- Test `build_context_pack` for Python backend file → verify correct contracts selected, correct symbols included
- Test `build_context_pack` for React file → verify ui.md included, schema.md excluded
- Test `build_context_pack` token budget enforcement → verify truncation at limit
- Test recon step integration → verify snapshot is captured before planning call

**Schema coverage:** No new tables.

**Exit criteria:**
- Workspace snapshot captures file tree, symbol table, dependency graph, test inventory, and schema inventory
- Incremental snapshot update works for changed files without full re-scan
- Context packs contain only relevant contracts and symbols for each file type
- Token budget enforcement prevents context overflow
- Recon step runs before every phase's planning call
- Builder no longer generates imports for non-existent modules (verified in integration test)
- All existing tests pass + ~12 new tests for snapshot, context pack, and recon integration

---

## Phase 41 — Task DAG & Progress Tracking

**Objective:** Replace the flat task list with a **directed acyclic graph** (DAG) of tasks with explicit dependencies, estimated costs, and state tracking. The DAG serves as the builder's "program counter" — it always knows exactly what has been done, what's in progress, and what's blocked.

**Background — why flat lists fail:**

The current `plan_tasks` is a flat list of strings. There's no dependency tracking, no state machine, no cost estimation. The builder emits tasks in whatever order it wants, and the orchestrator has no way to verify that prerequisites are met before a task begins. When a task fails and requires a fix, there's no way to determine which downstream tasks are affected.

The cognitive pattern being replicated: **the todo list is the program counter**. At any moment, the builder should be able to ask "What one thing am I doing right now?" and get a single, unambiguous answer. Completed tasks are checkpoints. Blocked tasks are visible. The DAG enforces ordering.

**Deliverables:**

### 41.1 — Task DAG Data Model (`forge_ide/contracts.py`)

- **`TaskNode` dataclass:**
  ```python
  @dataclass
  class TaskNode:
      id: str                          # e.g., "p0_t1"
      title: str                       # "Create app/config.py"
      phase: str                       # "Phase 0"
      status: TaskStatus               # pending | in_progress | completed | failed | blocked | skipped
      depends_on: list[str]            # IDs of prerequisite tasks
      blocks: list[str]                # IDs of tasks this blocks (computed)
      file_path: str | None            # primary file this task produces
      estimated_tokens: int            # estimated API token cost
      actual_tokens: int               # actual cost after completion
      started_at: datetime | None
      completed_at: datetime | None
      error: str | None                # failure reason if status == failed
      retry_count: int                 # number of retry attempts
  ```

- **`TaskDAG` class:**
  ```python
  class TaskDAG:
      nodes: dict[str, TaskNode]
      
      def add_task(self, node: TaskNode) -> None
      def get_ready_tasks(self) -> list[TaskNode]          # tasks whose deps are all completed
      def mark_in_progress(self, task_id: str) -> None     # enforces single in-progress
      def mark_completed(self, task_id: str, actual_tokens: int) -> None
      def mark_failed(self, task_id: str, error: str) -> None
      def get_blocked_by(self, task_id: str) -> list[TaskNode]  # what does this failure block?
      def get_progress(self) -> DAGProgress                # {completed, total, pct, est_remaining_tokens}
      def topological_order(self) -> list[TaskNode]        # full execution order
      def to_dict(self) -> dict                            # serialisable for WS events + persistence
      def from_manifest(cls, manifest: FileManifest) -> "TaskDAG"  # factory from planner output
  ```

- **`DAGProgress` dataclass:**
  ```python
  @dataclass
  class DAGProgress:
      total: int
      completed: int
      failed: int
      blocked: int
      in_progress: int
      pending: int
      percentage: float
      estimated_remaining_tokens: int
      estimated_remaining_cost_usd: float
  ```

### 41.2 — DAG Integration in Build Loop

- After `_generate_file_manifest()` returns, convert the manifest into a `TaskDAG` via `TaskDAG.from_manifest()`
- Replace the linear file iteration with: `while ready := dag.get_ready_tasks(): process(ready[0])`
- Before processing each file: `dag.mark_in_progress(task_id)` — emits `task_started` WS event
- After successful generation: `dag.mark_completed(task_id, tokens)` — emits `task_completed` WS event
- On generation failure: `dag.mark_failed(task_id, error)` — emits `task_failed` WS event, all tasks in `blocks` become `blocked`
- Progress broadcast after each task: `dag_progress` WS event with `DAGProgress` data
- Phase-level progress: the DAG resets per phase (same as `plan_tasks` reset in Phase 16)

### 41.3 — Failure Cascade & Recovery

When a task fails:
1. Mark all downstream tasks (reachable via `blocks`) as `blocked`
2. Attempt a fix (targeted fix call, same pattern as Phase 21 verification)
3. If fix succeeds: mark task completed, unblock downstream tasks
4. If fix fails after 2 retries: leave as `failed`, report blocked cascade to user
5. At pause threshold: report the full cascade — "Task X failed, which blocks Y and Z"

### 41.4 — Frontend: DAG Visualisation

- Replace the flat task checklist with a vertically stacked DAG view
- Each task shows: status icon, title, file path, estimated/actual tokens
- Dependency lines connect tasks (simple vertical lines with indentation, not a full graph layout)
- Blocked tasks are greyed out with a lock icon
- Progress bar at the top: `{completed}/{total} tasks — {pct}% — est. {tokens} tokens remaining`
- Failed tasks show error detail on expand

### 41.5 — Tests

- Test `TaskDAG.from_manifest()` — correct node creation, dependency wiring
- Test `get_ready_tasks()` — returns only tasks with all deps completed
- Test `mark_failed()` — downstream tasks become blocked
- Test failure cascade — deep dependency chain, one failure blocks 5 tasks
- Test recovery — failed task fixed, downstream unblocked
- Test `topological_order()` — correct ordering with complex dependency graph
- Test `get_progress()` — accurate counts and percentages
- Test circular dependency detection — raises error

**Schema coverage:** No new tables — DAG state is in-memory during build execution, serialised to build_logs for persistence.

**Exit criteria:**
- File manifest converts to a dependency-aware DAG with correct ordering
- Build loop processes tasks in dependency order (no task runs before its prerequisites)
- Task failures cascade to block downstream tasks
- Failed tasks can be fixed and downstream tasks unblocked
- Progress tracking is accurate (counts, percentages, estimated remaining cost)
- DAG state is broadcast via WebSocket and displayed in the frontend
- All existing tests pass + ~15 new tests for DAG operations, cascade, and recovery

---

## Phase 42 — Patch Retargeting & Self-Healing Edits

**Objective:** When the builder needs to modify an existing file (fix a bug, update an import, add a method), it generates a **targeted patch** rather than rewriting the entire file. If the file has changed since the builder last saw it (due to a previous fix or a parallel task), the patch engine automatically retargets to the new file content.

**Background — why full-file rewrites are dangerous:**

The current builder always generates complete file content via `write_file`. When fixing a bug in a 200-line file, it regenerates all 200 lines — and often introduces regressions in the parts it didn't mean to change (dropped imports, altered function signatures, reformatted code). IDE-based agents use surgical edits (replace lines 45-52 with new content). ForgeIDE needs the same capability.

The cognitive pattern being replicated: **patch retargeting**. When you read a file at line 100 and later try to edit it, but someone (or a previous fix) has shifted line 100 to line 107, the edit engine finds the correct location by matching surrounding context rather than trusting line numbers.

**Deliverables:**

### 42.1 — Diff Generator (`forge_ide/diff_generator.py`)

Generate surgical patches instead of full-file rewrites for modification tasks.

- **`def generate_edit_instruction(original: str, purpose: str, contracts: dict, snapshot: WorkspaceSnapshot) -> EditInstruction`**
  - Makes an API call (Sonnet, not Opus — cheaper for targeted edits) with:
    - The original file content
    - The modification purpose (e.g., "Add `get_user_by_email` method to UserRepo class")
    - Relevant context from the snapshot (what modules import this file, what tests cover it)
  - System prompt instructs the model to output a structured edit, NOT the complete file:
    ```
    Output your edit as a JSON object:
    {
      "edits": [
        {
          "anchor": "three lines of unchanged code above the edit point",
          "old_text": "the exact text being replaced (include 3 lines of context before and after)",
          "new_text": "the replacement text",
          "explanation": "why this change is needed"
        }
      ]
    }
    ```
  - Returns `EditInstruction` with a list of surgical edits

- **`EditInstruction` dataclass:**
  ```python
  @dataclass
  class Edit:
      anchor: str          # context lines to locate the edit point
      old_text: str        # exact text to replace
      new_text: str        # replacement text
      explanation: str
  
  @dataclass
  class EditInstruction:
      file_path: str
      edits: list[Edit]
      full_rewrite: bool   # fallback flag — if True, new_text is the complete file
  ```

### 42.2 — Patch Application Engine (`forge_ide/patcher.py`)

Apply edits to files with automatic retargeting when content has shifted.

- **`def apply_edits(file_path: Path, edits: list[Edit]) -> PatchResult`**
  - For each edit:
    1. **Exact match**: try `file_content.find(old_text)` — if found at exactly one location, replace
    2. **Fuzzy match**: if exact match fails, use the `anchor` text to locate the region, then find `old_text` within ±20 lines of the anchor
    3. **Difflib fallback**: if fuzzy match fails, use `difflib.SequenceMatcher` to find the closest match above a similarity threshold (0.85)
    4. **Abort**: if no match found above threshold, mark the edit as failed and return the error
  - Track all applied edits for logging
  - Return `PatchResult` with: applied edits, failed edits, final file content

- **`PatchResult` dataclass:**
  ```python
  @dataclass
  class PatchResult:
      success: bool
      applied: list[Edit]
      failed: list[tuple[Edit, str]]  # (edit, reason)
      final_content: str
      retargeted: int                 # how many edits needed fuzzy/difflib matching
  ```

### 42.3 — Retargeting Integration

When an edit fails to apply (file changed since the builder last saw it):

1. Re-read the current file content
2. Attempt retargeting via fuzzy match / difflib
3. If retargeting succeeds: apply and log "Edit retargeted from line N to line M"
4. If retargeting fails: fall back to a **targeted fix call** — send the current file + the intended change + "Apply this modification to the current file" — and receive the complete modified file
5. Last resort: full-file rewrite (the current behaviour, but now it's the fallback, not the default)

### 42.4 — Build Loop Integration

- Manifest entries gain an `action` field: `"create"` (new file) or `"modify"` (edit existing file)
- For `"create"` actions: use the existing full-file generation flow
- For `"modify"` actions: use `generate_edit_instruction()` + `apply_edits()`
- The verification step (Phase 21) uses `apply_edits()` for fixes instead of full-file regeneration
- The recovery planner's fix calls use `apply_edits()` for surgical corrections

### 42.5 — `write_file` Tool Enhancement

Update the builder's `write_file` tool to support a `mode` parameter:
- `mode: "create"` — write complete file (current behaviour)
- `mode: "edit"` — apply a surgical patch (new behaviour)
  - Additional parameters: `anchor`, `old_text`, `new_text`
  - Uses `apply_edits()` internally

### 42.6 — Tests

- Test exact match application — simple edit on unchanged file
- Test fuzzy retargeting — file has new lines inserted above the edit point, edit still applies correctly
- Test difflib retargeting — old_text has minor whitespace differences, still matches
- Test retarget failure — file completely rewritten, no match found → fallback to full rewrite
- Test multiple edits on same file — all apply in order
- Test `generate_edit_instruction()` with mocked API — correct prompt assembly, correct parsing
- Test `write_file` tool in edit mode — surgical patch via tool call
- Test build loop with `"modify"` manifest entries — uses edit flow, not full-file generation

**Schema coverage:** No new tables.

**Exit criteria:**
- Modification tasks generate surgical patches, not full-file rewrites
- Patches retarget automatically when files have shifted (fuzzy + difflib matching)
- Failed retargeting falls back to targeted fix call, then full rewrite
- `write_file` tool supports both create and edit modes
- Verification fixes use surgical edits instead of full-file regeneration
- Retargeting is logged (how many edits needed fuzzy matching)
- All existing tests pass + ~14 new tests for diff generation, patch application, retargeting, and fallback

---

## Phase 43 — Session Journal & Context Continuity

**Objective:** Implement a persistent **session journal** that survives context window rotations, API timeouts, and process restarts. The journal captures every significant event (task completed, test baseline changed, file written, error encountered) so the builder can resume from any point without re-discovering what it has already done.

**Background — why context windows kill multi-phase builds:**

A single Anthropic API call has a 200K token context window. A full build across 10+ phases easily exceeds this. The current `_compact_conversation()` handles this by summarising old turns — but summarisation is lossy. The builder forgets specific import paths, exact test counts, and which files it already wrote. When it resumes after compaction, it explores the workspace again (wasting tokens) and sometimes contradicts its own earlier work.

The cognitive pattern being replicated: **the conversation summary is the journal**. At the end of each work block, a structured summary is written capturing: what was accomplished, what the invariants are (test count, file count), what the current state is, and what remains. When context rotates, the journal is loaded as a compressed but complete state document — the builder picks up exactly where it left off.

**Deliverables:**

### 43.1 — Session Journal Data Model (`forge_ide/runner.py` or new module)

- **`SessionJournal` class:**
  ```python
  class SessionJournal:
      build_id: str
      entries: list[JournalEntry]
      invariants: dict[str, Any]      # tracked invariants (test_count, file_count, etc.)
      
      def record(self, event_type: str, detail: str, metadata: dict = None) -> None
      def set_invariant(self, key: str, value: Any) -> None
      def check_invariant(self, key: str, current: Any) -> InvariantResult
      def get_summary(self, max_tokens: int = 4000) -> str   # compressed summary for context injection
      def get_checkpoint(self) -> JournalCheckpoint            # serialisable state for persistence
      def restore_from_checkpoint(cls, checkpoint: JournalCheckpoint) -> "SessionJournal"
      def to_context_block(self) -> str                        # formatted for injection into API prompts
  ```

- **`JournalEntry` dataclass:**
  ```python
  @dataclass
  class JournalEntry:
      timestamp: datetime
      event_type: str          # task_completed | test_run | file_written | error | recon | phase_start | phase_complete | invariant_set | invariant_violated
      phase: str
      task_id: str | None
      detail: str              # human-readable description
      metadata: dict           # structured data (tokens, file paths, test counts, etc.)
  ```

- **`JournalCheckpoint` dataclass** (serialisable to JSON for DB persistence):
  ```python
  @dataclass
  class JournalCheckpoint:
      build_id: str
      phase: str
      task_dag_state: dict       # serialised DAG with task statuses
      invariants: dict[str, Any]
      files_written: list[str]
      snapshot_hash: str         # hash of workspace snapshot for drift detection
      compressed_history: str    # the journal summary text
      created_at: datetime
  ```

### 43.2 — Journal Recording in Build Loop

Instrument the build loop to record every significant event:

- **Phase start**: `journal.record("phase_start", f"Beginning {phase}", {"phase": phase, "task_count": dag.total})`
- **Task completed**: `journal.record("task_completed", f"Generated {file_path}", {"task_id": id, "tokens_in": n, "tokens_out": m, "file_path": path})`
- **Test run**: `journal.record("test_run", f"Tests: {passed} passed, {failed} failed", {"passed": n, "failed": m, "baseline": baseline})`
- **Error**: `journal.record("error", f"Generation failed for {path}: {error}", {"task_id": id, "error": str})`
- **Invariant set**: `journal.record("invariant_set", f"Test baseline: {count}", {"key": "test_count", "value": count})`
- **Phase complete**: `journal.record("phase_complete", f"Phase {phase} done", {"tasks_completed": n, "tokens_total": t})`

### 43.3 — Context Window Rotation

Replace `_compact_conversation()` with journal-based context management:

1. Before each API call, calculate total conversation tokens
2. If approaching the limit (80% of context window):
   - Write a journal checkpoint
   - Generate a journal summary: `journal.get_summary(max_tokens=4000)`
   - Replace the conversation history with: `[system_prompt, journal_summary_as_user_message, last_2_turns]`
   - The journal summary contains: all completed tasks, current invariants, current phase position, files written, recent errors
3. The builder receives a **dense, structured state document** instead of a lossy conversation summary
4. The builder can immediately continue without re-exploring — it knows what exists, what was done, and what's next

### 43.4 — Checkpoint Persistence

- Save journal checkpoints to the `build_logs` table (source: `journal`, message: serialised JSON)
- On build resume (after pause, crash, or restart): load the latest checkpoint, restore the journal, restore the DAG state
- `POST /projects/{id}/build/resume` now also restores the journal from the checkpoint
- Checkpoint saved after every phase completion and every pause event

### 43.5 — Journal Summary Format

The `get_summary()` method produces a structured text block optimised for LLM context injection:

```
=== SESSION JOURNAL (Build {id}, Phase {phase}) ===

## Completed Phases
- Phase 0 (Genesis): 5/5 tasks ✓ — 12,340 tokens
- Phase 1 (Auth): 8/8 tasks ✓ — 28,100 tokens

## Current Phase: Phase 2 (Repos)
- Tasks: 3/7 completed, 1 in progress, 3 pending
- Current task: Create app/api/routers/repos.py

## Invariants
- Backend test count: 45 (baseline: 45) ✓
- Frontend test count: 12 (baseline: 12) ✓
- Total files written: 18

## Files Written This Phase
- app/repos/repo_repo.py (42 lines)
- app/services/repo_service.py (78 lines)
- tests/test_repo_service.py (95 lines)

## Recent Events (last 5)
- [14:32:01] Generated tests/test_repo_service.py — 3,200 tokens
- [14:31:45] Generated app/services/repo_service.py — 2,800 tokens
- [14:31:20] Generated app/repos/repo_repo.py — 1,200 tokens
- [14:31:00] Test run: 45 passed, 0 failed ✓
- [14:30:45] Phase 2 started — 7 tasks planned

=== END JOURNAL ===
```

### 43.6 — Tests

- Test `SessionJournal.record()` — entries stored with correct timestamps and types
- Test `get_summary()` — produces structured text within token budget
- Test `get_checkpoint()` + `restore_from_checkpoint()` — round-trip serialisation preserves all state
- Test context rotation — conversation replaced with journal summary, builder continues correctly (mocked)
- Test checkpoint persistence to build_logs — serialised, retrievable
- Test build resume from checkpoint — DAG state, invariants, and files_written restored
- Test journal with 500+ entries — summary still within token budget, includes most recent events

**Schema coverage:** No new tables — checkpoints stored in build_logs.

**Exit criteria:**
- Every significant build event is recorded in the session journal
- Journal summary provides a dense, structured state document suitable for context injection
- Context window rotation uses journal summary instead of lossy conversation compaction
- Checkpoints persist to the database and can be restored after pause/crash/restart
- Build resume from checkpoint restores full state (DAG, invariants, files_written)
- Builder maintains accuracy after context rotation (verified in integration test with mocked agent)
- All existing tests pass + ~12 new tests for journal operations, summarisation, checkpointing, and restoration

---

## Phase 44 — Invariant Gates & Test Baseline Tracking

**Objective:** Implement a **hard invariant system** that prevents the build from ever regressing on key metrics. The test count is the primary invariant: it must never decrease. If a code change causes a test to fail or a test file to be deleted, the build halts immediately — before committing, before moving on, before the error propagates into downstream phases.

**Background — why "929 passed" matters:**

In a long build session, the most dangerous failure mode is **silent regression**. The builder writes new code that breaks an existing test. If the break isn't caught immediately, the builder continues building on top of broken code. By the time the audit catches it (if it catches it at all), the fix requires unwinding multiple phases of work. The solution: after every file write, run the test suite and compare against the invariant baseline. If the count drops, stop immediately.

The cognitive pattern being replicated: **the test baseline is the invariant**. After every phase, the builder records "929 tests pass." Before starting the next phase, it runs tests and verifies "929 tests still pass." If the count drops to 928, something broke — stop and fix before continuing. The test count only ever goes up.

**Deliverables:**

### 44.1 — Invariant Registry (`forge_ide/registry.py`)

A generalised invariant system that tracks named values and enforces monotonic or equality constraints.

- **`InvariantRegistry` class:**
  ```python
  class InvariantRegistry:
      invariants: dict[str, Invariant]
      
      def register(self, name: str, value: Any, constraint: Constraint) -> None
      def check(self, name: str, current_value: Any) -> InvariantResult
      def check_all(self, current_values: dict[str, Any]) -> list[InvariantResult]
      def update(self, name: str, new_value: Any) -> None  # only if new value satisfies constraint
  ```

- **`Constraint` enum:**
  - `MONOTONIC_UP` — value must be >= previous (test count: can only go up)
  - `MONOTONIC_DOWN` — value must be <= previous (error count: can only go down)
  - `EQUAL` — value must match exactly (schema hash: don't accidentally alter migrations)
  - `NON_ZERO` — value must be > 0 (at least one test must exist)

- **`InvariantResult` dataclass:**
  ```python
  @dataclass
  class InvariantResult:
      name: str
      passed: bool
      expected: Any         # the invariant value
      actual: Any           # the current value
      constraint: Constraint
      message: str          # human-readable description
  ```

### 44.2 — Built-in Invariants

Pre-registered invariants for every build:

| Invariant | Constraint | Description |
|-----------|-----------|-------------|
| `backend_test_count` | MONOTONIC_UP | Number of passing backend tests (pytest) |
| `frontend_test_count` | MONOTONIC_UP | Number of passing frontend tests (vitest) |
| `backend_test_failures` | EQUAL (to 0) | Number of failing backend tests |
| `frontend_test_failures` | EQUAL (to 0) | Number of failing frontend tests |
| `total_files` | MONOTONIC_UP | Number of files in the project |
| `migration_count` | MONOTONIC_UP | Number of DB migrations (don't delete migrations) |
| `syntax_errors` | EQUAL (to 0) | No syntax errors in any source file |

### 44.3 — Invariant Gate in Build Loop

Insert invariant checks at two critical points:

1. **After file generation** (per-file gate):
   - Run syntax check on the generated file
   - Check `syntax_errors` invariant
   - If violated: immediately fix (targeted fix call) before proceeding

2. **After phase verification** (phase gate):
   - Run the full test suite
   - Parse test output: extract passed count, failed count
   - Check `backend_test_count` invariant (must be >= baseline)
   - Check `backend_test_failures` invariant (must be 0)
   - If violated:
     1. Identify which tests broke (diff test output against previous run)
     2. Read the failing test + the file it tests
     3. Make a targeted fix call to restore the broken test
     4. Re-run tests
     5. If still broken after 2 attempts: pause build with invariant violation detail
   - If passed: update the invariant baseline (test count may have increased due to new tests)
   - Emit `invariant_check` WS event: `{ name, passed, expected, actual }`

### 44.4 — Invariant Violation Response

When an invariant is violated:

1. **Log the violation** to the session journal: `journal.record("invariant_violated", f"Test count dropped: {expected} -> {actual}", {...})`
2. **Identify the cause**: diff the workspace snapshot before and after the latest change to find what changed
3. **Attempt automatic fix**: targeted fix call with the broken test + recent changes
4. **If fix succeeds**: log recovery, update baseline, continue
5. **If fix fails**: pause the build with a detailed violation report:
   ```
   INVARIANT VIOLATION: backend_test_count
   Expected: >= 45
   Actual: 43
   Constraint: MONOTONIC_UP
   
   Failing tests:
   - tests/test_auth.py::test_jwt_decode_expired — NameError: name 'Settings' is not defined
   - tests/test_config.py::test_missing_env_var — ImportError: cannot import name 'settings'
   
   Recent changes:
   - Modified: app/config.py (renamed Settings class)
   ```

### 44.5 — Frontend: Invariant Dashboard

- **Invariant status strip** at the top of BuildProgress page:
  - Horizontal row of invariant badges: `Tests: 45 ✓` | `Errors: 0 ✓` | `Files: 18 ↑`
  - Green for passing, red for violated, amber for recently recovered
  - Clicking a badge shows history: value at each checkpoint
- **Violation alert**: when an invariant is violated, show an inline alert card (red border) with the violation details, cause analysis, and fix status

### 44.6 — Tests

- Test `InvariantRegistry.register()` + `check()` — all constraint types
- Test `MONOTONIC_UP` — value increase passes, decrease fails
- Test `MONOTONIC_DOWN` — value decrease passes, increase fails
- Test `EQUAL` — exact match passes, any change fails
- Test `NON_ZERO` — positive passes, zero fails
- Test `check_all()` — multiple invariants checked, mixed results
- Test `update()` — only updates if new value satisfies constraint
- Test build loop integration — invariant gate runs after verification, violation triggers fix
- Test invariant recovery — fix restores the invariant, build continues
- Test invariant persistence — invariants saved in journal checkpoint, restored on resume

**Schema coverage:** No new tables — invariants stored in session journal checkpoints.

**Exit criteria:**
- Invariant registry supports MONOTONIC_UP, MONOTONIC_DOWN, EQUAL, and NON_ZERO constraints
- Test count is tracked as a MONOTONIC_UP invariant — it can never decrease
- Invariant gates run after every file generation (syntax) and after every phase verification (tests)
- Violations trigger automatic fix attempts before pausing
- Violation reports include: expected vs actual, failing tests, recent changes
- Invariants persist across checkpoints and are restored on resume
- Frontend shows invariant status strip and violation alerts
- All existing tests pass + ~16 new tests for registry operations, gate integration, violation response, and persistence

---

## Phase 45 — Build Observability & Cognitive Dashboard

**Objective:** Surface the Phase 40-44 cognitive architecture to the frontend.

### 45.1 Recon Summary Card
### 45.2 Task DAG Panel
### 45.3 Invariant Status Strip
### 45.4 Context Compaction Indicator
### 45.5 Journal Timeline
### 45.6 Tests

---

## Phase 46 — Contracts Server-Side Lock & Git Exclusion

**Objective:** Contracts never leave the server. Every build — mini, full, or scout — pushes code and instructions to git but never pushes Forge contract files. The contract framework is ForgeGuard's value proposition; users own their code, the framework stays behind their login.

**Deliverables:**
- **Global `.gitignore` injection** — the build setup step (`_prepare_working_dir` or equivalent) ensures every target repo's `.gitignore` includes:
  ```
  # Forge contracts (server-side only)
  Forge/
  *.forge-contract
  .forge/
  ```
  If `.gitignore` exists, append the rules (idempotently). If it doesn't exist, create it.
- **Git commit filter** — in the git push logic (`app/services/build/git_push.py` or `build_service.py`), before staging files, explicitly `git rm --cached -r Forge/` and exclude any contract files from `git add`. Belt-and-suspenders alongside `.gitignore`.
- **Builder directive update** — add an explicit instruction to the builder contract / builder directive: "Do not include Forge contract file contents, contract references, or contract metadata in any committed source files, READMEs, or comments."
- **Existing builds retroactive** — any build that already pushed contracts: no retroactive cleanup needed, but going forward all builds (full, mini, scout) enforce exclusion.
- **Tests:**
  - Test that `.gitignore` injection is idempotent (running twice doesn't duplicate rules)
  - Test that `Forge/` directory contents are never included in staged files
  - Test that existing `.gitignore` content is preserved when appending
  - Test that a fresh repo with no `.gitignore` gets one created

**Schema coverage:** No new tables.

**Exit criteria:**
- Every build (full, mini, scout) injects `Forge/` into the target repo's `.gitignore`
- Contract files are never included in git commits, verified by test
- Builder directive explicitly prohibits contract content in source files
- All existing tests pass + ~6 new tests for gitignore injection and commit filtering

---

## Phase 47 — Mini Mode: Database & API Foundation

**Objective:** Add the data model and API surface for build tiers (proof-of-concept vs full) and project modes. This is the schema and routing layer that all subsequent mini mode phases depend on.

**Deliverables:**
- **Migration `0XX_project_mode.sql`:**
  ```sql
  ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_mode VARCHAR(20) DEFAULT 'full';
  -- 'full' = standard 7-section questionnaire, 6-12 phase build
  -- 'mini' = 2-section questionnaire, 2-phase proof-of-concept build
  ```
- **Migration `0XX_build_tier.sql`:**
  ```sql
  ALTER TABLE builds ADD COLUMN IF NOT EXISTS build_tier VARCHAR(20) DEFAULT 'full';
  -- 'full' = standard multi-phase build
  -- 'mini' = 2-phase proof-of-concept build
  ```
- **`POST /projects` update** — accept optional `project_mode` field (`"full"` | `"mini"`, default `"full"`)
- **`StartBuildRequest` update** — accept optional `build_tier` field (`"full"` | `"mini"`, default `"full"`). When `project_mode == "mini"`, `build_tier` defaults to `"mini"` automatically.
- **`GET /projects/{id}` update** — include `project_mode` in response
- **`GET /projects/{id}/build/status` update** — include `build_tier` in response
- **Config update** — add `MINI_DEFAULT_STACK` setting in `app/config.py`:
  ```python
  MINI_DEFAULT_STACK: str = os.getenv("MINI_DEFAULT_STACK", "fastapi-react")
  # Valid presets: "fastapi-react", "nextjs", "django-postgres", "go-sqlite"
  ```
- **Validation** — `project_mode` must be `"full"` or `"mini"`; `build_tier` must be `"full"` or `"mini"`
- **Tests:**
  - Test project creation with `project_mode="mini"`
  - Test project creation with `project_mode="full"` (default)
  - Test build start with `build_tier="mini"`
  - Test that mini project defaults to mini build tier
  - Test invalid mode/tier values are rejected (422)
  - Test that existing projects without the column default to `"full"`

**Schema coverage:**
- projects table: +`project_mode` column
- builds table: +`build_tier` column

**Exit criteria:**
- Projects can be created with `project_mode` set to `"full"` or `"mini"`
- Builds can be started with `build_tier` set to `"full"` or `"mini"`
- API responses include the new fields
- Database migrations are reversible
- All existing tests pass + ~8 new tests for mode/tier CRUD and validation

---

## Phase 48 — Mini Mode: Proof-of-Concept Questionnaire

**Objective:** When `project_mode == "mini"`, the questionnaire runs a focused 2-section flow (Product Intent + Primary UI Flow) with ~6-10 adaptive questions total. The LLM uses section templates with required-field checklists to ensure completeness before marking each section done.

**Deliverables:**
- **`MINI_QUESTIONNAIRE_SECTIONS`** in `app/services/project/questionnaire.py`:
  ```python
  MINI_QUESTIONNAIRE_SECTIONS = ["product_intent", "primary_ui_flow"]
  ```
- **Section templates with required fields** — the questionnaire LLM receives a checklist of fields that must be answered before a section can complete:

  **Product Intent template:**
  - [ ] Product name / working title
  - [ ] One-line description
  - [ ] Target user / audience
  - [ ] Core problem solved
  - [ ] Primary user action (e.g., "creates a listing", "submits a form")
  - [ ] Auth required (yes/no)

  **Primary UI Flow template:**
  - [ ] Landing / first screen description
  - [ ] Main entity the user interacts with
  - [ ] Core CRUD flow (what create/view/edit looks like)
  - [ ] Navigation structure (sidebar, tabs, pages)
  - [ ] Data display style (cards, tables, feed)

- **Adaptive question count** — the LLM checks off items as the user answers. If a single message covers multiple items, the LLM acknowledges and moves on. If the user is brief, follow-up questions target unfilled items. Section completes only when all checklist items are satisfied.
- **Mode-aware routing** — `POST /projects/{id}/questionnaire` checks `project.project_mode`:
  - `"mini"` → uses `MINI_QUESTIONNAIRE_SECTIONS` and mini system prompt
  - `"full"` → uses existing `QUESTIONNAIRE_SECTIONS` (unchanged)
- **Mini system prompt** — new system prompt for the mini questionnaire LLM:
  > "You are a project intake specialist for Forge Proof-of-Concept builds. Your job is to collect just enough information to scaffold a working backend + frontend prototype. You ask about Product Intent and Primary UI Flow only. Be conversational but efficient — most users should finish in 6-10 messages. Do NOT ask about tech stack, deployment, or architectural boundaries — those are auto-decided for proof-of-concept builds."
- **Completion trigger** — when both mini sections are complete, the questionnaire returns `"status": "complete"` just like the full flow, triggering contract generation.
- **Tests:**
  - Test mini questionnaire only asks 2 sections
  - Test full questionnaire still asks 7 sections (regression)
  - Test section completion requires all checklist items filled
  - Test adaptive question count — user gives detailed answers, fewer questions asked
  - Test mode routing — mini project gets mini sections, full project gets full sections
  - Test completion signal fires after both mini sections done

**Schema coverage:** No new tables — uses existing `projects.questionnaire_state` JSONB.

**Exit criteria:**
- Mini mode questionnaire asks only Product Intent and Primary UI Flow
- Each section has a required-field checklist that must be fully satisfied
- Questions are adaptive — 6-10 total depending on user detail level
- Full mode questionnaire is completely unchanged
- Section routing is driven by `project.project_mode`
- All existing tests pass + ~10 new tests for mini questionnaire flow

---

## Phase 49 — Mini Mode: Contract Generation, Auto Stack & Docker Release

**Objective:** When `project_mode == "mini"`, contract generation uses a curated default tech stack, injects auto-decided defaults for all remaining config, produces a 2-phase `phases` contract, and targets a Docker-ready release. All 9 contracts are still generated (the builder needs full context), but scoped to proof-of-concept depth.

**Deliverables:**
- **Curated stack presets** — mini builds use one of 4 battle-tested stack combos. The default is `fastapi-react`, configurable via `MINI_DEFAULT_STACK` env var or overridable per-project:

  | Preset | Backend | Frontend | Database | Best for |
  |---|---|---|---|---|
  | `fastapi-react` (default) | Python 3.12 + FastAPI | React 19 + Vite + TypeScript | PostgreSQL | API-driven apps, dashboards |
  | `nextjs` | Next.js 15 (full-stack) | Next.js (App Router + RSC) | PostgreSQL | Content sites, SSR apps |
  | `django-postgres` | Python 3.12 + Django 5 | Django templates + HTMX | PostgreSQL | Admin-heavy, data-centric apps |
  | `go-sqlite` | Go 1.22 + Chi router | React 19 + Vite + TypeScript | SQLite | Lightweight tools, CLIs with UI |

- **Stack preset registry** — `app/services/project/stack_presets.py`:
  ```python
  STACK_PRESETS = {
      "fastapi-react": {
          "tech_stack": "Backend: Python 3.12 + FastAPI + Uvicorn. Frontend: React 19 + Vite + TypeScript. Database: PostgreSQL 16. ORM: none — raw SQL via asyncpg. Auth: JWT placeholder.",
          "docker": True,
          "db_service": "postgres:16-alpine",
      },
      "nextjs": {
          "tech_stack": "Full-stack: Next.js 15 with App Router, React Server Components, TypeScript. Database: PostgreSQL 16 via Prisma ORM.",
          "docker": True,
          "db_service": "postgres:16-alpine",
      },
      "django-postgres": {
          "tech_stack": "Backend: Python 3.12 + Django 5 + Django REST Framework. Frontend: Django templates + HTMX for interactivity. Database: PostgreSQL 16.",
          "docker": True,
          "db_service": "postgres:16-alpine",
      },
      "go-sqlite": {
          "tech_stack": "Backend: Go 1.22 + Chi router. Frontend: React 19 + Vite + TypeScript. Database: SQLite 3 (embedded, file-based).",
          "docker": True,
          "db_service": None,
      },
  }
  ```
- **Auto answer injection** — before calling `generate_contracts()` for a mini project, inject synthetic questionnaire answers from the selected preset:
  ```python
  MINI_AUTO_ANSWERS = {
      "tech_stack": preset["tech_stack"],  # from selected preset
      "database_schema": "Infer tables and columns from the product intent and UI flow. Keep it minimal — only the entities the user described.",
      "api_endpoints": "Standard CRUD endpoints for each entity. REST conventions. No auth middleware unless the user specified login is required.",
      "architectural_boundaries": "Standard project structure. Single repo. No microservices.",
      "deployment_target": "Docker-ready release. Include Dockerfile, docker-compose.yaml, and .dockerignore. App should run with a single `docker compose up` command. Also include local dev instructions for running without Docker.",
      "ui_requirements": "Derive from Primary UI Flow answers. Clean, minimal UI. Inline styles or basic CSS — no component library.",
  }
  ```
- **Docker-ready output** — the builder directive for mini builds explicitly requires:
  - `Dockerfile` — multi-stage build (builder + runtime), production-ready image
  - `docker-compose.yaml` — app service + database service (if applicable), volumes, environment variables, health checks
  - `.dockerignore` — exclude node_modules, .venv, .git, __pycache__, .env
  - The app must start cleanly with `docker compose up --build`
- **Mini phase instruction** — when `project_mode == "mini"`, the `_CONTRACT_INSTRUCTIONS["phases"]` override:
  > "Generate exactly 2 phases. Phase 0 — Backend Scaffold: project structure, config, .env.example, Dockerfile, docker-compose.yaml, .dockerignore, database setup with migrations for core entities, CRUD endpoints for each entity, basic auth placeholder if required, seed data script, API tests, boot script. Phase 1 — Frontend Scaffold: framework setup, routing, dashboard/listing page, primary entity detail page, API integration, working end-to-end flow, component tests, update Dockerfile and docker-compose.yaml to serve frontend. Each phase must be self-contained and shippable. The final output must run with `docker compose up --build`."
- **Contract depth scoping** — for mini mode, each contract instruction gets a preamble:
  > "This is a proof-of-concept build. Keep the contract focused on the minimum viable scope needed for a 2-phase scaffold. Do not over-specify — the user may continue to a full build later, at which point contracts will be regenerated at full depth."
- **All 9 contracts still generated** — blueprint, manifesto, stack, schema, physics, boundaries, phases, ui, builder_directive. The builder needs them for context even in mini mode.
- **Full build stack handling** — when `project_mode == "full"`, the questionnaire tech_stack section presents the 4 presets as suggestions but lets the user specify anything they want. The presets are conveniences, not constraints.
- **Tests:**
  - Test each of the 4 stack presets produces valid auto answers
  - Test mini mode injects auto answers for missing sections
  - Test mini mode generates exactly 2 phases in the phases contract
  - Test phase instruction includes Docker deliverables
  - Test full mode is completely unchanged (regression)
  - Test all 9 contract types are generated for both modes
  - Test `MINI_DEFAULT_STACK` env var switches the preset
  - Test mini contract depth is scoped (shorter contracts, PoC focus)
  - Test full build questionnaire shows presets as suggestions, allows custom input

**Schema coverage:** No new tables — uses existing `project_contracts` table.

**Exit criteria:**
- Mini mode uses one of 4 curated stack presets (default: `fastapi-react`)
- Auto-fills tech stack, DB schema, endpoints, boundaries, deployment, and UI from preset + inference
- Phases contract contains exactly 2 phases for mini mode
- Phase instruction mandates Dockerfile, docker-compose.yaml, and .dockerignore
- All 9 contracts are generated for both modes
- Full mode contract generation is completely unchanged but shows presets as suggestions
- Stack preset is configurable via `MINI_DEFAULT_STACK` env var
- All existing tests pass + ~14 new tests for stack presets, Docker config, and mini contract generation

---

## Phase 50 — Mini Mode: Build Execution

**Objective:** The build pipeline handles `build_tier == "mini"` — executing exactly 2 phases using the standard plan-execute flow with no pipeline changes. Git pushes exclude all contract files (enforced globally from Phase 46).

**Deliverables:**
- **Tier-aware phase parsing** — in `_run_build_plan_execute()`, when `build_tier == "mini"`:
  - Parse phases from the phases contract (should be exactly 2)
  - If somehow more than 2 exist, take only Phase 0 and Phase 1
  - Log a warning if phase count doesn't match expected
- **Same pipeline, compressed scope** — mini builds use identical flow:
  1. Parse phases → 2. Plan file manifest (Sonnet) → 3. Generate files (Opus) → 4. Per-file audit → 5. Verification (syntax + tests) → 6. Governance gate → 7. Commit & push → 8. Phase complete
- **Docker verification** — after Phase 1 completes, verify that `Dockerfile`, `docker-compose.yaml`, and `.dockerignore` exist in the project root. If missing, flag as a verification failure and trigger a fix round.
- **Contract exclusion enforcement** — verify that Phase 46's `.gitignore` injection and commit filtering are active. Mini builds are the most important case for this since the user explicitly expects no contracts in the output.
- **Build status events** — WebSocket events include `build_tier` so the frontend can display "Proof of Concept" vs "Full Build" labels
- **Cost tracking** — mini builds track costs the same way. Expected: ~50K-100K tokens (~$1-3 USD with Opus) vs ~200K-500K for full builds.
- **Tests:**
  - Test mini build executes exactly 2 phases
  - Test mini build uses standard plan-execute pipeline
  - Test mini build git push contains no contract files
  - Test mini build output includes Dockerfile, docker-compose.yaml, .dockerignore
  - Test mini build WebSocket events include `build_tier: "mini"`
  - Test mini build cost tracking works correctly
  - Test full build is completely unchanged (regression)

**Schema coverage:** No new tables.

**Exit criteria:**
- Mini builds execute exactly 2 phases through the standard pipeline
- All build infrastructure (planning, generation, audit, verification, governance, git push) works identically for mini and full
- Output includes `Dockerfile`, `docker-compose.yaml`, and `.dockerignore` — app runs with `docker compose up --build`
- Contract files are excluded from all git commits
- WebSocket events identify the build tier
- Cost is tracked and reported
- All existing tests pass + ~10 new tests for mini build execution and Docker verification

---

## Phase 51 — Mini Mode: README, Deployment Docs & Instructions

**Objective:** Every build (mini and full) generates comprehensive documentation as a post-build step: `README.md` for setup/usage and `DEPLOYMENT.md` for deployment instructions. For mini builds, the README includes a call-to-action to continue building via ForgeGuard.

**Deliverables:**
- **Post-build documentation generation** — after the final phase completes and before the build is marked `"completed"`:
  1. Collect: all generated file paths, the product intent, the tech stack, the phase summaries, Docker config
  2. Call the planner model (Sonnet) with documentation generation prompts
  3. Write `README.md` and `DEPLOYMENT.md` to the project root
  4. Include in the final git commit
- **`README.md` content — all builds:**
  - Project name and one-line description
  - Quick start: `docker compose up --build` (mini) or tailored command (full)
  - Prerequisites (Docker, or language versions if running locally)
  - Step-by-step local setup and run instructions (without Docker)
  - Available API endpoints with method, path, and description
  - Frontend pages and what they show
  - How to run tests
  - Project structure overview
  - Environment variable reference (from `.env.example`)
- **`README.md` content — mini builds (additional):**
  - "This is a proof-of-concept scaffold built by Forge."
  - "To continue development with the full architectural framework, contract system, and multi-phase build pipeline, visit [ForgeGuard]."
  - "The scaffold includes a working backend and frontend that demonstrate the core data model and primary user flow."
- **`README.md` content — full builds (additional):**
  - "Built with Forge — autonomous build system"
- **`DEPLOYMENT.md` — all builds:**
  - **Docker deployment** (primary):
    - How to build the image(s)
    - `docker compose up --build` with expected output
    - Docker Compose service overview (app, db, etc.)
    - Volume mounts and data persistence
    - Environment variable configuration
    - Health check endpoints
    - How to view logs: `docker compose logs -f`
    - How to stop: `docker compose down`
    - How to rebuild after code changes
  - **Local development** (alternative):
    - Language/runtime prerequisites
    - Database setup
    - Install dependencies
    - Run migrations
    - Start dev servers
  - **Production notes** (mini builds):
    - "This is a proof-of-concept. For production deployment, consider: HTTPS/TLS, environment-specific configs, database backups, monitoring, CI/CD pipeline."
  - **Production deployment** (full builds):
    - Cloud deployment guidance (from deployment contract)
    - CI/CD pipeline setup
    - Scaling considerations
- **README prompt template** — `app/templates/contracts/readme_prompt.md`:
  > "You are writing a README.md for a freshly built project. The user will deploy this on their machine and needs clear, accurate instructions. Write for a developer audience. Be specific — reference actual file names, actual endpoints, actual commands. Do not be generic. The primary run method is Docker — `docker compose up --build` should be the first thing in Quick Start."
- **Deployment prompt template** — `app/templates/contracts/deployment_prompt.md`:
  > "You are writing a DEPLOYMENT.md for a freshly built project. Cover Docker deployment as the primary method with step-by-step instructions. Include a local development section as an alternative. Be specific to the actual Docker configuration in the project."
- **Tests:**
  - Test README is generated after build completes
  - Test README contains Docker quick start
  - Test README contains endpoint documentation
  - Test DEPLOYMENT.md is generated after build completes
  - Test DEPLOYMENT.md contains Docker Compose instructions
  - Test DEPLOYMENT.md contains local dev alternative
  - Test mini README includes ForgeGuard call-to-action
  - Test mini DEPLOYMENT.md includes production notes caveat
  - Test full DEPLOYMENT.md includes production deployment section
  - Test both docs are included in the final git commit

**Schema coverage:** No new tables.

**Exit criteria:**
- Every completed build has `README.md` and `DEPLOYMENT.md` in the repo root
- Both docs are accurate to the actual code and Docker config produced
- Mini build README includes the ForgeGuard continuation prompt
- DEPLOYMENT.md covers Docker as primary and local dev as alternative
- Both docs are part of the final git commit
- All existing tests pass + ~12 new tests for documentation generation

---

## Phase 52 — Mini Mode: Continue to Full Build

**Objective:** Users who completed a proof-of-concept can upgrade to a full build. This triggers a gap questionnaire (5 remaining sections), full-depth contract regeneration, and build resumption from Phase 2 onward.

**Deliverables:**
- **`POST /projects/{id}/continue-full`** — new endpoint:
  1. Validates: project exists, user owns it, `project_mode == "mini"`, latest build is `"completed"` with `build_tier == "mini"`
  2. Flips `project.project_mode` to `"full"`
  3. Resets `questionnaire_state` to preserve existing answers but mark the 5 gap sections as pending
  4. Returns `{ "status": "questionnaire_required", "remaining_sections": [...] }`
- **Gap questionnaire flow** — the existing `POST /projects/{id}/questionnaire` endpoint now sees 5 remaining sections (tech_stack, database_schema, api_endpoints, ui_requirements, architectural_boundaries, deployment_target) and walks the user through them. Product Intent and Primary UI Flow answers are preserved.
- **Tech stack question adjustment** — the questionnaire shows the current stack preset and asks: "Your proof of concept used [preset name, e.g. FastAPI + React + PostgreSQL]. Do you want to keep this stack, or change anything?" The user can keep it, pick a different preset, or specify a fully custom stack.
- **Contract regeneration** — when all 7 sections are complete, `generate_contracts()` runs at full depth with all answers. The LLM also receives a summary of the existing codebase (file tree + key file contents) so contracts account for what's already built.
- **Build resumption** — `POST /projects/{id}/build` with `build_tier="full"` and `resume_from_phase=2`:
  1. Loads the full phases contract (6-12 phases)
  2. Skips Phase 0 and Phase 1 (already completed by mini build)
  3. Planner sees existing code via workspace snapshot
  4. Builds Phase 2 onward, extending the scaffold
- **Frontend flow:**
  1. Build complete page shows "Continue to Full Build" button (only for mini builds)
  2. Click → navigates to gap questionnaire
  3. Complete questionnaire → contract review page
  4. Click "Start Full Build" → build resumes from Phase 2
- **Tests:**
  - Test continue endpoint validates mini mode + completed build
  - Test continue endpoint rejects full mode projects
  - Test gap questionnaire preserves existing answers
  - Test gap questionnaire asks only remaining 5 sections
  - Test tech stack question shows current auto-decided stack
  - Test contract regeneration uses all 7 sections of answers
  - Test build resumption starts from Phase 2
  - Test build resumption sees existing code
  - Test full flow: mini build → continue → gap questionnaire → full build

**Schema coverage:** No new tables — uses existing columns.

**Exit criteria:**
- "Continue to Full Build" flow works end-to-end
- Gap questionnaire asks only the 5 sections not covered by mini mode
- Existing PoC answers are preserved, not re-asked
- Contracts regenerate at full depth with complete context
- Full build resumes from Phase 2, building on the existing scaffold
- Frontend has the continuation UX (button + gap questionnaire + build start)
- All existing tests pass + ~12 new tests for the continuation flow

---

## Phase 53 — Mini Mode: Frontend UX

**Objective:** The frontend surfaces mini mode / proof-of-concept as a first-class option throughout the user journey — project creation, questionnaire, build progress, and build completion.

**Deliverables:**
- **Project creation modal** — add a mode selector before the questionnaire starts:
  - Two cards: "Proof of Concept" and "Full Build"
  - **Proof of Concept card:** "2-phase scaffold. Answer a few questions, get a working backend + frontend in minutes. ~$1-3 in LLM costs."
  - **Full Build card:** "Complete multi-phase build. Detailed questionnaire, full contract framework, production-ready output. ~$10-30+ in LLM costs."
  - Selection sets `project_mode` on the `POST /projects` call
- **Questionnaire page** — mode-aware header:
  - Mini: "Proof of Concept — tell us about your idea" with a progress indicator showing 2 sections
  - Full: existing 7-section progress indicator (unchanged)
- **Build progress page** — tier-aware labels:
  - Badge: "Proof of Concept" (amber) or "Full Build" (blue) next to the project name
  - Phase overview shows 2 phases for mini, N phases for full
- **Build complete page — mini builds:**
  - Success message: "Your proof of concept is ready!"
  - Summary of what was built (backend endpoints, frontend pages)
  - "View Repository" button
  - "Continue to Full Build" button (prominent, primary action)
  - Cost breakdown
- **Build complete page — full builds:**
  - Existing flow (unchanged)
- **Project detail page** — show `project_mode` badge, show "Continue to Full Build" option if mode is mini and build is complete
- **Tests:**
  - Test mode selector renders on project creation
  - Test mode selector sets correct project_mode
  - Test questionnaire header adapts to mode
  - Test build progress shows correct tier badge
  - Test build complete shows "Continue to Full Build" for mini
  - Test build complete does NOT show "Continue" for full builds

**Schema coverage:** No new tables.

**Exit criteria:**
- Mode selection is clear and prominent on project creation
- The entire UX flow adapts to the selected mode
- Mini builds have a clear "Continue to Full Build" call-to-action
- Full build UX is completely unchanged
- All existing tests pass + ~10 new tests for mode-aware frontend components