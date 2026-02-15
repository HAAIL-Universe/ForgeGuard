# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T04:02:10+00:00
- Branch: master
- HEAD: ef1b753792775a7c9c70756a9b1291ed2848554e
- BASE_HEAD: 34a7f775d201d31f437158d388b29554b44b8e96
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- TODO: 1-5 bullets (what changed, why, scope).

## Files Changed (staged)
- Forge/Contracts/physics.yaml
- Forge/Contracts/schema.md
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- USER_INSTRUCTIONS.md
- app/api/rate_limit.py
- app/api/routers/builds.py
- app/clients/agent_client.py
- app/repos/build_repo.py
- app/services/build_service.py
- db/migrations/004_build_costs.sql
- tests/test_build_repo.py
- tests/test_build_service.py
- tests/test_builds_router.py
- web/src/App.tsx
- web/src/__tests__/Build.test.tsx
- web/src/pages/BuildComplete.tsx
- web/src/pages/BuildProgress.tsx

## git status -sb
    ## master...origin/master
    M  Forge/Contracts/physics.yaml
    M  Forge/Contracts/schema.md
    M  Forge/evidence/test_runs.md
    M  Forge/evidence/test_runs_latest.md
     M Forge/evidence/updatedifflog.md
    M  USER_INSTRUCTIONS.md
    M  app/api/rate_limit.py
    M  app/api/routers/builds.py
    M  app/clients/agent_client.py
    M  app/repos/build_repo.py
    M  app/services/build_service.py
    A  db/migrations/004_build_costs.sql
    M  tests/test_build_repo.py
    M  tests/test_build_service.py
    M  tests/test_builds_router.py
    M  web/src/App.tsx
    M  web/src/__tests__/Build.test.tsx
    A  web/src/pages/BuildComplete.tsx
    M  web/src/pages/BuildProgress.tsx
    ?? forgeguard_lock.ps1

## Verification
- TODO: verification evidence (static -> runtime -> behavior -> contract).

## Notes (optional)
- TODO: blockers, risks, constraints.

