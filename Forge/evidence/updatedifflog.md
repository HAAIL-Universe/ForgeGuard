# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-16T21:30:00+00:00
- Branch: master
- HEAD: 3f158b8d56612f61f8351f6dc383a223b12f52dd
- BASE_HEAD: 3f158b8d56612f61f8351f6dc383a223b12f52dd
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- Phase 31: Contract Version History — contract_snapshots table (migration 013), repo layer (snapshot_contracts, get_snapshot_batches, get_snapshot_contracts), service layer (auto-snapshot before regeneration, list_contract_versions, get_contract_version), API endpoints (GET /contracts/history, GET /contracts/history/{batch} with route ordering fix), frontend version history UI (collapsible panel, lazy-loaded batches, version display in ProjectDetail), 12 new backend tests, fixed error message for "not found" routing. Updated schema.md with contract_snapshots table definition, traceability entry, and migration listing. Fixed pre-existing tests broken by new snapshot call in generate_contracts. Tightened boundaries.json SQL patterns (SELECT→SELECT…FROM, INSERT→INSERT INTO, UPDATE→UPDATE…SET, DELETE FROM→DELETE FROM) to eliminate false positives from English word usage in comments. 547 backend + 61 frontend = 608 total tests passing.

## Files Changed (staged)
- Forge/Contracts/boundaries.json
- Forge/Contracts/phases.md
- Forge/Contracts/schema.md
- app/api/routers/projects.py
- app/repos/project_repo.py
- app/services/project_service.py
- db/migrations/013_contract_snapshots.sql
- tests/test_contract_snapshots.py
- tests/test_project_service.py
- tests/test_projects_router.py
- web/src/components/ContractProgress.tsx
- web/src/components/QuestionnaireModal.tsx
- web/src/pages/ProjectDetail.tsx

## git status -sb
    ## master...origin/master
    MM Forge/Contracts/boundaries.json
    M  Forge/Contracts/phases.md
    A  Forge/IDE/tests/test_backoff.py
    A  Forge/IDE/tests/test_build_helpers.py
    A  Forge/IDE/tests/test_context_pack.py
    A  Forge/IDE/tests/test_contracts.py
    A  Forge/IDE/tests/test_diagnostics.py
    A  Forge/IDE/tests/test_diff_generator.py
    A  Forge/IDE/tests/test_errors.py
    A  Forge/IDE/tests/test_file_index.py
    A  Forge/IDE/tests/test_git_ops.py
    A  Forge/IDE/tests/test_log_parser.py
    A  Forge/IDE/tests/test_patcher.py
    A  Forge/IDE/tests/test_python_intel.py
    A  Forge/IDE/tests/test_reader.py
    A  Forge/IDE/tests/test_redactor.py
    A  Forge/IDE/tests/test_registry.py
    A  Forge/IDE/tests/test_relevance.py
    A  Forge/IDE/tests/test_response_parser.py
    A  Forge/IDE/tests/test_runner.py
    A  Forge/IDE/tests/test_sanitiser.py
    A  Forge/IDE/tests/test_searcher.py
    A  Forge/IDE/tests/test_ts_intel.py
    A  Forge/IDE/tests/test_workspace.py
    MM Forge/evidence/audit_ledger.md
    MM Forge/evidence/updatedifflog.md
    M  Forge/scripts/run_audit.ps1
    M  app/api/routers/builds.py
    M  app/clients/git_client.py
    M  app/services/build_service.py
    M  app/services/tool_executor.py
    A  forge_ide/__init__.py
    A  forge_ide/adapters.py
    A  forge_ide/backoff.py
    A  forge_ide/build_helpers.py
    A  forge_ide/context_pack.py
    A  forge_ide/contracts.py
    A  forge_ide/diagnostics.py
    A  forge_ide/diff_generator.py
    A  forge_ide/errors.py
    A  forge_ide/file_index.py
    A  forge_ide/git_ops.py
    A  forge_ide/lang/__init__.py
    A  forge_ide/lang/python_intel.py
    A  forge_ide/lang/ts_intel.py
    A  forge_ide/log_parser.py
    A  forge_ide/patcher.py
    A  forge_ide/reader.py
    A  forge_ide/redactor.py
    A  forge_ide/registry.py
    A  forge_ide/relevance.py
    A  forge_ide/response_parser.py
    A  forge_ide/runner.py
    A  forge_ide/sanitiser.py
    A  forge_ide/searcher.py
    A  forge_ide/workspace.py
    M  web/src/components/BranchPickerModal.tsx
    M  web/src/pages/BuildProgress.tsx

