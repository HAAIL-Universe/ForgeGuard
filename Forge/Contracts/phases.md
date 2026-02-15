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