## Next Steps
- TODO: next actions (small, specific).

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index c8f6a55..b5337ec 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -309,6 +309,26 @@ paths:
             items: BuildLogEntry[]
             total: integer
     
    +  # -- Build Summary & Instructions (Phase 11) --------------------------
    +
    +  /projects/{project_id}/build/summary:
    +    get:
    +      summary: "Complete build summary with cost breakdown"
    +      auth: bearer
    +      response:
    +        build: BuildStatus
    +        cost: BuildCostSummary
    +        elapsed_seconds: float | null
    +        loop_count: integer
    +
    +  /projects/{project_id}/build/instructions:
    +    get:
    +      summary: "Generated deployment instructions from project contracts"
    +      auth: bearer
    +      response:
    +        project_name: string
    +        instructions: string
    +
     # -- Schemas --------------------------------------------------------
     
     schemas:
    @@ -401,3 +421,27 @@ schemas:
         level: string
         message: string
         created_at: datetime
    +
    +  BuildStatus:
    +    id: uuid
    +    project_id: uuid
    +    phase: string
    +    status: string
    +    loop_count: integer
    +    started_at: datetime | null
    +    completed_at: datetime | null
    +    error_detail: string | null
    +    created_at: datetime
    +
    +  BuildCostSummary:
    +    total_input_tokens: integer
    +    total_output_tokens: integer
    +    total_cost_usd: float
    +    phases: BuildCostEntry[]
    +
    +  BuildCostEntry:
    +    phase: string
    +    input_tokens: integer
    +    output_tokens: integer
    +    model: string
    +    estimated_cost_usd: float
    diff --git a/Forge/Contracts/schema.md b/Forge/Contracts/schema.md
    index e53f083..e68e84e 100644
    --- a/Forge/Contracts/schema.md
    +++ b/Forge/Contracts/schema.md
    @@ -226,6 +226,29 @@ CREATE INDEX idx_build_logs_build_id_timestamp ON build_logs(build_id, timestamp
     
     ---
     
    +### build_costs
    +
    +Stores token usage and cost estimation per build phase.
    +
    +```sql
    +CREATE TABLE build_costs (
    +    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    build_id            UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    +    phase               VARCHAR(100) NOT NULL,
    +    input_tokens        INTEGER NOT NULL DEFAULT 0,
    +    output_tokens       INTEGER NOT NULL DEFAULT 0,
    +    model               VARCHAR(100) NOT NULL,
    +    estimated_cost_usd  NUMERIC(10, 6) NOT NULL DEFAULT 0,
    +    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +```
    +
    +```sql
    +CREATE INDEX idx_build_costs_build_id ON build_costs(build_id);
    +```
    +
    +---
    +
     ## Schema -> Phase Traceability
     
     | Table | Repo Created In | Wired To Caller In | Notes |
    @@ -238,6 +261,7 @@ CREATE INDEX idx_build_logs_build_id_timestamp ON build_logs(build_id, timestamp
     | project_contracts | Phase 8 | Phase 8 | Generated contract files |
     | builds | Phase 9 | Phase 9 | Build orchestration runs |
     | build_logs | Phase 9 | Phase 9 | Streaming builder output |
    +| build_costs | Phase 11 | Phase 11 | Token usage and cost tracking |
     
     ---
     
    @@ -250,4 +274,5 @@ db/migrations/
       001_initial_schema.sql
       002_projects.sql
       003_builds.sql
    +  004_build_costs.sql
     ```
    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    index 5d8508d..87a7d83 100644
    --- a/Forge/evidence/test_runs.md
    +++ b/Forge/evidence/test_runs.md
    @@ -840,3 +840,52 @@ M  web/vite.config.ts
     
     ```
     
    +## Test Run 2026-02-15T04:01:50Z
    +- Status: PASS
    +- Start: 2026-02-15T04:01:50Z
    +- End: 2026-02-15T04:01:51Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: ef1b753792775a7c9c70756a9b1291ed2848554e
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- git status -sb:
    +```
    +## master...origin/master
    + M Forge/Contracts/physics.yaml
    + M Forge/Contracts/schema.md
    + M USER_INSTRUCTIONS.md
    + M app/api/rate_limit.py
    + M app/api/routers/builds.py
    + M app/clients/agent_client.py
    + M app/repos/build_repo.py
    + M app/services/build_service.py
    + M tests/test_build_repo.py
    + M tests/test_build_service.py
    + M tests/test_builds_router.py
    + M web/src/App.tsx
    + M web/src/__tests__/Build.test.tsx
    + M web/src/pages/BuildProgress.tsx
    +?? db/migrations/004_build_costs.sql
    +?? forgeguard_lock.ps1
    +?? web/src/pages/BuildComplete.tsx
    +```
    +- git diff --stat:
    +```
    + Forge/Contracts/physics.yaml     |  44 ++++++++++
    + Forge/Contracts/schema.md        |  25 ++++++
    + USER_INSTRUCTIONS.md             |  74 +++++++++++++++-
    + app/api/rate_limit.py            |   3 +
    + app/api/routers/builds.py        |  39 +++++++++
    + app/clients/agent_client.py      |  34 +++++++-
    + app/repos/build_repo.py          |  70 +++++++++++++++-
    + app/services/build_service.py    | 177 ++++++++++++++++++++++++++++++++++++++-
    + tests/test_build_repo.py         |  71 +++++++++++++++-
    + tests/test_build_service.py      | 140 +++++++++++++++++++++++++++++++
    + tests/test_builds_router.py      |  98 ++++++++++++++++++++++
    + web/src/App.tsx                  |   9 ++
    + web/src/__tests__/Build.test.tsx | 149 +++++++++++++++++++++++++++++++-
    + web/src/pages/BuildProgress.tsx  |   1 +
    + 14 files changed, 923 insertions(+), 11 deletions(-)
    +```
    +
    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    index 483a3ac..b3064f1 100644
    --- a/Forge/evidence/test_runs_latest.md
    +++ b/Forge/evidence/test_runs_latest.md
    @@ -1,28 +1,48 @@
     ´╗┐Status: PASS
    -Start: 2026-02-15T03:37:06Z
    -End: 2026-02-15T03:37:08Z
    +Start: 2026-02-15T04:01:50Z
    +End: 2026-02-15T04:01:51Z
     Branch: master
    -HEAD: 58d55f59accd7fbdd338a2ab5bba5f1bc3d6ce9b
    +HEAD: ef1b753792775a7c9c70756a9b1291ed2848554e
     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
     compileall exit: 0
     import_sanity exit: 0
     git status -sb:
     ```
     ## master...origin/master
    -M  web/src/App.tsx
    -A  web/src/__tests__/Build.test.tsx
    -A  web/src/components/BuildAuditCard.tsx
    -A  web/src/components/BuildLogViewer.tsx
    -A  web/src/components/PhaseProgressBar.tsx
    -A  web/src/components/ProjectCard.tsx
    -A  web/src/pages/BuildProgress.tsx
    -M  web/src/pages/Dashboard.tsx
    -A  web/src/pages/ProjectDetail.tsx
    -M  web/vite.config.ts
    + M Forge/Contracts/physics.yaml
    + M Forge/Contracts/schema.md
    + M USER_INSTRUCTIONS.md
    + M app/api/rate_limit.py
    + M app/api/routers/builds.py
    + M app/clients/agent_client.py
    + M app/repos/build_repo.py
    + M app/services/build_service.py
    + M tests/test_build_repo.py
    + M tests/test_build_service.py
    + M tests/test_builds_router.py
    + M web/src/App.tsx
    + M web/src/__tests__/Build.test.tsx
    + M web/src/pages/BuildProgress.tsx
    +?? db/migrations/004_build_costs.sql
     ?? forgeguard_lock.ps1
    +?? web/src/pages/BuildComplete.tsx
     ```
     git diff --stat:
     ```
    -
    + Forge/Contracts/physics.yaml     |  44 ++++++++++
    + Forge/Contracts/schema.md        |  25 ++++++
    + USER_INSTRUCTIONS.md             |  74 +++++++++++++++-
    + app/api/rate_limit.py            |   3 +
    + app/api/routers/builds.py        |  39 +++++++++
    + app/clients/agent_client.py      |  34 +++++++-
    + app/repos/build_repo.py          |  70 +++++++++++++++-
    + app/services/build_service.py    | 177 ++++++++++++++++++++++++++++++++++++++-
    + tests/test_build_repo.py         |  71 +++++++++++++++-
    + tests/test_build_service.py      | 140 +++++++++++++++++++++++++++++++
    + tests/test_builds_router.py      |  98 ++++++++++++++++++++++
    + web/src/App.tsx                  |   9 ++
    + web/src/__tests__/Build.test.tsx | 149 +++++++++++++++++++++++++++++++-
    + web/src/pages/BuildProgress.tsx  |   1 +
    + 14 files changed, 923 insertions(+), 11 deletions(-)
     ```
     
    diff --git a/USER_INSTRUCTIONS.md b/USER_INSTRUCTIONS.md
    index bdc641e..c76b33a 100644
    --- a/USER_INSTRUCTIONS.md
    +++ b/USER_INSTRUCTIONS.md
    @@ -1,6 +1,6 @@
     # ForgeGuard ÔÇö User Instructions
     
    -ForgeGuard is a repository audit monitoring dashboard. It connects to your GitHub repos, listens for push events via webhooks, and runs automated audit checks on each commit.
    +ForgeGuard is a repository audit monitoring dashboard and autonomous build orchestrator. It connects to your GitHub repos, listens for push events via webhooks, runs automated audit checks on each commit, and can build entire projects autonomously using AI agents governed by the Forge framework.
     
     ---
     
    @@ -85,6 +85,9 @@ APP_URL=http://localhost:8000
     **Optional** (defaults shown):
     - `FRONTEND_URL` ÔÇö `http://localhost:5173`
     - `APP_URL` ÔÇö `http://localhost:8000`
    +- `ANTHROPIC_API_KEY` ÔÇö required for AI-powered builds (get one at [console.anthropic.com](https://console.anthropic.com))
    +- `LLM_BUILDER_MODEL` ÔÇö model for builds (default: `claude-opus-4-6`)
    +- `LLM_QUESTIONNAIRE_MODEL` ÔÇö model for questionnaire (default: `claude-3-5-haiku-20241022`)
     
     ---
     
    @@ -156,3 +159,72 @@ python -m pytest tests/ -v
     # Run frontend tests
     cd web && npx vitest run
     ```
    +
    +---
    +
    +## Project Creation & Build Workflow
    +
    +ForgeGuard can build entire projects autonomously using AI agents. Here's the workflow:
    +
    +### 1. Create a Project
    +
    +From the dashboard, click **Create Project** and enter a name and description.
    +
    +### 2. Complete the Questionnaire
    +
    +Navigate to your project and start the questionnaire. An AI assistant will guide you through key decisions:
    +- Product intent and goals
    +- Technology stack preferences
    +- Database schema design
    +- API endpoint planning
    +- UI requirements
    +- Deployment target
    +
    +### 3. Generate Contracts
    +
    +Once the questionnaire is complete, click **Generate Contracts** to produce Forge governance files (blueprint, manifesto, stack, schema, physics, etc.). You can review and edit these before building.
    +
    +### 4. Start a Build
    +
    +Click **Start Build** to spawn an autonomous builder agent. The builder:
    +- Works through phases (Phase 0 ÔåÆ Phase N) defined in your contracts
    +- Streams real-time progress via WebSocket
    +- Runs governance audits after each phase
    +- Automatically retries on audit failures (up to 3 times)
    +- Stops with `RISK_EXCEEDS_SCOPE` after 3 consecutive failures
    +
    +### 5. Monitor Progress
    +
    +The **Build Progress** page shows:
    +- Phase progress bar (grey=pending, blue=active, green=pass, red=fail)
    +- Streaming terminal-style logs with color-coded severity
    +- Per-phase audit results (A1-A9 checklist)
    +- Cancel button with confirmation
    +
    +### 6. Review Results
    +
    +When the build completes, the **Build Complete** page shows:
    +- Build summary (phases completed, elapsed time, loopback count)
    +- Cost estimate (input/output tokens per phase, total USD)
    +- Deployment instructions tailored to your project's stack
    +- Links to the generated code and build logs
    +
    +### Rate Limits
    +
    +Build endpoints are rate-limited to prevent abuse:
    +- **5 builds per user per hour** ÔÇö starting builds
    +- Concurrent builds per project are blocked (one at a time)
    +
    +### API Reference
    +
    +| Endpoint | Method | Description |
    +|----------|--------|-------------|
    +| `/projects` | GET | List your projects |
    +| `/projects` | POST | Create a new project |
    +| `/projects/{id}` | GET | Project detail |
    +| `/projects/{id}/build` | POST | Start a build |
    +| `/projects/{id}/build/cancel` | POST | Cancel active build |
    +| `/projects/{id}/build/status` | GET | Current build status |
    +| `/projects/{id}/build/logs` | GET | Paginated build logs |
    +| `/projects/{id}/build/summary` | GET | Build summary with cost breakdown |
    +| `/projects/{id}/build/instructions` | GET | Deployment instructions |
    diff --git a/app/api/rate_limit.py b/app/api/rate_limit.py
    index a041751..8947d07 100644
    --- a/app/api/rate_limit.py
    +++ b/app/api/rate_limit.py
    @@ -43,3 +43,6 @@ class RateLimiter:
     
     # Module-level singleton -- 30 requests per 60 seconds for webhooks.
     webhook_limiter = RateLimiter(max_requests=30, window_seconds=60)
    +
    +# Build limiter -- 5 build starts per user per hour (prevents abuse).
    +build_limiter = RateLimiter(max_requests=5, window_seconds=3600)
    diff --git a/app/api/routers/builds.py b/app/api/routers/builds.py
    index 8eb5c0b..9f0a5e0 100644
    --- a/app/api/routers/builds.py
    +++ b/app/api/routers/builds.py
    @@ -5,6 +5,7 @@ from uuid import UUID
     from fastapi import APIRouter, Depends, HTTPException, Query
     
     from app.api.deps import get_current_user
    +from app.api.rate_limit import build_limiter
     from app.services import build_service
     
     router = APIRouter(prefix="/projects", tags=["builds"])
    @@ -19,6 +20,8 @@ async def start_build(
         user: dict = Depends(get_current_user),
     ):
         """Start a build for a project."""
    +    if not build_limiter.is_allowed(str(user["id"])):
    +        raise HTTPException(status_code=429, detail="Build rate limit exceeded")
         try:
             build = await build_service.start_build(project_id, user["id"])
             return build
    @@ -87,3 +90,39 @@ async def get_build_logs(
             if "not found" in detail.lower():
                 raise HTTPException(status_code=404, detail=detail)
             raise HTTPException(status_code=400, detail=detail)
    +
    +
    +# ÔöÇÔöÇ GET /projects/{project_id}/build/summary ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +
    +
    +@router.get("/{project_id}/build/summary")
    +async def get_build_summary(
    +    project_id: UUID,
    +    user: dict = Depends(get_current_user),
    +):
    +    """Complete build summary with cost breakdown."""
    +    try:
    +        return await build_service.get_build_summary(project_id, user["id"])
    +    except ValueError as exc:
    +        detail = str(exc)
    +        if "not found" in detail.lower():
    +            raise HTTPException(status_code=404, detail=detail)
    +        raise HTTPException(status_code=400, detail=detail)
    +
    +
    +# ÔöÇÔöÇ GET /projects/{project_id}/build/instructions ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +
    +
    +@router.get("/{project_id}/build/instructions")
    +async def get_build_instructions(
    +    project_id: UUID,
    +    user: dict = Depends(get_current_user),
    +):
    +    """Generated deployment instructions."""
    +    try:
    +        return await build_service.get_build_instructions(project_id, user["id"])
    +    except ValueError as exc:
    +        detail = str(exc)
    +        if "not found" in detail.lower():
    +            raise HTTPException(status_code=404, detail=detail)
    +        raise HTTPException(status_code=400, detail=detail)
    diff --git a/app/clients/agent_client.py b/app/clients/agent_client.py
    index 1324756..f2d0c51 100644
    --- a/app/clients/agent_client.py
    +++ b/app/clients/agent_client.py
    @@ -8,7 +8,11 @@ yields incremental text chunks so the build service can persist them.
     No database access, no business logic, no HTTP framework imports.
     """
     
    +from __future__ import annotations
    +
    +import json
     from collections.abc import AsyncIterator
    +from dataclasses import dataclass, field
     
     import httpx
     
    @@ -16,6 +20,14 @@ ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
     ANTHROPIC_API_VERSION = "2023-06-01"
     
     
    +@dataclass
    +class StreamUsage:
    +    """Accumulates token usage from a streaming session."""
    +    input_tokens: int = 0
    +    output_tokens: int = 0
    +    model: str = ""
    +
    +
     def _headers(api_key: str) -> dict:
         """Build request headers for the Anthropic API."""
         return {
    @@ -31,6 +43,7 @@ async def stream_agent(
         system_prompt: str,
         messages: list[dict],
         max_tokens: int = 16384,
    +    usage_out: StreamUsage | None = None,
     ) -> AsyncIterator[str]:
         """Stream a builder agent session, yielding text chunks.
     
    @@ -40,6 +53,7 @@ async def stream_agent(
             system_prompt: Builder directive / system instructions.
             messages: Conversation history in Anthropic messages format.
             max_tokens: Maximum tokens for the response.
    +        usage_out: Optional StreamUsage to accumulate token counts.
     
         Yields:
             Incremental text chunks from the builder agent.
    @@ -70,12 +84,24 @@ async def stream_agent(
                     data = line[6:]  # strip "data: " prefix
                     if data == "[DONE]":
                         break
    -                # Parse SSE data for content_block_delta events
    +                # Parse SSE data for content and usage events
                     try:
    -                    import json
    -
                         event = json.loads(data)
    -                    if event.get("type") == "content_block_delta":
    +                    etype = event.get("type", "")
    +
    +                    # Capture usage from message_start
    +                    if etype == "message_start" and usage_out is not None:
    +                        msg = event.get("message", {})
    +                        usage = msg.get("usage", {})
    +                        usage_out.input_tokens += usage.get("input_tokens", 0)
    +                        usage_out.model = msg.get("model", model)
    +
    +                    # Capture usage from message_delta (output tokens)
    +                    if etype == "message_delta" and usage_out is not None:
    +                        usage = event.get("usage", {})
    +                        usage_out.output_tokens += usage.get("output_tokens", 0)
    +
    +                    if etype == "content_block_delta":
                             delta = event.get("delta", {})
                             text = delta.get("text", "")
                             if text:
    diff --git a/app/repos/build_repo.py b/app/repos/build_repo.py
    index 80d77ef..b1d4781 100644
    --- a/app/repos/build_repo.py
    +++ b/app/repos/build_repo.py
    @@ -1,6 +1,7 @@
    -"""Build repository -- database reads and writes for builds and build_logs tables."""
    +"""Build repository -- database reads and writes for builds, build_logs, and build_costs tables."""
     
     import json
    +from decimal import Decimal
     from datetime import datetime, timezone
     from uuid import UUID
     
    @@ -172,3 +173,70 @@ async def get_build_logs(
             offset,
         )
         return [dict(r) for r in rows], total
    +
    +
    +# ---------------------------------------------------------------------------
    +# build_costs
    +# ---------------------------------------------------------------------------
    +
    +
    +async def record_build_cost(
    +    build_id: UUID,
    +    phase: str,
    +    input_tokens: int,
    +    output_tokens: int,
    +    model: str,
    +    estimated_cost_usd: Decimal,
    +) -> dict:
    +    """Record token usage and cost for a build phase."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        INSERT INTO build_costs (build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd)
    +        VALUES ($1, $2, $3, $4, $5, $6)
    +        RETURNING id, build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at
    +        """,
    +        build_id,
    +        phase,
    +        input_tokens,
    +        output_tokens,
    +        model,
    +        estimated_cost_usd,
    +    )
    +    return dict(row)
    +
    +
    +async def get_build_costs(build_id: UUID) -> list[dict]:
    +    """Fetch all cost entries for a build."""
    +    pool = await get_pool()
    +    rows = await pool.fetch(
    +        """
    +        SELECT id, build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at
    +        FROM build_costs WHERE build_id = $1
    +        ORDER BY created_at ASC
    +        """,
    +        build_id,
    +    )
    +    return [dict(r) for r in rows]
    +
    +
    +async def get_build_cost_summary(build_id: UUID) -> dict:
    +    """Aggregate cost summary for a build."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        SELECT
    +            COALESCE(SUM(input_tokens), 0)  AS total_input_tokens,
    +            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
    +            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
    +            COUNT(*) AS phase_count
    +        FROM build_costs WHERE build_id = $1
    +        """,
    +        build_id,
    +    )
    +    return dict(row) if row else {
    +        "total_input_tokens": 0,
    +        "total_output_tokens": 0,
    +        "total_cost_usd": Decimal("0"),
    +        "phase_count": 0,
    +    }
    diff --git a/app/services/build_service.py b/app/services/build_service.py
    index 4933f59..e98f439 100644
    --- a/app/services/build_service.py
    +++ b/app/services/build_service.py
    @@ -1,17 +1,19 @@
     """Build service -- orchestrates autonomous builder sessions.
     
     Manages the full build lifecycle: validate contracts, spawn agent session,
    -stream progress, run inline audits, handle loopback, and advance phases.
    +stream progress, run inline audits, handle loopback, track costs, and
    +advance phases.
     
     No SQL, no HTTP framework, no direct GitHub API calls.
     """
     
     import asyncio
     import re
    +from decimal import Decimal
     from datetime import datetime, timezone
     from uuid import UUID
     
    -from app.clients.agent_client import stream_agent
    +from app.clients.agent_client import StreamUsage, stream_agent
     from app.config import settings
     from app.repos import build_repo
     from app.repos import project_repo
    @@ -29,6 +31,10 @@ BUILD_ERROR_SIGNAL = "RISK_EXCEEDS_SCOPE"
     # Active build tasks keyed by build_id
     _active_tasks: dict[str, asyncio.Task] = {}
     
    +# Cost-per-token estimates (USD) -- updated as pricing changes
    +_COST_PER_INPUT_TOKEN: Decimal = Decimal("0.000015")   # $15 / 1M input tokens
    +_COST_PER_OUTPUT_TOKEN: Decimal = Decimal("0.000075")  # $75 / 1M output tokens
    +
     
     # ---------------------------------------------------------------------------
     # Public API
    @@ -221,12 +227,16 @@ async def _run_build(
             accumulated_text = ""
             current_phase = "Phase 0"
     
    +        # Token usage tracking
    +        usage = StreamUsage()
    +
             # Stream agent output
             async for chunk in stream_agent(
                 api_key=settings.ANTHROPIC_API_KEY,
                 model=settings.LLM_BUILDER_MODEL,
                 system_prompt="You are an autonomous software builder operating under the Forge governance framework.",
                 messages=messages,
    +            usage_out=usage,
             ):
                 accumulated_text += chunk
     
    @@ -305,6 +315,9 @@ async def _run_build(
                             )
                             return
     
    +                # Record cost for this phase
    +                await _record_phase_cost(build_id, current_phase, usage)
    +
                     # Reset accumulated text for next phase detection
                     accumulated_text = ""
     
    @@ -424,3 +437,163 @@ async def _broadcast_build_event(
             "type": event_type,
             "payload": payload,
         })
    +
    +
    +async def _record_phase_cost(
    +    build_id: UUID, phase: str, usage: StreamUsage
    +) -> None:
    +    """Persist token usage for the current phase and reset counters."""
    +    input_t = usage.input_tokens
    +    output_t = usage.output_tokens
    +    model = usage.model or settings.LLM_BUILDER_MODEL
    +    cost = (Decimal(input_t) * _COST_PER_INPUT_TOKEN
    +            + Decimal(output_t) * _COST_PER_OUTPUT_TOKEN)
    +    await build_repo.record_build_cost(
    +        build_id, phase, input_t, output_t, model, cost
    +    )
    +    # Reset for next phase
    +    usage.input_tokens = 0
    +    usage.output_tokens = 0
    +
    +
    +# ---------------------------------------------------------------------------
    +# Summary / Instructions (Phase 11 API)
    +# ---------------------------------------------------------------------------
    +
    +
    +async def get_build_summary(project_id: UUID, user_id: UUID) -> dict:
    +    """Return a complete build summary with cost breakdown.
    +
    +    Raises:
    +        ValueError: If project not found, not owned, or no builds.
    +    """
    +    project = await project_repo.get_project_by_id(project_id)
    +    if not project or str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    latest = await build_repo.get_latest_build_for_project(project_id)
    +    if not latest:
    +        raise ValueError("No builds found for this project")
    +
    +    build_id = latest["id"]
    +    cost_summary = await build_repo.get_build_cost_summary(build_id)
    +    cost_entries = await build_repo.get_build_costs(build_id)
    +
    +    elapsed_seconds: float | None = None
    +    if latest.get("started_at") and latest.get("completed_at"):
    +        elapsed_seconds = (
    +            latest["completed_at"] - latest["started_at"]
    +        ).total_seconds()
    +
    +    return {
    +        "build": latest,
    +        "cost": {
    +            "total_input_tokens": cost_summary["total_input_tokens"],
    +            "total_output_tokens": cost_summary["total_output_tokens"],
    +            "total_cost_usd": float(cost_summary["total_cost_usd"]),
    +            "phases": [
    +                {
    +                    "phase": e["phase"],
    +                    "input_tokens": e["input_tokens"],
    +                    "output_tokens": e["output_tokens"],
    +                    "model": e["model"],
    +                    "estimated_cost_usd": float(e["estimated_cost_usd"]),
    +                }
    +                for e in cost_entries
    +            ],
    +        },
    +        "elapsed_seconds": elapsed_seconds,
    +        "loop_count": latest["loop_count"],
    +    }
    +
    +
    +async def get_build_instructions(project_id: UUID, user_id: UUID) -> dict:
    +    """Generate deployment instructions from the project's stack contract.
    +
    +    Raises:
    +        ValueError: If project not found, not owned, or no contracts.
    +    """
    +    project = await project_repo.get_project_by_id(project_id)
    +    if not project or str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    contracts = await project_repo.get_contracts_by_project(project_id)
    +    if not contracts:
    +        raise ValueError("No contracts found for this project")
    +
    +    stack_content = ""
    +    blueprint_content = ""
    +    for c in contracts:
    +        if c["contract_type"] == "stack":
    +            stack_content = c["content"]
    +        elif c["contract_type"] == "blueprint":
    +            blueprint_content = c["content"]
    +
    +    instructions = _generate_deploy_instructions(
    +        project["name"], stack_content, blueprint_content
    +    )
    +    return {
    +        "project_name": project["name"],
    +        "instructions": instructions,
    +    }
    +
    +
    +def _generate_deploy_instructions(
    +    project_name: str, stack_content: str, blueprint_content: str
    +) -> str:
    +    """Build deployment instructions from stack and blueprint contracts."""
    +    lines = [f"# Deployment Instructions ÔÇö {project_name}\n"]
    +
    +    # Detect stack components
    +    has_python = "python" in stack_content.lower()
    +    has_node = "node" in stack_content.lower() or "react" in stack_content.lower()
    +    has_postgres = "postgres" in stack_content.lower()
    +    has_render = "render" in stack_content.lower()
    +
    +    lines.append("## Prerequisites\n")
    +    if has_python:
    +        lines.append("- Python 3.12+")
    +    if has_node:
    +        lines.append("- Node.js 18+")
    +    if has_postgres:
    +        lines.append("- PostgreSQL 15+")
    +    lines.append("- Git 2.x\n")
    +
    +    lines.append("## Setup Steps\n")
    +    lines.append("1. Clone the generated repository")
    +    lines.append("2. Copy `.env.example` to `.env` and fill in credentials")
    +    if has_python:
    +        lines.append("3. Create virtual environment: `python -m venv .venv`")
    +        lines.append("4. Install dependencies: `pip install -r requirements.txt`")
    +    if has_node:
    +        lines.append("5. Install frontend: `cd web && npm install`")
    +    if has_postgres:
    +        lines.append("6. Run database migrations: `psql $DATABASE_URL -f db/migrations/*.sql`")
    +    lines.append("7. Start the application: `pwsh -File boot.ps1`\n")
    +
    +    if has_render:
    +        lines.append("## Render Deployment\n")
    +        lines.append("1. Create a new **Web Service** on Render")
    +        lines.append("2. Connect your GitHub repository")
    +        lines.append("3. Set **Build Command**: `pip install -r requirements.txt`")
    +        lines.append("4. Set **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`")
    +        lines.append("5. Add a **PostgreSQL** database")
    +        lines.append("6. Configure environment variables in the Render dashboard")
    +        if has_node:
    +            lines.append("7. For the frontend, create a **Static Site** pointing to `web/`")
    +            lines.append("8. Set **Build Command**: `npm install && npm run build`")
    +            lines.append("9. Set **Publish Directory**: `web/dist`")
    +
    +    lines.append("\n## Environment Variables\n")
    +    lines.append("| Variable | Required | Description |")
    +    lines.append("|----------|----------|-------------|")
    +    lines.append("| `DATABASE_URL` | Yes | PostgreSQL connection string |")
    +    lines.append("| `JWT_SECRET` | Yes | Random secret for session tokens |")
    +    if "github" in stack_content.lower() or "oauth" in stack_content.lower():
    +        lines.append("| `GITHUB_CLIENT_ID` | Yes | GitHub OAuth app client ID |")
    +        lines.append("| `GITHUB_CLIENT_SECRET` | Yes | GitHub OAuth app secret |")
    +        lines.append("| `GITHUB_WEBHOOK_SECRET` | Yes | Webhook signature secret |")
    +    lines.append("| `FRONTEND_URL` | No | Frontend origin for CORS |")
    +    lines.append("| `APP_URL` | No | Backend URL |")
    +
    +    return "\n".join(lines)
    diff --git a/db/migrations/004_build_costs.sql b/db/migrations/004_build_costs.sql
    new file mode 100644
    index 0000000..ac5274b
    --- /dev/null
    +++ b/db/migrations/004_build_costs.sql
    @@ -0,0 +1,15 @@
    +-- Phase 11: Build cost tracking
    +-- build_costs: token usage and cost estimation per build phase
    +
    +CREATE TABLE IF NOT EXISTS build_costs (
    +    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    build_id            UUID NOT NULL REFERENCES builds(id) ON DELETE CASCADE,
    +    phase               VARCHAR(100) NOT NULL,
    +    input_tokens        INTEGER NOT NULL DEFAULT 0,
    +    output_tokens       INTEGER NOT NULL DEFAULT 0,
    +    model               VARCHAR(100) NOT NULL,
    +    estimated_cost_usd  NUMERIC(10, 6) NOT NULL DEFAULT 0,
    +    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +
    +CREATE INDEX IF NOT EXISTS idx_build_costs_build_id ON build_costs(build_id);
    diff --git a/tests/test_build_repo.py b/tests/test_build_repo.py
    index b08298b..218e2b3 100644
    --- a/tests/test_build_repo.py
    +++ b/tests/test_build_repo.py
    @@ -1,6 +1,7 @@
    -"""Tests for app/repos/build_repo.py -- build and build_logs CRUD operations."""
    +"""Tests for app/repos/build_repo.py -- build, build_logs, and build_costs CRUD operations."""
     
     import uuid
    +from decimal import Decimal
     from datetime import datetime, timezone
     from unittest.mock import AsyncMock, MagicMock, patch
     
    @@ -195,3 +196,71 @@ async def test_get_build_logs(mock_get_pool):
     
         assert total == 42
         assert len(logs) == 2
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: build_costs
    +# ---------------------------------------------------------------------------
    +
    +
    +def _cost_row(**overrides):
    +    """Create a fake build_cost DB row."""
    +    defaults = {
    +        "id": uuid.uuid4(),
    +        "build_id": uuid.uuid4(),
    +        "phase": "Phase 0",
    +        "input_tokens": 1000,
    +        "output_tokens": 500,
    +        "model": "claude-opus-4-6",
    +        "estimated_cost_usd": Decimal("0.052500"),
    +        "created_at": datetime.now(timezone.utc),
    +    }
    +    defaults.update(overrides)
    +    return defaults
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.repos.build_repo.get_pool")
    +async def test_record_build_cost(mock_get_pool):
    +    pool = _fake_pool()
    +    row = _cost_row()
    +    pool.fetchrow.return_value = row
    +    mock_get_pool.return_value = pool
    +
    +    result = await build_repo.record_build_cost(
    +        row["build_id"], "Phase 0", 1000, 500, "claude-opus-4-6", Decimal("0.052500")
    +    )
    +
    +    pool.fetchrow.assert_called_once()
    +    assert result["phase"] == "Phase 0"
    +    assert result["input_tokens"] == 1000
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.repos.build_repo.get_pool")
    +async def test_get_build_costs(mock_get_pool):
    +    pool = _fake_pool()
    +    pool.fetch.return_value = [_cost_row(), _cost_row(phase="Phase 1")]
    +    mock_get_pool.return_value = pool
    +
    +    costs = await build_repo.get_build_costs(uuid.uuid4())
    +
    +    assert len(costs) == 2
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.repos.build_repo.get_pool")
    +async def test_get_build_cost_summary(mock_get_pool):
    +    pool = _fake_pool()
    +    pool.fetchrow.return_value = {
    +        "total_input_tokens": 5000,
    +        "total_output_tokens": 2500,
    +        "total_cost_usd": Decimal("0.225000"),
    +        "phase_count": 3,
    +    }
    +    mock_get_pool.return_value = pool
    +
    +    summary = await build_repo.get_build_cost_summary(uuid.uuid4())
    +
    +    assert summary["total_input_tokens"] == 5000
    +    assert summary["phase_count"] == 3
    diff --git a/tests/test_build_service.py b/tests/test_build_service.py
    index 6cc84a2..1c7792e 100644
    --- a/tests/test_build_service.py
    +++ b/tests/test_build_service.py
    @@ -2,6 +2,7 @@
     
     import asyncio
     import uuid
    +from decimal import Decimal
     from datetime import datetime, timezone
     from unittest.mock import AsyncMock, MagicMock, patch
     
    @@ -291,3 +292,142 @@ async def test_fail_build(mock_build_repo, mock_manager):
         call_kwargs = mock_build_repo.update_build_status.call_args
         assert call_kwargs[0][1] == "failed"
         mock_manager.send_to_user.assert_called_once()
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: get_build_summary
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.project_repo")
    +@patch("app.services.build_service.build_repo")
    +async def test_get_build_summary(mock_build_repo, mock_project_repo):
    +    """get_build_summary returns build, cost, elapsed, and loop_count."""
    +    now = datetime.now(timezone.utc)
    +    b = _build(
    +        status="completed",
    +        started_at=now,
    +        completed_at=now,
    +        loop_count=1,
    +    )
    +    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    +    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=b)
    +    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
    +        "total_input_tokens": 3000,
    +        "total_output_tokens": 1500,
    +        "total_cost_usd": Decimal("0.157500"),
    +        "phase_count": 2,
    +    })
    +    mock_build_repo.get_build_costs = AsyncMock(return_value=[
    +        {
    +            "phase": "Phase 0",
    +            "input_tokens": 1500,
    +            "output_tokens": 800,
    +            "model": "claude-opus-4-6",
    +            "estimated_cost_usd": Decimal("0.082500"),
    +        },
    +    ])
    +
    +    result = await build_service.get_build_summary(_PROJECT_ID, _USER_ID)
    +
    +    assert result["build"]["status"] == "completed"
    +    assert result["cost"]["total_input_tokens"] == 3000
    +    assert len(result["cost"]["phases"]) == 1
    +    assert result["loop_count"] == 1
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.project_repo")
    +@patch("app.services.build_service.build_repo")
    +async def test_get_build_summary_not_found(mock_build_repo, mock_project_repo):
    +    """get_build_summary raises ValueError for missing project."""
    +    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)
    +
    +    with pytest.raises(ValueError, match="not found"):
    +        await build_service.get_build_summary(_PROJECT_ID, _USER_ID)
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: get_build_instructions
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.project_repo")
    +async def test_get_build_instructions(mock_project_repo):
    +    """get_build_instructions returns deploy instructions from stack contract."""
    +    mock_project_repo.get_project_by_id = AsyncMock(
    +        return_value=_project(name="TestApp")
    +    )
    +    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[
    +        {"contract_type": "stack", "content": "Python 3.12, PostgreSQL, React, Render"},
    +        {"contract_type": "blueprint", "content": "# Blueprint\nA test app"},
    +    ])
    +
    +    result = await build_service.get_build_instructions(_PROJECT_ID, _USER_ID)
    +
    +    assert result["project_name"] == "TestApp"
    +    assert "Python 3.12" in result["instructions"]
    +    assert "Render" in result["instructions"]
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.project_repo")
    +async def test_get_build_instructions_no_contracts(mock_project_repo):
    +    """get_build_instructions raises ValueError when no contracts."""
    +    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    +    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[])
    +
    +    with pytest.raises(ValueError, match="No contracts"):
    +        await build_service.get_build_instructions(_PROJECT_ID, _USER_ID)
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: _generate_deploy_instructions
    +# ---------------------------------------------------------------------------
    +
    +
    +def test_generate_deploy_instructions_full_stack():
    +    """_generate_deploy_instructions includes all stack components."""
    +    result = build_service._generate_deploy_instructions(
    +        "MyApp",
    +        "Python 3.12+, React, PostgreSQL 15+, Render",
    +        "# Blueprint",
    +    )
    +
    +    assert "MyApp" in result
    +    assert "Python 3.12" in result
    +    assert "Node.js" in result
    +    assert "PostgreSQL" in result
    +    assert "Render" in result
    +
    +
    +def test_generate_deploy_instructions_minimal():
    +    """_generate_deploy_instructions handles minimal stack."""
    +    result = build_service._generate_deploy_instructions(
    +        "SimpleApp", "Go backend", "# Simple"
    +    )
    +
    +    assert "SimpleApp" in result
    +    assert "Git 2.x" in result
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: _record_phase_cost
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.build_repo")
    +async def test_record_phase_cost(mock_build_repo):
    +    """_record_phase_cost persists cost and resets usage counters."""
    +    from app.clients.agent_client import StreamUsage
    +    mock_build_repo.record_build_cost = AsyncMock()
    +
    +    usage = StreamUsage(input_tokens=1000, output_tokens=500, model="claude-opus-4-6")
    +    await build_service._record_phase_cost(_BUILD_ID, "Phase 0", usage)
    +
    +    mock_build_repo.record_build_cost.assert_called_once()
    +    assert usage.input_tokens == 0
    +    assert usage.output_tokens == 0
    diff --git a/tests/test_builds_router.py b/tests/test_builds_router.py
    index 1fd532c..cea1ab7 100644
    --- a/tests/test_builds_router.py
    +++ b/tests/test_builds_router.py
    @@ -225,7 +225,105 @@ def test_build_endpoints_require_auth(client):
             ("POST", f"/projects/{pid}/build/cancel"),
             ("GET", f"/projects/{pid}/build/status"),
             ("GET", f"/projects/{pid}/build/logs"),
    +        ("GET", f"/projects/{pid}/build/summary"),
    +        ("GET", f"/projects/{pid}/build/instructions"),
         ]
         for method, url in endpoints:
             resp = client.request(method, url)
             assert resp.status_code == 401, f"{method} {url} should require auth"
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: GET /projects/{id}/build/summary
    +# ---------------------------------------------------------------------------
    +
    +
    +@patch("app.api.routers.builds.build_service.get_build_summary", new_callable=AsyncMock)
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_get_build_summary(mock_get_user, mock_summary, client):
    +    """GET /projects/{id}/build/summary returns cost breakdown."""
    +    mock_get_user.return_value = _USER
    +    mock_summary.return_value = {
    +        "build": _build(status="completed"),
    +        "cost": {
    +            "total_input_tokens": 5000,
    +            "total_output_tokens": 2500,
    +            "total_cost_usd": 0.26,
    +            "phases": [],
    +        },
    +        "elapsed_seconds": 120.5,
    +        "loop_count": 0,
    +    }
    +
    +    resp = client.get(f"/projects/{_PROJECT_ID}/build/summary", headers=_auth_header())
    +
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert data["cost"]["total_input_tokens"] == 5000
    +    assert data["elapsed_seconds"] == 120.5
    +
    +
    +@patch("app.api.routers.builds.build_service.get_build_summary", new_callable=AsyncMock)
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_get_build_summary_not_found(mock_get_user, mock_summary, client):
    +    """GET /projects/{id}/build/summary returns 404 for missing project."""
    +    mock_get_user.return_value = _USER
    +    mock_summary.side_effect = ValueError("Project not found")
    +
    +    resp = client.get(f"/projects/{_PROJECT_ID}/build/summary", headers=_auth_header())
    +
    +    assert resp.status_code == 404
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: GET /projects/{id}/build/instructions
    +# ---------------------------------------------------------------------------
    +
    +
    +@patch("app.api.routers.builds.build_service.get_build_instructions", new_callable=AsyncMock)
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_get_build_instructions(mock_get_user, mock_instr, client):
    +    """GET /projects/{id}/build/instructions returns deploy instructions."""
    +    mock_get_user.return_value = _USER
    +    mock_instr.return_value = {
    +        "project_name": "TestApp",
    +        "instructions": "# Deploy\n1. Clone repo\n2. Run boot.ps1",
    +    }
    +
    +    resp = client.get(f"/projects/{_PROJECT_ID}/build/instructions", headers=_auth_header())
    +
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert data["project_name"] == "TestApp"
    +    assert "Clone repo" in data["instructions"]
    +
    +
    +@patch("app.api.routers.builds.build_service.get_build_instructions", new_callable=AsyncMock)
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_get_build_instructions_not_found(mock_get_user, mock_instr, client):
    +    """GET /projects/{id}/build/instructions returns 404 for missing project."""
    +    mock_get_user.return_value = _USER
    +    mock_instr.side_effect = ValueError("Project not found")
    +
    +    resp = client.get(f"/projects/{_PROJECT_ID}/build/instructions", headers=_auth_header())
    +
    +    assert resp.status_code == 404
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: Rate limiting on build start
    +# ---------------------------------------------------------------------------
    +
    +
    +@patch("app.api.routers.builds.build_limiter")
    +@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_start_build_rate_limited(mock_get_user, mock_start, mock_limiter, client):
    +    """POST /projects/{id}/build returns 429 when rate limited."""
    +    mock_get_user.return_value = _USER
    +    mock_limiter.is_allowed.return_value = False
    +
    +    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())
    +
    +    assert resp.status_code == 429
    +    mock_start.assert_not_called()
    diff --git a/web/src/App.tsx b/web/src/App.tsx
    index 6ee24ff..7a6f811 100644
    --- a/web/src/App.tsx
    +++ b/web/src/App.tsx
    @@ -6,6 +6,7 @@ import CommitTimeline from './pages/CommitTimeline';
     import AuditDetailPage from './pages/AuditDetail';
     import ProjectDetail from './pages/ProjectDetail';
     import BuildProgress from './pages/BuildProgress';
    +import BuildComplete from './pages/BuildComplete';
     import { AuthProvider, useAuth } from './context/AuthContext';
     import { ToastProvider } from './context/ToastContext';
     
    @@ -63,6 +64,14 @@ function App() {
                     </ProtectedRoute>
                   }
                 />
    +            <Route
    +              path="/projects/:projectId/build/complete"
    +              element={
    +                <ProtectedRoute>
    +                  <BuildComplete />
    +                </ProtectedRoute>
    +              }
    +            />
                 <Route path="*" element={<Navigate to="/" replace />} />
               </Routes>
             </BrowserRouter>
    diff --git a/web/src/__tests__/Build.test.tsx b/web/src/__tests__/Build.test.tsx
    index 0ef58e2..daae829 100644
    --- a/web/src/__tests__/Build.test.tsx
    +++ b/web/src/__tests__/Build.test.tsx
    @@ -1,9 +1,11 @@
    -import { describe, it, expect } from 'vitest';
    -import { render, screen } from '@testing-library/react';
    +import { describe, it, expect, vi, beforeEach } from 'vitest';
    +import { render, screen, waitFor } from '@testing-library/react';
    +import { MemoryRouter, Route, Routes } from 'react-router-dom';
     import PhaseProgressBar from '../components/PhaseProgressBar';
     import BuildLogViewer from '../components/BuildLogViewer';
     import BuildAuditCard from '../components/BuildAuditCard';
     import ProjectCard from '../components/ProjectCard';
    +import BuildComplete from '../pages/BuildComplete';
     
     describe('PhaseProgressBar', () => {
       it('renders the progress bar', () => {
    @@ -156,3 +158,146 @@ describe('ProjectCard', () => {
         expect(screen.getByText(/Phase 3/)).toBeInTheDocument();
       });
     });
    +
    +// ÔöÇÔöÇ BuildComplete ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +
    +vi.mock('../context/AuthContext', () => ({
    +  useAuth: () => ({ token: 'test-token' }),
    +  AuthProvider: ({ children }: { children: unknown }) => children,
    +}));
    +
    +const mockSummary = {
    +  build: {
    +    id: 'b1',
    +    project_id: 'p1',
    +    phase: 'Phase 5',
    +    status: 'completed',
    +    loop_count: 1,
    +    started_at: '2026-01-01T00:00:00Z',
    +    completed_at: '2026-01-01T01:00:00Z',
    +    error_detail: null,
    +    created_at: '2026-01-01T00:00:00Z',
    +  },
    +  cost: {
    +    total_input_tokens: 50000,
    +    total_output_tokens: 25000,
    +    total_cost_usd: 2.625,
    +    phases: [
    +      { phase: 'Phase 0', input_tokens: 10000, output_tokens: 5000, model: 'claude-opus-4-6', estimated_cost_usd: 0.525 },
    +    ],
    +  },
    +  elapsed_seconds: 3600,
    +  loop_count: 1,
    +};
    +
    +const mockInstructions = {
    +  project_name: 'TestApp',
    +  instructions: '# Deployment Instructions ÔÇö TestApp\n\n## Prerequisites\n- Python 3.12+',
    +};
    +
    +function renderBuildComplete(fetchImpl?: typeof global.fetch) {
    +  if (fetchImpl) {
    +    global.fetch = fetchImpl;
    +  }
    +  return render(
    +    <MemoryRouter initialEntries={['/projects/p1/build/complete']}>
    +      <Routes>
    +        <Route path="/projects/:projectId/build/complete" element={<BuildComplete />} />
    +      </Routes>
    +    </MemoryRouter>,
    +  );
    +}
    +
    +describe('BuildComplete', () => {
    +  beforeEach(() => {
    +    vi.restoreAllMocks();
    +  });
    +
    +  it('shows skeleton while loading', () => {
    +    global.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
    +    renderBuildComplete();
    +    expect(screen.getByTestId('build-complete-skeleton')).toBeInTheDocument();
    +  });
    +
    +  it('renders build complete page with data', async () => {
    +    global.fetch = vi.fn((url: string | URL | Request) => {
    +      const u = typeof url === 'string' ? url : url.toString();
    +      if (u.includes('/summary')) {
    +        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
    +      }
    +      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    +    }) as unknown as typeof fetch;
    +
    +    renderBuildComplete();
    +    await waitFor(() => expect(screen.getByTestId('build-complete')).toBeInTheDocument());
    +
    +    expect(screen.getByText('Build Complete')).toBeInTheDocument();
    +    expect(screen.getByText('TestApp')).toBeInTheDocument();
    +  });
    +
    +  it('displays cost summary', async () => {
    +    global.fetch = vi.fn((url: string | URL | Request) => {
    +      const u = typeof url === 'string' ? url : url.toString();
    +      if (u.includes('/summary')) {
    +        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
    +      }
    +      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    +    }) as unknown as typeof fetch;
    +
    +    renderBuildComplete();
    +    await waitFor(() => expect(screen.getByTestId('summary-cost')).toBeInTheDocument());
    +
    +    expect(screen.getByText('$2.63')).toBeInTheDocument();
    +  });
    +
    +  it('displays deployment instructions', async () => {
    +    global.fetch = vi.fn((url: string | URL | Request) => {
    +      const u = typeof url === 'string' ? url : url.toString();
    +      if (u.includes('/summary')) {
    +        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
    +      }
    +      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    +    }) as unknown as typeof fetch;
    +
    +    renderBuildComplete();
    +    await waitFor(() => expect(screen.getByTestId('deploy-instructions')).toBeInTheDocument());
    +
    +    expect(screen.getByText(/Prerequisites/)).toBeInTheDocument();
    +  });
    +
    +  it('shows status banner for completed build', async () => {
    +    global.fetch = vi.fn((url: string | URL | Request) => {
    +      const u = typeof url === 'string' ? url : url.toString();
    +      if (u.includes('/summary')) {
    +        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSummary) } as Response);
    +      }
    +      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    +    }) as unknown as typeof fetch;
    +
    +    renderBuildComplete();
    +    await waitFor(() => expect(screen.getByTestId('build-status-banner')).toBeInTheDocument());
    +
    +    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    +    expect(screen.getByText('All phases passed')).toBeInTheDocument();
    +  });
    +
    +  it('shows failed status for failed build', async () => {
    +    const failedSummary = {
    +      ...mockSummary,
    +      build: { ...mockSummary.build, status: 'failed', error_detail: 'RISK_EXCEEDS_SCOPE' },
    +    };
    +    global.fetch = vi.fn((url: string | URL | Request) => {
    +      const u = typeof url === 'string' ? url : url.toString();
    +      if (u.includes('/summary')) {
    +        return Promise.resolve({ ok: true, json: () => Promise.resolve(failedSummary) } as Response);
    +      }
    +      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockInstructions) } as Response);
    +    }) as unknown as typeof fetch;
    +
    +    renderBuildComplete();
    +    await waitFor(() => expect(screen.getByTestId('build-status-banner')).toBeInTheDocument());
    +
    +    expect(screen.getByText('Build Failed')).toBeInTheDocument();
    +    expect(screen.getByText('FAILED')).toBeInTheDocument();
    +  });
    +});
    diff --git a/web/src/pages/BuildComplete.tsx b/web/src/pages/BuildComplete.tsx
    new file mode 100644
    index 0000000..e419947
    --- /dev/null
    +++ b/web/src/pages/BuildComplete.tsx
    @@ -0,0 +1,263 @@
    +import { useEffect, useState, useCallback } from 'react';
    +import { useParams, useNavigate } from 'react-router-dom';
    +import { useAuth } from '../context/AuthContext';
    +import Skeleton from '../components/Skeleton';
    +
    +interface BuildCostEntry {
    +  phase: string;
    +  input_tokens: number;
    +  output_tokens: number;
    +  model: string;
    +  estimated_cost_usd: number;
    +}
    +
    +interface BuildSummary {
    +  build: {
    +    id: string;
    +    project_id: string;
    +    phase: string;
    +    status: string;
    +    loop_count: number;
    +    started_at: string | null;
    +    completed_at: string | null;
    +    error_detail: string | null;
    +    created_at: string;
    +  };
    +  cost: {
    +    total_input_tokens: number;
    +    total_output_tokens: number;
    +    total_cost_usd: number;
    +    phases: BuildCostEntry[];
    +  };
    +  elapsed_seconds: number | null;
    +  loop_count: number;
    +}
    +
    +interface DeployInstructions {
    +  project_name: string;
    +  instructions: string;
    +}
    +
    +const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +
    +export default function BuildComplete() {
    +  const { projectId } = useParams<{ projectId: string }>();
    +  const { token } = useAuth();
    +  const navigate = useNavigate();
    +
    +  const [summary, setSummary] = useState<BuildSummary | null>(null);
    +  const [instructions, setInstructions] = useState<DeployInstructions | null>(null);
    +  const [loading, setLoading] = useState(true);
    +  const [error, setError] = useState<string | null>(null);
    +
    +  const headers = useCallback(() => ({
    +    Authorization: `Bearer ${token}`,
    +    'Content-Type': 'application/json',
    +  }), [token]);
    +
    +  useEffect(() => {
    +    if (!token || !projectId) return;
    +    const load = async () => {
    +      try {
    +        const [sumRes, instrRes] = await Promise.all([
    +          fetch(`${API_BASE}/projects/${projectId}/build/summary`, { headers: headers() }),
    +          fetch(`${API_BASE}/projects/${projectId}/build/instructions`, { headers: headers() }),
    +        ]);
    +        if (sumRes.ok) setSummary(await sumRes.json());
    +        if (instrRes.ok) setInstructions(await instrRes.json());
    +      } catch (e) {
    +        setError(e instanceof Error ? e.message : 'Failed to load');
    +      } finally {
    +        setLoading(false);
    +      }
    +    };
    +    load();
    +  }, [token, projectId, headers]);
    +
    +  const formatDuration = (seconds: number): string => {
    +    const h = Math.floor(seconds / 3600);
    +    const m = Math.floor((seconds % 3600) / 60);
    +    const s = Math.floor(seconds % 60);
    +    if (h > 0) return `${h}h ${m}m ${s}s`;
    +    if (m > 0) return `${m}m ${s}s`;
    +    return `${s}s`;
    +  };
    +
    +  const formatTokens = (n: number): string => {
    +    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    +    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    +    return String(n);
    +  };
    +
    +  if (loading) {
    +    return (
    +      <div style={{ padding: 32 }} data-testid="build-complete-skeleton">
    +        <Skeleton width="40%" height={32} />
    +        <Skeleton width="100%" height={120} />
    +        <Skeleton width="100%" height={200} />
    +      </div>
    +    );
    +  }
    +
    +  if (error) {
    +    return (
    +      <div style={{ padding: 32, color: '#EF4444' }} data-testid="build-complete-error">
    +        <h2>Error</h2>
    +        <p>{error}</p>
    +      </div>
    +    );
    +  }
    +
    +  const buildStatus = summary?.build.status ?? 'unknown';
    +  const isSuccess = buildStatus === 'completed';
    +  const isFailed = buildStatus === 'failed';
    +
    +  return (
    +    <div style={{ padding: 32, maxWidth: 900, margin: '0 auto' }} data-testid="build-complete">
    +      {/* Header */}
    +      <div style={{ marginBottom: 24 }}>
    +        <h1 style={{ color: '#F8FAFC', margin: 0, fontSize: 24 }}>
    +          {isSuccess ? 'Build Complete' : isFailed ? 'Build Failed' : 'Build Summary'}
    +        </h1>
    +        <p style={{ color: '#94A3B8', margin: '4px 0 0' }}>
    +          {instructions?.project_name ?? `Project ${projectId}`}
    +        </p>
    +      </div>
    +
    +      {/* Status Banner */}
    +      <div
    +        data-testid="build-status-banner"
    +        style={{
    +          padding: 16,
    +          borderRadius: 8,
    +          marginBottom: 24,
    +          background: isSuccess ? '#166534' : isFailed ? '#7F1D1D' : '#1E3A5F',
    +          border: `1px solid ${isSuccess ? '#22C55E' : isFailed ? '#EF4444' : '#3B82F6'}`,
    +        }}
    +      >
    +        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
    +          <span style={{ color: '#F8FAFC', fontWeight: 600 }}>
    +            {isSuccess ? 'All phases passed' : isFailed ? summary?.build.error_detail ?? 'Build failed' : `Status: ${buildStatus}`}
    +          </span>
    +          <span style={{
    +            padding: '2px 10px',
    +            borderRadius: 12,
    +            fontSize: 13,
    +            fontWeight: 600,
    +            background: isSuccess ? '#22C55E' : isFailed ? '#EF4444' : '#3B82F6',
    +            color: '#FFF',
    +          }}>
    +            {buildStatus.toUpperCase()}
    +          </span>
    +        </div>
    +      </div>
    +
    +      {/* Build Summary Cards */}
    +      {summary && (
    +        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 24 }}>
    +          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-phase">
    +            <div style={{ color: '#94A3B8', fontSize: 13 }}>Final Phase</div>
    +            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>{summary.build.phase}</div>
    +          </div>
    +          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-time">
    +            <div style={{ color: '#94A3B8', fontSize: 13 }}>Total Time</div>
    +            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>
    +              {summary.elapsed_seconds != null ? formatDuration(summary.elapsed_seconds) : 'ÔÇö'}
    +            </div>
    +          </div>
    +          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-loops">
    +            <div style={{ color: '#94A3B8', fontSize: 13 }}>Loopbacks</div>
    +            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>{summary.loop_count}</div>
    +          </div>
    +          <div style={{ background: '#1E293B', borderRadius: 8, padding: 16 }} data-testid="summary-cost">
    +            <div style={{ color: '#94A3B8', fontSize: 13 }}>Estimated Cost</div>
    +            <div style={{ color: '#F8FAFC', fontSize: 20, fontWeight: 600 }}>
    +              ${summary.cost.total_cost_usd.toFixed(2)}
    +            </div>
    +          </div>
    +        </div>
    +      )}
    +
    +      {/* Token Usage */}
    +      {summary && summary.cost.phases.length > 0 && (
    +        <div style={{ background: '#1E293B', borderRadius: 8, padding: 20, marginBottom: 24 }} data-testid="cost-breakdown">
    +          <h3 style={{ color: '#F8FAFC', margin: '0 0 12px' }}>Token Usage by Phase</h3>
    +          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #334155', color: '#94A3B8', fontSize: 13 }}>
    +            <span style={{ flex: 2 }}>Phase</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>Input</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>Output</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>Cost</span>
    +          </div>
    +          {summary.cost.phases.map((entry, i) => (
    +            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid #1E293B', color: '#F8FAFC', fontSize: 14 }}>
    +              <span style={{ flex: 2 }}>{entry.phase}</span>
    +              <span style={{ flex: 1, textAlign: 'right', color: '#94A3B8' }}>{formatTokens(entry.input_tokens)}</span>
    +              <span style={{ flex: 1, textAlign: 'right', color: '#94A3B8' }}>{formatTokens(entry.output_tokens)}</span>
    +              <span style={{ flex: 1, textAlign: 'right' }}>${entry.estimated_cost_usd.toFixed(4)}</span>
    +            </div>
    +          ))}
    +          <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 0 0', color: '#F8FAFC', fontWeight: 600, fontSize: 14 }}>
    +            <span style={{ flex: 2 }}>Total</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>{formatTokens(summary.cost.total_input_tokens)}</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>{formatTokens(summary.cost.total_output_tokens)}</span>
    +            <span style={{ flex: 1, textAlign: 'right' }}>${summary.cost.total_cost_usd.toFixed(4)}</span>
    +          </div>
    +        </div>
    +      )}
    +
    +      {/* Deployment Instructions */}
    +      {instructions && (
    +        <div style={{ background: '#1E293B', borderRadius: 8, padding: 20, marginBottom: 24 }} data-testid="deploy-instructions">
    +          <h3 style={{ color: '#F8FAFC', margin: '0 0 12px' }}>Deployment Instructions</h3>
    +          <pre style={{
    +            background: '#0F172A',
    +            padding: 16,
    +            borderRadius: 6,
    +            color: '#E2E8F0',
    +            fontSize: 13,
    +            lineHeight: 1.6,
    +            whiteSpace: 'pre-wrap',
    +            wordBreak: 'break-word',
    +            maxHeight: 400,
    +            overflowY: 'auto',
    +            margin: 0,
    +          }}>
    +            {instructions.instructions}
    +          </pre>
    +        </div>
    +      )}
    +
    +      {/* Actions */}
    +      <div style={{ display: 'flex', gap: 12 }}>
    +        <button
    +          onClick={() => navigate(`/projects/${projectId}`)}
    +          style={{
    +            padding: '10px 20px',
    +            borderRadius: 6,
    +            border: '1px solid #334155',
    +            background: '#1E293B',
    +            color: '#F8FAFC',
    +            cursor: 'pointer',
    +            fontWeight: 500,
    +          }}
    +        >
    +          Back to Project
    +        </button>
    +        <button
    +          onClick={() => navigate(`/projects/${projectId}/build`)}
    +          style={{
    +            padding: '10px 20px',
    +            borderRadius: 6,
    +            border: 'none',
    +            background: '#2563EB',
    +            color: '#FFF',
    +            cursor: 'pointer',
    +            fontWeight: 500,
    +          }}
    +        >
    +          View Build Logs
    +        </button>
    +      </div>
    +    </div>
    +  );
    +}
    diff --git a/web/src/pages/BuildProgress.tsx b/web/src/pages/BuildProgress.tsx
    index 9d7639d..a4f1808 100644
    --- a/web/src/pages/BuildProgress.tsx
    +++ b/web/src/pages/BuildProgress.tsx
    @@ -144,6 +144,7 @@ function BuildProgress() {
                 setBuild(payload.build as BuildStatus);
                 if (data.type === 'build_complete') {
                   addToast('Build completed successfully!', 'success');
    +              navigate(`/projects/${projectId}/build/complete`);
                 } else if (data.type === 'build_error') {
                   addToast('Build failed: ' + (payload.error ?? 'Unknown error'));
                 }