## Verification
- Static: all modules pass import checks and type annotations. Runtime: 1404 tests pass (68 new for Phase 30). Behavior: redact() catches 12 secret categories, sort functions produce deterministic order, sanitise_output strips timestamps/PIDs/tmpdir/absolute paths, diff paths normalised to forward slashes. Contract: all models frozen, __all__ exports verified.

## Notes (optional)
- Audit-trail DB storage deferred to integration phase (requires PostgreSQL). sort_diagnostics not applied inside merge_diagnostics to preserve existing contract.

## Next Steps
- Phase 31 planning.

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/boundaries.json b/Forge/Contracts/boundaries.json
    index 7725b56..46d929e 100644
    --- a/Forge/Contracts/boundaries.json
    +++ b/Forge/Contracts/boundaries.json
    @@ -121,5 +121,24 @@
           ]
         }
       ],
    -  "known_violations": []
    +  "known_violations": [
    +    {
    +      "layer": "clients",
    +      "file": "agent_client.py",
    +      "pattern": "\\bSELECT\\b",
    +      "reason": "False positive: word 'select' in comment ('select the best key'), not SQL"
    +    },
    +    {
    +      "layer": "clients",
    +      "file": "git_client.py",
    +      "pattern": "\\bINSERT\\b",
    +      "reason": "False positive: Python list .insert() method call, not SQL"
    +    },
    +    {
    +      "layer": "services",
    +      "file": "build_service.py",
    +      "pattern": "\\bSELECT\\b",
    +      "reason": "False positive: word 'Select' in docstring/comment, not SQL"
    +    }
    +  ]
     }
    diff --git a/Forge/Contracts/phases.md b/Forge/Contracts/phases.md
    index b51ec66..263b7c3 100644
    --- a/Forge/Contracts/phases.md
    +++ b/Forge/Contracts/phases.md
    @@ -1,1422 +1,3129 @@
    -# ForgeGuard -- Phases
    -
    -Canonical phase plan. Each phase is self-contained, shippable, and auditable. The builder contract (S1) requires completing phases in strict order. No phase may begin until the previous phase passes audit.
    -
    ----
    -
    -## Phase 0 -- Genesis
    -
    -**Objective:** Skeleton project that boots, passes lint, has one test, and serves health.
    -
    -**Deliverables:**
    -- `/health` returns `{ "status": "ok" }` (FastAPI)
    -- Root `boot.ps1` -- installs backend deps, starts uvicorn
    -- `pytest` passes with one health test
    -- Project structure matches blueprint layers
    -- `forge.json` at project root with initial phase metadata
    -- `.gitignore` for Python + Node
    -
    -**Exit criteria:**
    -- `boot.ps1` runs without errors
    -- `GET /health` returns 200
    -- `pytest` passes (1 test)
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 1 -- Authentication
    -
    -**Objective:** GitHub OAuth login flow. Users can sign in and out. JWT session management.
    -
    -**Deliverables:**
    -- `POST /auth/github` -- accepts GitHub OAuth code, exchanges for access token, creates/updates user, returns JWT
    -- `GET /auth/github/callback` -- handles the OAuth redirect
    -- `GET /auth/me` -- returns current user from JWT
    -- `users` table (id, github_id, github_login, avatar_url, access_token_enc, created_at, updated_at)
    -- JWT encode/decode utility (HS256, env-configured secret)
    -- Auth middleware that protects all routes except `/health`, `/auth/*`
    -- Login page with "Sign in with GitHub" button
    -
    -**Schema coverage:**
    -- [x] `users` table
    -
    -**Exit criteria:**
    -- OAuth round-trip works (login -> GitHub -> callback -> JWT returned)
    -- `GET /auth/me` returns user info with valid JWT
    -- `GET /auth/me` returns 401 with missing/invalid JWT
    -- All protected routes return 401 without auth
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 2 -- Repo Management
    -
    -**Objective:** Connect and disconnect GitHub repos. Manage webhook lifecycle.
    -
    -**Deliverables:**
    -- `GET /repos` -- list connected repos for current user
    -- `POST /repos/connect` -- accepts `{ "github_repo_id": int }`, fetches repo metadata from GitHub, installs webhook, creates `repos` row
    -- `POST /repos/{id}/disconnect` -- removes webhook from GitHub, deletes `repos` row and associated audit data
    -- `repos` table (id, user_id FK, github_repo_id, full_name, default_branch, webhook_id, connected_at)
    -- GitHub API client: list user repos, create webhook, delete webhook
    -- Repo picker modal on frontend (searchable list of user's GitHub repos)
    -- Repo list on dashboard (shows connected repos with placeholder health badge)
    -
    -**Schema coverage:**
    -- [x] `users` table (Phase 1)
    -- [x] `repos` table
    -
    -**Exit criteria:**
    -- User can connect a repo and see it in the list
    -- User can disconnect a repo (webhook removed, data deleted)
    -- Connecting same repo twice returns 409
    -- Webhook appears on the GitHub repo settings page after connect
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 3 -- Audit Engine
    -
    -**Objective:** Receive GitHub push events, run audit checks, store results.
    -
    -**Deliverables:**
    -- `POST /webhooks/github` -- receives push event, validates signature, enqueues audit
    -- Audit runner: clones repo at commit SHA (shallow clone), runs registered checks, stores results
    -- Check registry: pluggable check functions (start with A4 boundary check, A9 dependency gate)
    -- `audit_runs` table (id, repo_id FK, commit_sha, commit_message, author, branch, status, started_at, completed_at)
    -- `audit_checks` table (id, audit_run_id FK, check_code, check_name, result, detail)
    -- `GET /repos/{id}/audits` -- list audit runs for a repo
    -- `GET /repos/{id}/audits/{auditId}` -- full audit detail with check results
    -- Commit timeline view on frontend (list of audits per repo)
    -- Audit detail view on frontend (check breakdown)
    -
    -**Schema coverage:**
    -- [x] `users` table (Phase 1)
    -- [x] `repos` table (Phase 2)
    -- [x] `audit_runs` table
    -- [x] `audit_checks` table
    -
    -**Exit criteria:**
    -- Push to connected repo triggers audit via webhook
    -- Audit results appear in DB within 30 seconds of push
    -- `GET /repos/{id}/audits` returns audit list with correct data
    -- Audit detail shows individual check results
    -- Invalid webhook signatures are rejected (401)
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 4 -- Dashboard & Real-Time
    -
    -**Objective:** Polished frontend with real-time updates via WebSocket.
    -
    -**Deliverables:**
    -- `GET /ws` -- WebSocket endpoint, sends audit updates to connected clients
    -- Real-time dashboard: new audit results appear without page refresh
    -- Health badge calculation: green (all pass), yellow (warnings only), red (any fail)
    -- Commit timeline with result badges and mini check summary
    -- Audit detail page with full check breakdown
    -- Skeleton loaders for all data views
    -- Empty states for repos and audits
    -- Toast notifications for errors
    -- Responsive sidebar collapse below 1024px
    -
    -**Schema coverage:**
    -- [x] `users` table (Phase 1)
    -- [x] `repos` table (Phase 2)
    -- [x] `audit_runs` table (Phase 3)
    -- [x] `audit_checks` table (Phase 3)
    -
    -**Exit criteria:**
    -- Dashboard updates in real-time when a new audit completes
    -- Health badges reflect actual audit results
    -- All empty states render correctly
    -- UI matches ui.md specification
    -- All Vitest frontend tests pass
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 5 -- Ship Gate
    -
    -**Objective:** Final hardening, documentation, and deployment readiness.
    -
    -**Deliverables:**
    -- `USER_INSTRUCTIONS.md` -- end-user setup and usage guide
    -- `boot.ps1` -- one-click full setup (backend + frontend + DB migration)
    -- Rate limiting on webhook endpoint
    -- Input validation on all API endpoints
    -- Error handling audit (no unhandled exceptions leak stack traces)
    -- All tests pass (pytest + Vitest)
    -- Environment variable validation on startup (fail fast if missing)
    -
    -**Exit criteria:**
    -- `boot.ps1` brings up the full stack from a fresh clone
    -- `USER_INSTRUCTIONS.md` covers: prerequisites, setup, usage, troubleshooting
    -- No unhandled exceptions in any code path
    -- All tests pass
    -- `run_audit.ps1` passes all checks
    -- Project is deployable to Render
    -
    ----
    -
    -## Phase 6 -- Integration Test
    -
    -**Objective:** Validate the full audit pipeline end-to-end with a minor code change, confirming the builder workflow, test suite, and governance audit all function correctly post-ship.
    -
    -**Deliverables:**
    -- Add `GET /health/version` endpoint returning `{ "version": "0.1.0", "phase": "6" }`
    -- Add `VERSION` constant to `app/config.py`
    -- Add test for the new endpoint in `tests/test_health.py`
    -- Frontend: show version string in the AppShell sidebar footer
    -- All existing tests continue to pass (no regressions)
    -
    -**Exit criteria:**
    -- `GET /health/version` returns 200 with correct payload
    -- New test passes
    -- All 85+ existing tests still pass (pytest + Vitest)
    -- Version visible in sidebar footer
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 7 -- Python Audit Runner
    -
    -**Objective:** Port the PowerShell audit checks (A1-A9, W1-W3) to a Python module, enabling hosted/containerized audit execution without a PowerShell dependency. This is the foundation for server-side build orchestration.
    -
    -**Deliverables:**
    -- `app/audit/runner.py` -- Python implementation of all A1-A9 blocking checks and W1-W3 warnings, matching the logic in `Forge/scripts/run_audit.ps1`
    -  - A1 Scope compliance (git diff vs claimed files)
    -  - A2 Minimal-diff discipline (detect renames)
    -  - A3 Evidence completeness (test_runs_latest.md exists, Status: PASS)
    -  - A4 Boundary compliance (boundaries.json pattern matching)
    -  - A5 Diff log gate (updatedifflog.md exists, no TODO placeholders)
    -  - A6 Authorization gate (no unauthorized commits)
    -  - A7 Verification hierarchy order (Static, Runtime, Behavior, Contract in order)
    -  - A8 Test gate (test_runs_latest.md Status: PASS)
    -  - A9 Dependency gate (imports vs dependency manifest)
    -  - W1 No secrets in diff
    -  - W2 Audit ledger integrity
    -  - W3 Physics route coverage
    -- `app/audit/runner.py` must accept the same inputs as the PS1 script: claimed files list, phase identifier, and project root path
    -- CLI entrypoint: `python -m app.audit.runner --claimed-files "file1,file2" --phase "Phase N"` for local/CI use
    -- Audit runner returns structured results (list of check results with code, name, result, detail) and appends to `evidence/audit_ledger.md`
    -- `GET /audit/run` -- internal API endpoint to trigger an audit run programmatically (auth: bearer, admin-only or system-internal)
    -- Tests: one test per check (A1-A9, W1-W3) using fixtures with known-good and known-bad inputs
    -- Existing `app/audit/engine.py` remains unchanged -- it handles push-triggered repo checks (A4, A9, W1), while `runner.py` handles full governance audits
    -
    -**Schema coverage:**
    -- No new tables (runner writes to evidence files and audit_ledger.md)
    -
    -**Exit criteria:**
    -- `python -m app.audit.runner` produces identical PASS/FAIL results as `run_audit.ps1` for the same inputs
    -- All 12 audit checks (A1-A9, W1-W3) have dedicated tests
    -- CLI entrypoint works from project root
    -- Internal API endpoint returns structured audit results
    -- All existing tests still pass
    -- `run_audit.ps1` passes all checks (self-referential: the PS1 script audits the phase that replaces it)
    -
    ----
    -
    -## Phase 8 -- Project Intake & Questionnaire
    -
    -**Objective:** Users can create a new project by answering a chat-based questionnaire. A lightweight LLM (Haiku) guides the conversation, collects requirements, and generates Forge contract files from templates.
    -
    -**Deliverables:**
    -- `projects` table (id, user_id, name, description, status, repo_id FK nullable, created_at, updated_at)
    -- `project_contracts` table (id, project_id FK, contract_type, content, version, created_at, updated_at)
    -- Contract templates in `app/templates/contracts/` -- parameterized Markdown/YAML templates for: blueprint, manifesto, stack, schema, physics, boundaries, phases, ui, builder_contract, builder_directive
    -- `POST /projects` -- create a new project (name, description)
    -- `GET /projects` -- list user's projects
    -- `GET /projects/{id}` -- project detail with contract status
    -- `POST /projects/{id}/questionnaire` -- send a message to the questionnaire chat; Haiku processes the answer and returns the next question or a completion signal
    -- `GET /projects/{id}/questionnaire/state` -- current questionnaire progress (which sections are complete, which remain)
    -- `POST /projects/{id}/contracts/generate` -- generate all contract files from completed questionnaire answers; stores in project_contracts table
    -- `GET /projects/{id}/contracts` -- list generated contracts
    -- `GET /projects/{id}/contracts/{type}` -- view a single contract
    -- `PUT /projects/{id}/contracts/{type}` -- edit a contract before build
    -- Questionnaire flow covers: product intent, tech stack, database schema, API endpoints, UI requirements, architectural boundaries, deployment target, phase breakdown
    -- Frontend: new "Create Project" button on dashboard, questionnaire chat page, contract review page
    -- Haiku API integration via `app/clients/llm_client.py` (Anthropic Messages API, model configurable via env var `LLM_QUESTIONNAIRE_MODEL`)
    -
    -**Schema coverage:**
    -- [x] users (Phase 1)
    -- [x] repos (Phase 2)
    -- [x] audit_runs (Phase 3)
    -- [x] audit_checks (Phase 3)
    -- [x] projects
    -- [x] project_contracts
    -
    -**Exit criteria:**
    -- User can create a project and complete the questionnaire via chat
    -- All required contract sections are collected through the questionnaire
    -- Contracts generate from templates with user-provided values
    -- Contracts are viewable and editable before build
    -- Haiku responses are contextual and guide the user through each section
    -- All new endpoints have tests (mocked LLM responses)
    -- All existing tests still pass
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 9 -- Build Orchestrator
    -
    -**Objective:** Integrate the Claude Agent SDK to spawn autonomous builder sessions. ForgeGuard manages the full build lifecycle: clone repo, inject contracts, start builder, stream progress, run audits inline, handle loopback, and advance through phases.
    -
    -**Deliverables:**
    -- `builds` table (id, project_id FK, phase, status, started_at, completed_at, loop_count, error_detail, created_at)
    -- `build_logs` table (id, build_id FK, timestamp, source, level, message, created_at) -- captures streaming builder output
    -- `app/services/build_service.py` -- orchestration layer:
    -  - Clone target repo (or create fresh repo from project)
    -  - Write generated contracts into `Forge/` directory
    -  - Spawn Agent SDK session with builder directive as prompt
    -  - Stream builder output and store in build_logs
    -  - Detect phase completion signals from builder output
    -  - Invoke Python audit runner (Phase 7) inline when builder signals COMPLETE
    -  - Feed audit results back to builder (PASS: continue, FAIL: loopback items)
    -  - Enforce loop limit (3 consecutive fails = RISK_EXCEEDS_SCOPE)
    -  - Track phase progression and overall build status
    -- `app/clients/agent_client.py` -- Agent SDK wrapper (claude_agent_sdk.query with configurable model, tools, permissions)
    -- `POST /projects/{id}/build` -- start a build (validates contracts exist, then spawns builder)
    -- `POST /projects/{id}/build/cancel` -- cancel an active build
    -- `GET /projects/{id}/build/status` -- current build status (phase, progress, active/idle)
    -- `GET /projects/{id}/build/logs` -- paginated build logs
    -- WebSocket extension: broadcast build progress events (phase_start, phase_complete, audit_pass, audit_fail, build_complete, build_error)
    -- Environment variables: `ANTHROPIC_API_KEY` (for Agent SDK), `LLM_BUILDER_MODEL` (default: claude-opus-4-6)
    -- Build runs in a background task (asyncio) -- the API endpoint returns immediately, progress streams via WebSocket
    -
    -**Schema coverage:**
    -- [x] users (Phase 1)
    -- [x] repos (Phase 2)
    -- [x] audit_runs (Phase 3)
    -- [x] audit_checks (Phase 3)
    -- [x] projects (Phase 8)
    -- [x] project_contracts (Phase 8)
    -- [x] builds
    -- [x] build_logs
    -
    -**Exit criteria:**
    -- Starting a build spawns an Agent SDK session that reads contracts and begins Phase 0
    -- Builder output streams to build_logs table in real-time
    -- Phase completion triggers the Python audit runner automatically
    -- Audit PASS advances to next phase; audit FAIL triggers loopback
    -- Build can be cancelled mid-execution
    -- 3 consecutive loopback failures stops the build with RISK_EXCEEDS_SCOPE
    -- All new endpoints have tests (mocked Agent SDK)
    -- All existing tests still pass
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 10 -- Live Build Dashboard
    -
    -**Objective:** Real-time build progress visualization. Users watch their project being built phase-by-phase with streaming logs, audit results, and phase status indicators.
    -
    -**Deliverables:**
    -- Frontend: Build Progress page (`/projects/{id}/build`)
    -  - Phase progress bar (Phase 0 through N, color-coded: grey=pending, blue=active, green=pass, red=fail)
    -  - Streaming log viewer (auto-scrolling terminal-style output, color-coded by level)
    -  - Per-phase audit result cards (A1-A9 checklist with PASS/FAIL badges)
    -  - Loopback indicator (shows iteration count and fix plan when in loopback)
    -  - Build summary header (elapsed time, current phase, overall status)
    -  - Cancel build button with confirmation dialog
    -- Frontend: Project list view on dashboard (shows all projects with their build status)
    -- Frontend: Project detail page (`/projects/{id}`) -- overview with links to questionnaire, contracts, build, and connected repo
    -- WebSocket: build progress events render in real-time (no polling)
    -- Skeleton loaders for build page during initial data fetch
    -- Empty states for projects with no builds
    -- Toast notifications for build completion (PASS or FAIL)
    -- Mobile-responsive layout for build progress page
    -
    -**Schema coverage:**
    -- No new tables (reads from builds, build_logs, projects)
    -
    -**Exit criteria:**
    -- Build progress page updates in real-time as the builder works
    -- Phase transitions are visually clear (progress bar updates)
    -- Streaming logs appear within 1 second of generation
    -- Audit results display correctly per phase
    -- Loopback iterations are visible with fix plans
    -- Build completion triggers a toast notification
    -- All empty and loading states render correctly
    -- All Vitest frontend tests pass
    -- All existing tests still pass
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 11 -- Ship & Deploy
    -
    -**Objective:** Post-build UX -- deployment instructions, build artifacts summary, cost tracking, and final hardening for the hosted build pipeline.
    -
    -**Deliverables:**
    -- Frontend: Build Complete page -- shown when all phases pass
    -  - Deployment instructions (generated from project's stack.md -- e.g., Render setup steps, env vars to configure)
    -  - Build summary (phases completed, total time, total audit checks, loopback count)
    -  - Cost estimate (token usage from Agent SDK, approximate API cost)
    -  - Link to generated repo with all code
    -  - "Download contracts" button (zip of Forge/ directory)
    -- `build_costs` table (id, build_id FK, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at)
    -- Token usage tracking in build_service.py (capture from Agent SDK streaming output)
    -- `GET /projects/{id}/build/summary` -- complete build summary with cost breakdown
    -- `GET /projects/{id}/build/instructions` -- generated deployment instructions
    -- Rate limiting on build endpoints (prevent concurrent builds per user, max builds per hour)
    -- Input validation on all new API endpoints
    -- Error handling audit for the full build pipeline (no unhandled exceptions)
    -- Update `USER_INSTRUCTIONS.md` with project intake, questionnaire, and build workflow documentation
    -- All tests pass (pytest + Vitest)
    -
    -**Schema coverage:**
    -- [x] users (Phase 1)
    -- [x] repos (Phase 2)
    -- [x] audit_runs (Phase 3)
    -- [x] audit_checks (Phase 3)
    -- [x] projects (Phase 8)
    -- [x] project_contracts (Phase 8)
    -- [x] builds (Phase 9)
    -- [x] build_logs (Phase 9)
    -- [x] build_costs
    -
    -**Exit criteria:**
    -- Build complete page shows deployment instructions tailored to the project's stack
    -- Cost breakdown is accurate (input/output tokens per phase, total USD estimate)
    -- Rate limiting prevents abuse of build endpoints
    -- No unhandled exceptions across the build pipeline
    -- `USER_INSTRUCTIONS.md` covers the full project creation and build workflow
    -- All tests pass
    -- `run_audit.ps1` passes all checks
    -
    ----
    -
    -## Phase 12 -- Build Target & File Writing
    -
    -**Objective:** The builder actually writes files. Users choose a build target -- a new GitHub repo, an existing GitHub repo, or a local file path -- and the builder's output is parsed into discrete files and written to that target in real time. This replaces the current "stream text only" behaviour.
    -
    -**Background:** Currently the builder streams its entire output as raw text chunks. No files are created anywhere. The user's intention was always that builds produce real, usable code in a real repository or directory.
    -
    -**Deliverables:**
    -
    -- **Build target selection** (backend + frontend):
    -  - `build_target_type` enum: `github_new`, `github_existing`, `local_path`
    -  - `POST /projects/{id}/build` extended: accepts `target_type` and `target_ref` (repo name for new, repo full_name for existing, absolute path for local)
    -  - For `github_new`: call GitHub API to create an empty repo under the user's account, clone it locally to a temp working directory
    -  - For `github_existing`: clone the repo to a temp working directory (shallow clone, default branch)
    -  - For `local_path`: validate the path exists (or create it), use directly as the working directory
    -  - Store target info on the `builds` table: `target_type`, `target_ref`, `working_dir`
    -
    -- **DB migration 008** -- `build_targets`:
    -  - Add columns to `builds`: `target_type VARCHAR(20)`, `target_ref TEXT`, `working_dir TEXT`
    -
    -- **File parser in build_service.py**:
    -  - Builder directive updated to instruct the LLM to emit file blocks in a canonical format:
    -    ```
    -    === FILE: path/to/file.py ===
    -    ```python
    -    <file contents>
    -    ```
    -    === END FILE ===
    -    ```
    -  - `_parse_file_blocks(accumulated_text)` -- extracts file path + content from builder output
    -  - When a file block is detected:
    -    1. Write the file to the working directory on disk
    -    2. Emit `file_created` WS event with `{ path, size_bytes, language }`
    -    3. Log the file write to build_logs
    -  - Handle file updates (same path written twice = overwrite with latest)
    -
    -- **Git operations** (for GitHub targets):
    -  - After each phase completes audit, stage + commit all new/modified files with message `"forge: Phase N complete"`
    -  - After build completes, push all commits to the remote
    -  - `app/clients/git_client.py` -- thin wrapper around `asyncio.subprocess` for git clone, add, commit, push (no heavy git library dependency)
    -
    -- **Frontend: Build target picker** (shown before build starts):
    -  - Three-option card selector: "New GitHub Repo" / "Existing GitHub Repo" / "Local Directory"
    -  - For new repo: text input for repo name (validated against GitHub naming rules)
    -  - For existing repo: repo picker modal (reuse existing component)
    -  - For local: text input for absolute path with validation
    -  - Selected target stored in build state and sent with `POST /projects/{id}/build`
    -
    -- **Frontend: File tree panel** on BuildProgress page:
    -  - New collapsible panel (left column, below phase checklist) showing files created so far
    -  - Tree structure mirroring directory hierarchy
    -  - Each file shows: path, size, language icon
    -  - Updates in real-time via `file_created` WS events
    -  - Click a file to expand and see a syntax-highlighted preview of its content (read from build logs or a dedicated endpoint)
    -
    -- **API for file retrieval**:
    -  - `GET /projects/{id}/build/files` -- list all files written during the build
    -  - `GET /projects/{id}/build/files/{path}` -- retrieve content of a specific file
    -
    -**Schema coverage:**
    -- [x] builds (extended with target columns)
    -- [x] build_logs (Phase 9)
    -- [ ] No new tables -- file metadata derived from build_logs + filesystem
    -
    -**Exit criteria:**
    -- User can select "New GitHub Repo" and the build creates the repo + writes files into it
    -- User can select "Existing GitHub Repo" and the build writes files into the cloned repo
    -- User can select "Local Directory" and files are written to that path
    -- Files appear in the file tree panel in real-time as the builder creates them
    -- After each phase audit pass, files are committed with a phase-specific message
    -- After build completion (GitHub targets), all commits are pushed to the remote
    -- File content is retrievable via API
    -- All existing tests still pass + new tests for file parsing, git operations, and target selection
    -- `run_audit.ps1` passes all checks
    -
    ----
    ... (46265 lines truncated, 46765 total)
