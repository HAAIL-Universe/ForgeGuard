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