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