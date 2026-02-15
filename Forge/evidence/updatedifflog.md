# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T02:55:51+00:00
- Branch: master
- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
- BASE_HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- Phase 8: Project Intake and Questionnaire
- DB migration 002_projects.sql (projects + project_contracts tables)
- project_repo.py with CRUD for both tables
- llm_client.py Anthropic Messages API wrapper
- project_service.py questionnaire chat + contract generation
- projects router with 9 endpoints
- 10 contract templates
- A7 fix: scan only Verification section in run_audit.ps1 and runner.py
- 42 new tests (154 total)

## Files Changed (staged)
- Forge/Contracts/physics.yaml
- Forge/Contracts/schema.md
- Forge/evidence/audit_ledger.md
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md
- Forge/scripts/run_audit.ps1
- app/api/routers/projects.py
- app/audit/runner.py
- app/clients/llm_client.py
- app/config.py
- app/main.py
- app/repos/project_repo.py
- app/services/project_service.py
- app/templates/contracts/blueprint.md
- app/templates/contracts/boundaries.json
- app/templates/contracts/builder_contract.md
- app/templates/contracts/builder_directive.md
- app/templates/contracts/manifesto.md
- app/templates/contracts/phases.md
- app/templates/contracts/physics.yaml
- app/templates/contracts/schema.md
- app/templates/contracts/stack.md
- app/templates/contracts/ui.md
- db/migrations/002_projects.sql
- tests/test_audit_runner.py
- tests/test_llm_client.py
- tests/test_project_service.py
- tests/test_projects_router.py

## git status -sb
    ## master...origin/master
    M  Forge/Contracts/physics.yaml
    M  Forge/Contracts/schema.md
    M  Forge/evidence/audit_ledger.md
    M  Forge/evidence/test_runs.md
    M  Forge/evidence/test_runs_latest.md
     M Forge/evidence/updatedifflog.md
    M  Forge/scripts/run_audit.ps1
    A  app/api/routers/projects.py
    M  app/audit/runner.py
    A  app/clients/llm_client.py
    M  app/config.py
    M  app/main.py
    A  app/repos/project_repo.py
    A  app/services/project_service.py
    A  app/templates/contracts/blueprint.md
    A  app/templates/contracts/boundaries.json
    A  app/templates/contracts/builder_contract.md
    A  app/templates/contracts/builder_directive.md
    A  app/templates/contracts/manifesto.md
    A  app/templates/contracts/phases.md
    A  app/templates/contracts/physics.yaml
    A  app/templates/contracts/schema.md
    A  app/templates/contracts/stack.md
    A  app/templates/contracts/ui.md
    A  db/migrations/002_projects.sql
    M  tests/test_audit_runner.py
    A  tests/test_llm_client.py
    A  tests/test_project_service.py
    A  tests/test_projects_router.py

## Verification
- Static: PASS -- compileall clean
- Runtime: PASS -- app boots with projects router
- Behavior: PASS -- 154 tests pass (pytest)
- Contract: PASS -- boundary compliance intact

## Notes (optional)
- None

## Next Steps
- Phase 9: Build Orchestrator

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index d0d584a..2010175 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -162,6 +162,105 @@ paths:
             type: "audit_update"
             payload: AuditRunSummary
     
    +  # -- Projects (Phase 8) -------------------------------------------
    +
    +  /projects:
    +    post:
    +      summary: "Create a new project"
    +      auth: bearer
    +      request:
    +        name: string (required, min 1, max 255)
    +        description: string (optional)
    +      response:
    +        id: uuid
    +        name: string
    +        description: string | null
    +        status: string
    +        created_at: datetime
    +
    +    get:
    +      summary: "List user's projects"
    +      auth: bearer
    +      response:
    +        items: ProjectSummary[]
    +
    +  /projects/{project_id}:
    +    get:
    +      summary: "Get project detail with contract status"
    +      auth: bearer
    +      response:
    +        id: uuid
    +        name: string
    +        description: string | null
    +        status: string
    +        repo_id: uuid | null
    +        questionnaire_progress: QuestionnaireProgress
    +        contracts: ContractSummary[]
    +        created_at: datetime
    +        updated_at: datetime
    +
    +  /projects/{project_id}/questionnaire:
    +    post:
    +      summary: "Send a message to the questionnaire chat"
    +      auth: bearer
    +      request:
    +        message: string (required, min 1)
    +      response:
    +        reply: string
    +        section: string
    +        completed_sections: string[]
    +        remaining_sections: string[]
    +        is_complete: boolean
    +
    +  /projects/{project_id}/questionnaire/state:
    +    get:
    +      summary: "Current questionnaire progress"
    +      auth: bearer
    +      response:
    +        current_section: string | null
    +        completed_sections: string[]
    +        remaining_sections: string[]
    +        is_complete: boolean
    +
    +  /projects/{project_id}/contracts/generate:
    +    post:
    +      summary: "Generate all contract files from completed questionnaire answers"
    +      auth: bearer
    +      response:
    +        contracts: ContractSummary[]
    +
    +  /projects/{project_id}/contracts:
    +    get:
    +      summary: "List generated contracts"
    +      auth: bearer
    +      response:
    +        items: ContractSummary[]
    +
    +  /projects/{project_id}/contracts/{contract_type}:
    +    get:
    +      summary: "View a single contract"
    +      auth: bearer
    +      response:
    +        id: uuid
    +        project_id: uuid
    +        contract_type: string
    +        content: string
    +        version: integer
    +        created_at: datetime
    +        updated_at: datetime
    +
    +    put:
    +      summary: "Edit a contract before build"
    +      auth: bearer
    +      request:
    +        content: string (required)
    +      response:
    +        id: uuid
    +        contract_type: string
    +        content: string
    +        version: integer
    +        updated_at: datetime
    +
     # -- Schemas --------------------------------------------------------
     
     schemas:
    @@ -223,3 +322,25 @@ schemas:
         name: string
         result: string
         detail: string | null
    +
    +  ProjectSummary:
    +    id: uuid
    +    name: string
    +    description: string | null
    +    status: string
    +    created_at: datetime
    +    updated_at: datetime
    +
    +  QuestionnaireProgress:
    +    current_section: string | null
    +    completed_sections: string[]
    +    remaining_sections: string[]
    +    is_complete: boolean
    +
    +  ContractSummary:
    +    id: uuid
    +    project_id: uuid
    +    contract_type: string
    +    version: integer
    +    created_at: datetime
    +    updated_at: datetime
    diff --git a/Forge/Contracts/schema.md b/Forge/Contracts/schema.md
    index 310b60c..637c7b7 100644
    --- a/Forge/Contracts/schema.md
    +++ b/Forge/Contracts/schema.md
    @@ -122,6 +122,57 @@ CREATE INDEX idx_audit_checks_audit_run_id ON audit_checks(audit_run_id);
     
     ---
     
    +### projects
    +
    +Stores user projects created via the intake questionnaire.
    +
    +```sql
    +CREATE TABLE projects (
    +    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    +    name                  VARCHAR(255) NOT NULL,
    +    description           TEXT,
    +    status                VARCHAR(20) NOT NULL DEFAULT 'draft',
    +    repo_id               UUID REFERENCES repos(id) ON DELETE SET NULL,
    +    questionnaire_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
    +    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    +    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +```
    +
    +`status` values: `draft`, `questionnaire`, `contracts_ready`, `building`, `completed`
    +
    +```sql
    +CREATE INDEX idx_projects_user_id ON projects(user_id);
    +```
    +
    +---
    +
    +### project_contracts
    +
    +Stores generated contract files for a project.
    +
    +```sql
    +CREATE TABLE project_contracts (
    +    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    +    contract_type   VARCHAR(50) NOT NULL,
    +    content         TEXT NOT NULL,
    +    version         INTEGER NOT NULL DEFAULT 1,
    +    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    +    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +```
    +
    +`contract_type` values: `blueprint`, `manifesto`, `stack`, `schema`, `physics`, `boundaries`, `phases`, `ui`, `builder_contract`, `builder_directive`
    +
    +```sql
    +CREATE UNIQUE INDEX idx_project_contracts_project_type ON project_contracts(project_id, contract_type);
    +CREATE INDEX idx_project_contracts_project_id ON project_contracts(project_id);
    +```
    +
    +---
    +
     ## Schema -> Phase Traceability
     
     | Table | Repo Created In | Wired To Caller In | Notes |
    @@ -130,6 +181,8 @@ CREATE INDEX idx_audit_checks_audit_run_id ON audit_checks(audit_run_id);
     | repos | Phase 2 | Phase 2 | Connect-repo flow creates repo records |
     | audit_runs | Phase 3 | Phase 3 | Webhook handler creates audit runs |
     | audit_checks | Phase 3 | Phase 3 | Audit engine writes check results |
    +| projects | Phase 8 | Phase 8 | Project intake & questionnaire |
    +| project_contracts | Phase 8 | Phase 8 | Generated contract files |
     
     ---
     
    @@ -140,4 +193,5 @@ The builder creates migration files in `db/migrations/` during Phase 0.
     ```
     db/migrations/
       001_initial_schema.sql
    +  002_projects.sql
     ```
    diff --git a/Forge/evidence/audit_ledger.md b/Forge/evidence/audit_ledger.md
    index 7df0cb1..634e3e2 100644
    --- a/Forge/evidence/audit_ledger.md
    +++ b/Forge/evidence/audit_ledger.md
    @@ -2132,3 +2132,161 @@ Timestamp: 2026-02-15T02:37:10Z
     AEM Cycle: Phase 7 -- Python Audit Runner
     Outcome: AUTO-AUTHORIZED (committed)
     Note: Auto-authorize enabled per directive. Audit iteration 45 passed all checks (A1-A9). Proceeding to commit and push.
    +
    +---
    +## Audit Entry: Phase 8 -- Project Intake and Questionnaire,DB migration 002_projects.sql (projects + project_contracts tables),project_repo.py with CRUD for both tables,llm_client.py Anthropic Messages API wrapper,project_service.py questionnaire chat + contract generation,projects router with 9 endpoints,10 contract templates,42 new tests (154 total) -- Iteration 46
    +Timestamp: 2026-02-15T02:50:52Z
    +AEM Cycle: Phase 8 -- Project Intake and Questionnaire,DB migration 002_projects.sql (projects + project_contracts tables),project_repo.py with CRUD for both tables,llm_client.py Anthropic Messages API wrapper,project_service.py questionnaire chat + contract generation,projects router with 9 endpoints,10 contract templates,42 new tests (154 total)
    +Outcome: FAIL
    +
    +### Checklist
    +- A1 Scope compliance:      FAIL -- Unclaimed in diff: Forge/evidence/updatedifflog.md. 
    +- A2 Minimal-diff:          PASS -- No renames; diff is minimal.
    +- A3 Evidence completeness: PASS -- test_runs_latest.md=PASS, updatedifflog.md present.
    +- A4 Boundary compliance:   PASS -- No forbidden patterns found in any boundary layer.
    +- A5 Diff Log Gate:         PASS -- No TODO: placeholders in updatedifflog.md.
    +- A6 Authorization Gate:    PASS -- No prior AUTHORIZED entry; first AEM cycle.
    +- A7 Verification order:    FAIL -- Verification keywords are out of order.
    +- A8 Test gate:             PASS -- test_runs_latest.md reports PASS.
    +- A9 Dependency gate:       PASS -- All imports in changed files have declared dependencies.
    +
    +### Fix Plan (FAIL items)
    +- A1: FAIL -- Unclaimed in diff: Forge/evidence/updatedifflog.md. 
    +- A7: FAIL -- Verification keywords are out of order.
    +
    +### Files Changed
    +- app/api/routers/projects.py
    +- app/clients/llm_client.py
    +- app/config.py
    +- app/main.py
    +- app/repos/project_repo.py
    +- app/services/project_service.py
    +- app/templates/contracts/blueprint.md
    +- app/templates/contracts/boundaries.json
    +- app/templates/contracts/builder_contract.md
    +- app/templates/contracts/builder_directive.md
    +- app/templates/contracts/manifesto.md
    +- app/templates/contracts/phases.md
    +- app/templates/contracts/physics.yaml
    +- app/templates/contracts/schema.md
    +- app/templates/contracts/stack.md
    +- app/templates/contracts/ui.md
    +- db/migrations/002_projects.sql
    +- Forge/Contracts/physics.yaml
    +- Forge/Contracts/schema.md
    +- Forge/evidence/test_runs_latest.md
    +- Forge/evidence/test_runs.md
    +- tests/test_llm_client.py
    +- tests/test_project_service.py
    +- tests/test_projects_router.py
    +
    +### Notes
    +W1: WARN -- Potential secrets found: sk-, AKIA, -----BEGIN, password=, secret=, token=
    +W2: PASS -- audit_ledger.md exists and is non-empty.
    +W3: PASS -- All physics paths have corresponding handler files.
    +
    +---
    +## Audit Entry: Phase 8 -- Project Intake and Questionnaire -- Iteration 47
    +Timestamp: 2026-02-15T02:51:04Z
    +AEM Cycle: Phase 8 -- Project Intake and Questionnaire
    +Outcome: FAIL
    +
    +### Checklist
    +- A1 Scope compliance:      FAIL -- Unclaimed in diff: Forge/evidence/audit_ledger.md. 
    +- A2 Minimal-diff:          PASS -- No renames; diff is minimal.
    +- A3 Evidence completeness: PASS -- test_runs_latest.md=PASS, updatedifflog.md present.
    +- A4 Boundary compliance:   PASS -- No forbidden patterns found in any boundary layer.
    +- A5 Diff Log Gate:         PASS -- No TODO: placeholders in updatedifflog.md.
    +- A6 Authorization Gate:    PASS -- No prior AUTHORIZED entry; first AEM cycle.
    +- A7 Verification order:    FAIL -- Verification keywords are out of order.
    +- A8 Test gate:             PASS -- test_runs_latest.md reports PASS.
    +- A9 Dependency gate:       PASS -- All imports in changed files have declared dependencies.
    +
    +### Fix Plan (FAIL items)
    +- A1: FAIL -- Unclaimed in diff: Forge/evidence/audit_ledger.md. 
    +- A7: FAIL -- Verification keywords are out of order.
    +
    +### Files Changed
    +- app/api/routers/projects.py
    +- app/clients/llm_client.py
    +- app/config.py
    +- app/main.py
    +- app/repos/project_repo.py
    +- app/services/project_service.py
    +- app/templates/contracts/blueprint.md
    +- app/templates/contracts/boundaries.json
    +- app/templates/contracts/builder_contract.md
    +- app/templates/contracts/builder_directive.md
    +- app/templates/contracts/manifesto.md
    +- app/templates/contracts/phases.md
    +- app/templates/contracts/physics.yaml
    +- app/templates/contracts/schema.md
    +- app/templates/contracts/stack.md
    +- app/templates/contracts/ui.md
    +- db/migrations/002_projects.sql
    +- Forge/Contracts/physics.yaml
    +- Forge/Contracts/schema.md
    +- Forge/evidence/test_runs_latest.md
    +- Forge/evidence/test_runs.md
    +- Forge/evidence/updatedifflog.md
    +- tests/test_llm_client.py
    +- tests/test_project_service.py
    +- tests/test_projects_router.py
    +
    +### Notes
    +W1: WARN -- Potential secrets found: sk-, AKIA, -----BEGIN, password=, secret=, token=
    +W2: PASS -- audit_ledger.md exists and is non-empty.
    +W3: PASS -- All physics paths have corresponding handler files.
    +
    +---
    +## Audit Entry: Phase 8 -- Project Intake and Questionnaire -- Iteration 48
    +Timestamp: 2026-02-15T02:51:15Z
    +AEM Cycle: Phase 8 -- Project Intake and Questionnaire
    +Outcome: FAIL
    +
    +### Checklist
    +- A1 Scope compliance:      FAIL -- Unclaimed in diff: Forge/evidence/audit_ledger.md. 
    +- A2 Minimal-diff:          PASS -- No renames; diff is minimal.
    +- A3 Evidence completeness: PASS -- test_runs_latest.md=PASS, updatedifflog.md present.
    +- A4 Boundary compliance:   PASS -- No forbidden patterns found in any boundary layer.
    +- A5 Diff Log Gate:         PASS -- No TODO: placeholders in updatedifflog.md.
    +- A6 Authorization Gate:    PASS -- No prior AUTHORIZED entry; first AEM cycle.
    +- A7 Verification order:    FAIL -- Verification keywords are out of order.
    +- A8 Test gate:             PASS -- test_runs_latest.md reports PASS.
    +- A9 Dependency gate:       PASS -- All imports in changed files have declared dependencies.
    +
    +### Fix Plan (FAIL items)
    +- A1: FAIL -- Unclaimed in diff: Forge/evidence/audit_ledger.md. 
    +- A7: FAIL -- Verification keywords are out of order.
    +
    +### Files Changed
    +- app/api/routers/projects.py
    +- app/clients/llm_client.py
    +- app/config.py
    +- app/main.py
    +- app/repos/project_repo.py
    +- app/services/project_service.py
    +- app/templates/contracts/blueprint.md
    +- app/templates/contracts/boundaries.json
    +- app/templates/contracts/builder_contract.md
    +- app/templates/contracts/builder_directive.md
    +- app/templates/contracts/manifesto.md
    +- app/templates/contracts/phases.md
    +- app/templates/contracts/physics.yaml
    +- app/templates/contracts/schema.md
    +- app/templates/contracts/stack.md
    +- app/templates/contracts/ui.md
    +- db/migrations/002_projects.sql
    +- Forge/Contracts/physics.yaml
    +- Forge/Contracts/schema.md
    +- Forge/evidence/test_runs_latest.md
    +- Forge/evidence/test_runs.md
    +- Forge/evidence/updatedifflog.md
    +- tests/test_llm_client.py
    +- tests/test_project_service.py
    +- tests/test_projects_router.py
    +
    +### Notes
    +W1: WARN -- Potential secrets found: sk-, AKIA, -----BEGIN, password=, secret=, token=
    +W2: PASS -- audit_ledger.md exists and is non-empty.
    +W3: PASS -- All physics paths have corresponding handler files.
    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    index d552ad3..f4d00d1 100644
    --- a/Forge/evidence/test_runs.md
    +++ b/Forge/evidence/test_runs.md
    @@ -642,3 +642,137 @@ M  tests/test_audit_runner.py
     
     ```
     
    +## Test Run 2026-02-15T02:50:18Z
    +- Status: PASS
    +- Start: 2026-02-15T02:50:18Z
    +- End: 2026-02-15T02:50:31Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
    +- pytest exit: 0
    +- import_sanity exit: 0
    +- compileall exit: 0
    +- git status -sb:
    +```
    +## master...origin/master
    + M Forge/Contracts/physics.yaml
    + M Forge/Contracts/schema.md
    + M app/config.py
    + M app/main.py
    +?? app/api/routers/projects.py
    +?? app/clients/llm_client.py
    +?? app/repos/project_repo.py
    +?? app/services/project_service.py
    +?? app/templates/
    +?? db/migrations/002_projects.sql
    +?? tests/test_llm_client.py
    +?? tests/test_project_service.py
    +?? tests/test_projects_router.py
    +```
    +- git diff --stat:
    +```
    + Forge/Contracts/physics.yaml | 121 +++++++++++++++++++++++++++++++++++++++++++
    + Forge/Contracts/schema.md    |  54 +++++++++++++++++++
    + app/config.py                |   4 ++
    + app/main.py                  |   4 +-
    + 4 files changed, 182 insertions(+), 1 deletion(-)
    +```
    +
    +## Test Run 2026-02-15T02:55:09Z
    +- Status: PASS
    +- Start: 2026-02-15T02:55:09Z
    +- End: 2026-02-15T02:55:23Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- pytest exit: 0
    +- git status -sb:
    +```
    +## master...origin/master
    +M  Forge/Contracts/physics.yaml
    +M  Forge/Contracts/schema.md
    +M  Forge/evidence/audit_ledger.md
    +M  Forge/evidence/test_runs.md
    +M  Forge/evidence/test_runs_latest.md
    +M  Forge/evidence/updatedifflog.md
    +M  Forge/scripts/run_audit.ps1
    +A  app/api/routers/projects.py
    +M  app/audit/runner.py
    +A  app/clients/llm_client.py
    +M  app/config.py
    +M  app/main.py
    +A  app/repos/project_repo.py
    +A  app/services/project_service.py
    +A  app/templates/contracts/blueprint.md
    +A  app/templates/contracts/boundaries.json
    +A  app/templates/contracts/builder_contract.md
    +A  app/templates/contracts/builder_directive.md
    +A  app/templates/contracts/manifesto.md
    +A  app/templates/contracts/phases.md
    +A  app/templates/contracts/physics.yaml
    +A  app/templates/contracts/schema.md
    +A  app/templates/contracts/stack.md
    +A  app/templates/contracts/ui.md
    +A  db/migrations/002_projects.sql
    +M  tests/test_audit_runner.py
    +A  tests/test_llm_client.py
    +A  tests/test_project_service.py
    +A  tests/test_projects_router.py
    +```
    +- git diff --stat:
    +```
    +
    +```
    +
    +## Test Run 2026-02-15T02:55:23Z
    +- Status: PASS
    +- Start: 2026-02-15T02:55:23Z
    +- End: 2026-02-15T02:55:37Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
    +- import_sanity exit: 0
    +- pytest exit: 0
    +- compileall exit: 0
    +- git status -sb:
    +```
    +## master...origin/master
    +M  Forge/Contracts/physics.yaml
    +M  Forge/Contracts/schema.md
    +M  Forge/evidence/audit_ledger.md
    +MM Forge/evidence/test_runs.md
    +MM Forge/evidence/test_runs_latest.md
    +M  Forge/evidence/updatedifflog.md
    +M  Forge/scripts/run_audit.ps1
    +A  app/api/routers/projects.py
    +M  app/audit/runner.py
    +A  app/clients/llm_client.py
    +M  app/config.py
    +M  app/main.py
    +A  app/repos/project_repo.py
    +A  app/services/project_service.py
    +A  app/templates/contracts/blueprint.md
    +A  app/templates/contracts/boundaries.json
    +A  app/templates/contracts/builder_contract.md
    +A  app/templates/contracts/builder_directive.md
    +A  app/templates/contracts/manifesto.md
    +A  app/templates/contracts/phases.md
    +A  app/templates/contracts/physics.yaml
    +A  app/templates/contracts/schema.md
    +A  app/templates/contracts/stack.md
    +A  app/templates/contracts/ui.md
    +A  db/migrations/002_projects.sql
    +M  tests/test_audit_runner.py
    +A  tests/test_llm_client.py
    +A  tests/test_project_service.py
    +A  tests/test_projects_router.py
    +```
    +- git diff --stat:
    +```
    + Forge/evidence/test_runs.md        | 48 ++++++++++++++++++++++++++++++++
    + Forge/evidence/test_runs_latest.md | 56 +++++++++++++++++++++++---------------
    + 2 files changed, 82 insertions(+), 22 deletions(-)
    +```
    +
    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    index c118d19..c12400f 100644
    --- a/Forge/evidence/test_runs_latest.md
    +++ b/Forge/evidence/test_runs_latest.md
    @@ -1,26 +1,49 @@
     Status: PASS
    -Start: 2026-02-15T02:28:50Z
    -End: 2026-02-15T02:29:04Z
    +Start: 2026-02-15T02:55:23Z
    +End: 2026-02-15T02:55:37Z
     Branch: master
    -HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
    +HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    -compileall exit: 0
    -pytest exit: 0
     import_sanity exit: 0
    +pytest exit: 0
    +compileall exit: 0
     git status -sb:
     ```
     ## master...origin/master
    +M  Forge/Contracts/physics.yaml
    +M  Forge/Contracts/schema.md
     M  Forge/evidence/audit_ledger.md
    -M  Forge/evidence/test_runs.md
    -M  Forge/evidence/test_runs_latest.md
    +MM Forge/evidence/test_runs.md
    +MM Forge/evidence/test_runs_latest.md
     M  Forge/evidence/updatedifflog.md
    -M  Forge/scripts/overwrite_diff_log.ps1
     M  Forge/scripts/run_audit.ps1
    +A  app/api/routers/projects.py
     M  app/audit/runner.py
    +A  app/clients/llm_client.py
    +M  app/config.py
    +M  app/main.py
    +A  app/repos/project_repo.py
    +A  app/services/project_service.py
    +A  app/templates/contracts/blueprint.md
    +A  app/templates/contracts/boundaries.json
    +A  app/templates/contracts/builder_contract.md
    +A  app/templates/contracts/builder_directive.md
    +A  app/templates/contracts/manifesto.md
    +A  app/templates/contracts/phases.md
    +A  app/templates/contracts/physics.yaml
    +A  app/templates/contracts/schema.md
    +A  app/templates/contracts/stack.md
    +A  app/templates/contracts/ui.md
    +A  db/migrations/002_projects.sql
     M  tests/test_audit_runner.py
    +A  tests/test_llm_client.py
    +A  tests/test_project_service.py
    +A  tests/test_projects_router.py
     ```
     git diff --stat:
     ```
    -
    + Forge/evidence/test_runs.md        | 48 ++++++++++++++++++++++++++++++++
    + Forge/evidence/test_runs_latest.md | 56 +++++++++++++++++++++++---------------
    + 2 files changed, 82 insertions(+), 22 deletions(-)
     ```
     
    diff --git a/Forge/scripts/run_audit.ps1 b/Forge/scripts/run_audit.ps1
    index 02e5232..369350b 100644
    --- a/Forge/scripts/run_audit.ps1
    +++ b/Forge/scripts/run_audit.ps1
    @@ -275,7 +275,17 @@ try {
           $results["A7"] = "FAIL -- updatedifflog.md missing; cannot verify order."
           $anyFail = $true
         } else {
    -      $dlText = Get-Content $diffLog -Raw
    +      $dlRaw = Get-Content $diffLog -Raw
    +      # Only scan the ## Verification section so that keywords appearing
    +      # in file names, table names, or diff hunks don't cause false positives.
    +      $verIdx = $dlRaw.IndexOf('## Verification')
    +      if ($verIdx -lt 0) {
    +        $results["A7"] = "FAIL -- No ## Verification section found in updatedifflog.md."
    +        $anyFail = $true
    +      } else {
    +      $verRest = $dlRaw.Substring($verIdx + '## Verification'.Length)
    +      $nextHeading = $verRest.IndexOf("`n## ")
    +      $dlText = if ($nextHeading -ge 0) { $verRest.Substring(0, $nextHeading) } else { $verRest }
           $keywords = @("Static", "Runtime", "Behavior", "Contract")
           $positions = @()
           $missing = @()
    @@ -307,6 +317,7 @@ try {
               $anyFail = $true
             }
           }
    +      } # close $verIdx else
         }
       } catch {
         $results["A7"] = "FAIL -- Error checking verification order: $_"
    diff --git a/app/api/routers/projects.py b/app/api/routers/projects.py
    new file mode 100644
    index 0000000..efe9c48
    --- /dev/null
    +++ b/app/api/routers/projects.py
    @@ -0,0 +1,267 @@
    +"""Projects router -- project CRUD, questionnaire chat, contract management."""
    +
    +import logging
    +from uuid import UUID
    +
    +from fastapi import APIRouter, Depends, HTTPException, status
    +from pydantic import BaseModel, Field
    +
    +from app.api.deps import get_current_user
    +from app.services.project_service import (
    +    create_new_project,
    +    delete_user_project,
    +    generate_contracts,
    +    get_contract,
    +    get_project_detail,
    +    get_questionnaire_state,
    +    list_contracts,
    +    list_user_projects,
    +    process_questionnaire_message,
    +    update_contract,
    +)
    +
    +logger = logging.getLogger(__name__)
    +
    +router = APIRouter(prefix="/projects", tags=["projects"])
    +
    +
    +# ---------------------------------------------------------------------------
    +# Request models
    +# ---------------------------------------------------------------------------
    +
    +
    +class CreateProjectRequest(BaseModel):
    +    """Request body for creating a project."""
    +
    +    name: str = Field(..., min_length=1, max_length=255, description="Project name")
    +    description: str | None = Field(
    +        None, max_length=2000, description="Project description"
    +    )
    +
    +
    +class QuestionnaireMessageRequest(BaseModel):
    +    """Request body for sending a questionnaire message."""
    +
    +    message: str = Field(..., min_length=1, description="User message")
    +
    +
    +class UpdateContractRequest(BaseModel):
    +    """Request body for updating a contract."""
    +
    +    content: str = Field(..., min_length=1, description="Updated contract content")
    +
    +
    +# ---------------------------------------------------------------------------
    +# Project CRUD
    +# ---------------------------------------------------------------------------
    +
    +
    +@router.post("")
    +async def create_project(
    +    body: CreateProjectRequest,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Create a new project."""
    +    project = await create_new_project(
    +        user_id=current_user["id"],
    +        name=body.name,
    +        description=body.description,
    +    )
    +    return {
    +        "id": str(project["id"]),
    +        "name": project["name"],
    +        "description": project["description"],
    +        "status": project["status"],
    +        "created_at": project["created_at"],
    +    }
    +
    +
    +@router.get("")
    +async def list_projects(
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """List user's projects."""
    +    projects = await list_user_projects(current_user["id"])
    +    return {
    +        "items": [
    +            {
    +                "id": str(p["id"]),
    +                "name": p["name"],
    +                "description": p["description"],
    +                "status": p["status"],
    +                "created_at": p["created_at"],
    +                "updated_at": p["updated_at"],
    +            }
    +            for p in projects
    +        ]
    +    }
    +
    +
    +@router.get("/{project_id}")
    +async def get_project(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Get project detail with contract status."""
    +    try:
    +        return await get_project_detail(current_user["id"], project_id)
    +    except ValueError as exc:
    +        raise HTTPException(
    +            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
    +        )
    +
    +
    +@router.delete("/{project_id}")
    +async def remove_project(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Delete a project."""
    +    try:
    +        await delete_user_project(current_user["id"], project_id)
    +    except ValueError as exc:
    +        raise HTTPException(
    +            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
    +        )
    +    return {"status": "deleted"}
    +
    +
    +# ---------------------------------------------------------------------------
    +# Questionnaire
    +# ---------------------------------------------------------------------------
    +
    +
    +@router.post("/{project_id}/questionnaire")
    +async def questionnaire_message(
    +    project_id: UUID,
    +    body: QuestionnaireMessageRequest,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Send a message to the questionnaire chat."""
    +    try:
    +        return await process_questionnaire_message(
    +            user_id=current_user["id"],
    +            project_id=project_id,
    +            message=body.message,
    +        )
    +    except ValueError as exc:
    +        detail = str(exc)
    +        code = (
    +            status.HTTP_404_NOT_FOUND
    +            if "not found" in detail.lower()
    +            else status.HTTP_400_BAD_REQUEST
    +        )
    +        raise HTTPException(status_code=code, detail=detail)
    +
    +
    +@router.get("/{project_id}/questionnaire/state")
    +async def questionnaire_progress(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Current questionnaire progress."""
    +    try:
    +        return await get_questionnaire_state(current_user["id"], project_id)
    +    except ValueError as exc:
    +        raise HTTPException(
    +            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
    +        )
    +
    +
    +# ---------------------------------------------------------------------------
    +# Contracts
    +# ---------------------------------------------------------------------------
    +
    +
    +@router.post("/{project_id}/contracts/generate")
    +async def gen_contracts(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Generate all contract files from completed questionnaire answers."""
    +    try:
    +        contracts = await generate_contracts(current_user["id"], project_id)
    +    except ValueError as exc:
    +        detail = str(exc)
    +        code = (
    +            status.HTTP_404_NOT_FOUND
    +            if "not found" in detail.lower()
    +            else status.HTTP_400_BAD_REQUEST
    +        )
    +        raise HTTPException(status_code=code, detail=detail)
    +    return {"contracts": contracts}
    +
    +
    +@router.get("/{project_id}/contracts")
    +async def list_project_contracts(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """List generated contracts."""
    +    try:
    +        contracts = await list_contracts(current_user["id"], project_id)
    +    except ValueError as exc:
    +        raise HTTPException(
    +            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
    +        )
    +    return {
    +        "items": [
    +            {
    +                "id": str(c["id"]),
    +                "project_id": str(c["project_id"]),
    +                "contract_type": c["contract_type"],
    +                "version": c["version"],
    +                "created_at": c["created_at"],
    +                "updated_at": c["updated_at"],
    +            }
    +            for c in contracts
    +        ]
    +    }
    +
    +
    +@router.get("/{project_id}/contracts/{contract_type}")
    +async def get_project_contract(
    +    project_id: UUID,
    +    contract_type: str,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """View a single contract."""
    +    try:
    +        return await get_contract(current_user["id"], project_id, contract_type)
    +    except ValueError as exc:
    +        detail = str(exc)
    +        code = (
    +            status.HTTP_404_NOT_FOUND
    +            if "not found" in detail.lower()
    +            else status.HTTP_400_BAD_REQUEST
    +        )
    +        raise HTTPException(status_code=code, detail=detail)
    +
    +
    +@router.put("/{project_id}/contracts/{contract_type}")
    +async def edit_contract(
    +    project_id: UUID,
    +    contract_type: str,
    +    body: UpdateContractRequest,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Edit a contract before build."""
    +    try:
    +        result = await update_contract(
    +            current_user["id"], project_id, contract_type, body.content
    +        )
    +    except ValueError as exc:
    +        detail = str(exc)
    +        code = (
    +            status.HTTP_404_NOT_FOUND
    +            if "not found" in detail.lower()
    +            else status.HTTP_400_BAD_REQUEST
    +        )
    +        raise HTTPException(status_code=code, detail=detail)
    +    return {
    +        "id": str(result["id"]),
    +        "contract_type": result["contract_type"],
    +        "content": result["content"],
    +        "version": result["version"],
    +        "updated_at": result["updated_at"],
    +    }
    diff --git a/app/audit/runner.py b/app/audit/runner.py
    index 56bb940..907bd8a 100644
    --- a/app/audit/runner.py
    +++ b/app/audit/runner.py
    @@ -404,7 +404,22 @@ def check_a7_verification_order(gov_root: str) -> GovernanceCheckResult:
             }
     
         with open(diff_log, encoding="utf-8") as f:
    -        text = f.read()
    +        full_text = f.read()
    +
    +    # Only scan the ## Verification section so that keywords appearing
    +    # in file names, table names, or diff hunks don't cause false positives.
    +    ver_start = full_text.find("## Verification")
    +    if ver_start < 0:
    +        return {
    +            "code": "A7",
    +            "name": "Verification hierarchy order",
    +            "result": "FAIL",
    +            "detail": "No ## Verification section found in updatedifflog.md.",
    +        }
    +    # The section runs until the next ## heading or end of file.
    +    rest = full_text[ver_start + len("## Verification"):]
    +    next_heading = rest.find("\n## ")
    +    text = rest[:next_heading] if next_heading >= 0 else rest
     
         keywords = ["Static", "Runtime", "Behavior", "Contract"]
         positions: list[int] = []
    diff --git a/app/clients/llm_client.py b/app/clients/llm_client.py
    new file mode 100644
    index 0000000..681f520
    --- /dev/null
    +++ b/app/clients/llm_client.py
    @@ -0,0 +1,75 @@
    +"""LLM client -- Anthropic Messages API wrapper for questionnaire chat."""
    +
    +import httpx
    +
    +ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
    +ANTHROPIC_API_VERSION = "2023-06-01"
    +
    +
    +def _headers(api_key: str) -> dict:
    +    """Return standard Anthropic API headers."""
    +    return {
    +        "x-api-key": api_key,
    +        "anthropic-version": ANTHROPIC_API_VERSION,
    +        "Content-Type": "application/json",
    +    }
    +
    +
    +async def chat(
    +    api_key: str,
    +    model: str,
    +    system_prompt: str,
    +    messages: list[dict],
    +    max_tokens: int = 2048,
    +) -> str:
    +    """Send a chat request to the Anthropic Messages API.
    +
    +    Parameters
    +    ----------
    +    api_key : str
    +        Anthropic API key.
    +    model : str
    +        Model identifier (e.g. ``claude-3-5-haiku-20241022``).
    +    system_prompt : str
    +        System-level instructions for the model.
    +    messages : list[dict]
    +        Conversation history as ``[{"role": "user"|"assistant", "content": str}]``.
    +    max_tokens : int
    +        Maximum tokens in the response.
    +
    +    Returns
    +    -------
    +    str
    +        The assistant's text reply.
    +
    +    Raises
    +    ------
    +    httpx.HTTPStatusError
    +        If the API returns a non-2xx status.
    +    ValueError
    +        If the response body is missing content.
    +    """
    +    async with httpx.AsyncClient(timeout=60.0) as client:
    +        response = await client.post(
    +            ANTHROPIC_MESSAGES_URL,
    +            headers=_headers(api_key),
    +            json={
    +                "model": model,
    +                "max_tokens": max_tokens,
    +                "system": system_prompt,
    +                "messages": messages,
    +            },
    +        )
    +        response.raise_for_status()
    +
    +    data = response.json()
    +    content_blocks = data.get("content", [])
    +    if not content_blocks:
    +        raise ValueError("Empty response from Anthropic API")
    +
    +    # Extract text from the first text content block.
    +    for block in content_blocks:
    +        if block.get("type") == "text":
    +            return block["text"]
    +
    +    raise ValueError("No text block in Anthropic API response")
    diff --git a/app/config.py b/app/config.py
    index d004eb2..baf01d0 100644
    --- a/app/config.py
    +++ b/app/config.py
    @@ -43,6 +43,10 @@ class Settings:
         JWT_SECRET: str = _require("JWT_SECRET")
         FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
         APP_URL: str = os.getenv("APP_URL", "http://localhost:8000")
    +    LLM_QUESTIONNAIRE_MODEL: str = os.getenv(
    +        "LLM_QUESTIONNAIRE_MODEL", "claude-3-5-haiku-20241022"
    +    )
    +    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
     
     
     # Validate at import time -- but only when NOT running under pytest.
    diff --git a/app/main.py b/app/main.py
    index f73a09e..b11324c 100644
    --- a/app/main.py
    +++ b/app/main.py
    @@ -10,6 +10,7 @@ from fastapi.responses import JSONResponse
     from app.api.routers.audit import router as audit_router
     from app.api.routers.auth import router as auth_router
     from app.api.routers.health import router as health_router
    +from app.api.routers.projects import router as projects_router
     from app.api.routers.repos import router as repos_router
     from app.api.routers.webhooks import router as webhooks_router
     from app.api.routers.ws import router as ws_router
    @@ -50,13 +51,14 @@ def create_app() -> FastAPI:
             CORSMiddleware,
             allow_origins=[settings.FRONTEND_URL],
             allow_credentials=True,
    -        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    +        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
             allow_headers=["Authorization", "Content-Type"],
         )
     
         application.include_router(health_router)
         application.include_router(auth_router)
         application.include_router(repos_router)
    +    application.include_router(projects_router)
         application.include_router(webhooks_router)
         application.include_router(ws_router)
         application.include_router(audit_router)
    diff --git a/app/repos/project_repo.py b/app/repos/project_repo.py
    new file mode 100644
    index 0000000..88ca6a4
    --- /dev/null
    +++ b/app/repos/project_repo.py
    @@ -0,0 +1,205 @@
    +"""Project repository -- database reads and writes for projects and project_contracts tables."""
    +
    +import json
    +from uuid import UUID
    +
    +from app.repos.db import get_pool
    +
    +
    +# ---------------------------------------------------------------------------
    +# projects
    +# ---------------------------------------------------------------------------
    +
    +
    +async def create_project(
    +    user_id: UUID,
    +    name: str,
    +    description: str | None = None,
    +) -> dict:
    +    """Insert a new project. Returns the created row as a dict."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        INSERT INTO projects (user_id, name, description)
    +        VALUES ($1, $2, $3)
    +        RETURNING id, user_id, name, description, status, repo_id,
    +                  questionnaire_state, created_at, updated_at
    +        """,
    +        user_id,
    +        name,
    +        description,
    +    )
    +    return _project_to_dict(row)
    +
    +
    +async def get_project_by_id(project_id: UUID) -> dict | None:
    +    """Fetch a project by primary key. Returns None if not found."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        SELECT id, user_id, name, description, status, repo_id,
    +               questionnaire_state, created_at, updated_at
    +        FROM projects
    +        WHERE id = $1
    +        """,
    +        project_id,
    +    )
    +    return _project_to_dict(row) if row else None
    +
    +
    +async def get_projects_by_user(user_id: UUID) -> list[dict]:
    +    """Fetch all projects for a user, newest first."""
    +    pool = await get_pool()
    +    rows = await pool.fetch(
    +        """
    +        SELECT id, user_id, name, description, status, repo_id,
    +               questionnaire_state, created_at, updated_at
    +        FROM projects
    +        WHERE user_id = $1
    +        ORDER BY created_at DESC
    +        """,
    +        user_id,
    +    )
    +    return [_project_to_dict(r) for r in rows]
    +
    +
    +async def update_project_status(project_id: UUID, status: str) -> None:
    +    """Update the status of a project."""
    +    pool = await get_pool()
    +    await pool.execute(
    +        """
    +        UPDATE projects SET status = $2, updated_at = now()
    +        WHERE id = $1
    +        """,
    +        project_id,
    +        status,
    +    )
    +
    +
    +async def update_questionnaire_state(
    +    project_id: UUID,
    +    state: dict,
    +) -> None:
    +    """Overwrite the questionnaire_state JSONB column."""
    +    pool = await get_pool()
    +    await pool.execute(
    +        """
    +        UPDATE projects SET questionnaire_state = $2::jsonb, updated_at = now()
    +        WHERE id = $1
    +        """,
    +        project_id,
    +        json.dumps(state),
    +    )
    +
    +
    +async def delete_project(project_id: UUID) -> bool:
    +    """Delete a project by primary key. Returns True if a row was deleted."""
    +    pool = await get_pool()
    +    result = await pool.execute(
    +        "DELETE FROM projects WHERE id = $1",
    +        project_id,
    +    )
    +    return result == "DELETE 1"
    +
    +
    +# ---------------------------------------------------------------------------
    +# project_contracts
    +# ---------------------------------------------------------------------------
    +
    +
    +async def upsert_contract(
    +    project_id: UUID,
    +    contract_type: str,
    +    content: str,
    +    version: int = 1,
    +) -> dict:
    +    """Insert or update a contract for a project. Returns the row as a dict."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        INSERT INTO project_contracts (project_id, contract_type, content, version)
    +        VALUES ($1, $2, $3, $4)
    +        ON CONFLICT (project_id, contract_type)
    +        DO UPDATE SET content = EXCLUDED.content,
    +                      version = project_contracts.version + 1,
    +                      updated_at = now()
    +        RETURNING id, project_id, contract_type, content, version,
    +                  created_at, updated_at
    +        """,
    +        project_id,
    +        contract_type,
    +        content,
    +        version,
    +    )
    +    return dict(row)
    +
    +
    +async def get_contracts_by_project(project_id: UUID) -> list[dict]:
    +    """Fetch all contracts for a project."""
    +    pool = await get_pool()
    +    rows = await pool.fetch(
    +        """
    +        SELECT id, project_id, contract_type, content, version,
    +               created_at, updated_at
    +        FROM project_contracts
    +        WHERE project_id = $1
    +        ORDER BY contract_type
    +        """,
    +        project_id,
    +    )
    +    return [dict(r) for r in rows]
    +
    +
    +async def get_contract_by_type(
    +    project_id: UUID,
    +    contract_type: str,
    +) -> dict | None:
    +    """Fetch a single contract by project and type. Returns None if not found."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        SELECT id, project_id, contract_type, content, version,
    +               created_at, updated_at
    +        FROM project_contracts
    +        WHERE project_id = $1 AND contract_type = $2
    +        """,
    +        project_id,
    +        contract_type,
    +    )
    +    return dict(row) if row else None
    +
    +
    +async def update_contract_content(
    +    project_id: UUID,
    +    contract_type: str,
    +    content: str,
    +) -> dict | None:
    +    """Update the content of an existing contract. Returns updated row or None."""
    +    pool = await get_pool()
    +    row = await pool.fetchrow(
    +        """
    +        UPDATE project_contracts
    +        SET content = $3, version = version + 1, updated_at = now()
    +        WHERE project_id = $1 AND contract_type = $2
    +        RETURNING id, project_id, contract_type, content, version,
    +                  created_at, updated_at
    +        """,
    +        project_id,
    +        contract_type,
    +        content,
    +    )
    +    return dict(row) if row else None
    +
    +
    +# ---------------------------------------------------------------------------
    +# helpers
    +# ---------------------------------------------------------------------------
    +
    +
    +def _project_to_dict(row) -> dict:
    +    """Convert a project row to a dict, parsing JSONB questionnaire_state."""
    +    d = dict(row)
    +    qs = d.get("questionnaire_state")
    +    if isinstance(qs, str):
    +        d["questionnaire_state"] = json.loads(qs)
    +    return d
    diff --git a/app/services/project_service.py b/app/services/project_service.py
    new file mode 100644
    index 0000000..138670f
    --- /dev/null
    +++ b/app/services/project_service.py
    @@ -0,0 +1,460 @@
    +"""Project service -- orchestrates project CRUD, questionnaire chat, and contract generation."""
    +
    +import json
    +import logging
    +from pathlib import Path
    +from uuid import UUID
    +
    +from app.clients.llm_client import chat as llm_chat
    +from app.config import settings
    +from app.repos.project_repo import (
    +    create_project as repo_create_project,
    +    delete_project as repo_delete_project,
    +    get_contract_by_type,
    +    get_contracts_by_project,
    +    get_project_by_id,
    +    get_projects_by_user,
    +    update_contract_content as repo_update_contract_content,
    +    update_project_status,
    +    update_questionnaire_state,
    +    upsert_contract,
    +)
    +
    +logger = logging.getLogger(__name__)
    +
    +# ---------------------------------------------------------------------------
    +# Questionnaire definitions
    +# ---------------------------------------------------------------------------
    +
    +QUESTIONNAIRE_SECTIONS = [
    +    "product_intent",
    +    "tech_stack",
    +    "database_schema",
    +    "api_endpoints",
    +    "ui_requirements",
    +    "architectural_boundaries",
    +    "deployment_target",
    +    "phase_breakdown",
    +]
    +
    +CONTRACT_TYPES = [
    +    "blueprint",
    +    "manifesto",
    +    "stack",
    +    "schema",
    +    "physics",
    +    "boundaries",
    +    "phases",
    +    "ui",
    +    "builder_contract",
    +    "builder_directive",
    +]
    +
    +TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "contracts"
    +
    +_SYSTEM_PROMPT = """\
    +You are a project intake specialist for Forge, an autonomous build system.
    +Your job is to guide the user through a structured questionnaire to collect
    +all the information needed to generate Forge contract files for their project.
    +
    +The questionnaire has these sections (in order):
    +1. product_intent ÔÇö What the product does, who it's for, key features
    +2. tech_stack ÔÇö Backend/frontend languages, frameworks, database, deployment
    +3. database_schema ÔÇö Tables, columns, relationships, constraints
    +4. api_endpoints ÔÇö REST/GraphQL endpoints, auth, request/response shapes
    +5. ui_requirements ÔÇö Pages, components, design system, responsive needs
    +6. architectural_boundaries ÔÇö Layer rules, forbidden imports, separation concerns
    +7. deployment_target ÔÇö Where it runs, CI/CD, infrastructure
    +8. phase_breakdown ÔÇö Implementation phases with deliverables and exit criteria
    +
    +RULES:
    +- Ask focused questions for the current section. One section at a time.
    +- When you have enough info for the current section, summarize what you captured
    +  and move to the next section.
    +- Your response MUST be valid JSON with this structure:
    +  {
    +    "reply": "<your message to the user>",
    +    "section": "<current section name>",
    +    "section_complete": true|false,
    +    "extracted_data": { <key-value pairs of captured information> }
    +  }
    +- If section_complete is true, extracted_data must contain the final data for
    +  that section.
    +- Be conversational but efficient. Don't ask unnecessary follow-ups if the user
    +  gave comprehensive answers.
    +- When all sections are complete, set section to "complete" and section_complete
    +  to true.
    +"""
    +
    +
    +# ---------------------------------------------------------------------------
    +# Project CRUD
    +# ---------------------------------------------------------------------------
    +
    +
    +async def create_new_project(
    +    user_id: UUID,
    +    name: str,
    +    description: str | None = None,
    +) -> dict:
    +    """Create a new project and return it."""
    +    project = await repo_create_project(user_id, name, description)
    +    return project
    +
    +
    +async def list_user_projects(user_id: UUID) -> list[dict]:
    +    """List all projects for a user."""
    +    return await get_projects_by_user(user_id)
    +
    +
    +async def get_project_detail(user_id: UUID, project_id: UUID) -> dict:
    +    """Get full project detail. Raises ValueError if not found or not owned."""
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    contracts = await get_contracts_by_project(project_id)
    +    qs = project.get("questionnaire_state", {})
    +
    +    project["questionnaire_progress"] = _questionnaire_progress(qs)
    +    project["contracts"] = [
    +        {
    +            "id": str(c["id"]),
    +            "project_id": str(c["project_id"]),
    +            "contract_type": c["contract_type"],
    +            "version": c["version"],
    +            "created_at": c["created_at"],
    +            "updated_at": c["updated_at"],
    +        }
    +        for c in contracts
    +    ]
    +    return project
    +
    +
    +async def delete_user_project(user_id: UUID, project_id: UUID) -> bool:
    +    """Delete a project if owned by user. Returns True if deleted."""
    +    project = await get_project_by_id(project_id)
    +    if not project or str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +    return await repo_delete_project(project_id)
    +
    +
    +# ---------------------------------------------------------------------------
    +# Questionnaire
    +# ---------------------------------------------------------------------------
    +
    +
    +async def process_questionnaire_message(
    +    user_id: UUID,
    +    project_id: UUID,
    +    message: str,
    +) -> dict:
    +    """Process a user message in the questionnaire chat.
    +
    +    Returns the LLM reply with section progress information.
    +    """
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    qs = project.get("questionnaire_state") or {}
    +    completed = qs.get("completed_sections", [])
    +    answers = qs.get("answers", {})
    +    history = qs.get("conversation_history", [])
    +
    +    # Determine the current section
    +    current_section = None
    +    for section in QUESTIONNAIRE_SECTIONS:
    +        if section not in completed:
    +            current_section = section
    +            break
    +
    +    if current_section is None:
    +        return {
    +            "reply": "The questionnaire is already complete. You can now generate contracts.",
    +            "section": "complete",
    +            "completed_sections": completed,
    +            "remaining_sections": [],
    +            "is_complete": True,
    +        }
    +
    +    # If this is the first message, update status to questionnaire
    +    if project["status"] == "draft":
    +        await update_project_status(project_id, "questionnaire")
    +
    +    # Build conversation for LLM
    +    context_msg = (
    +        f"Project name: {project['name']}\n"
    +        f"Project description: {project.get('description', 'N/A')}\n"
    +        f"Current section: {current_section}\n"
    +        f"Completed sections: {', '.join(completed) if completed else 'none'}\n"
    +        f"Previously collected data: {json.dumps(answers, indent=2)}"
    +    )
    +
    +    llm_messages = [{"role": "user", "content": context_msg}]
    +    llm_messages.extend(history)
    +    llm_messages.append({"role": "user", "content": message})
    +
    +    try:
    +        raw_reply = await llm_chat(
    +            api_key=settings.ANTHROPIC_API_KEY,
    +            model=settings.LLM_QUESTIONNAIRE_MODEL,
    +            system_prompt=_SYSTEM_PROMPT,
    +            messages=llm_messages,
    +        )
    +    except Exception as exc:
    +        logger.exception("LLM chat failed for project %s", project_id)
    +        raise ValueError(f"LLM service error: {exc}") from exc
    +
    +    # Parse the structured JSON response from the LLM
    +    parsed = _parse_llm_response(raw_reply)
    +
    +    # Update state based on LLM response
    +    history.append({"role": "user", "content": message})
    +    history.append({"role": "assistant", "content": parsed["reply"]})
    +
    +    if parsed.get("section_complete") and parsed.get("extracted_data"):
    +        section_name = parsed.get("section", current_section)
    +        answers[section_name] = parsed["extracted_data"]
    +        if section_name not in completed:
    +            completed.append(section_name)
    +
    +    new_state = {
    +        "completed_sections": completed,
    +        "answers": answers,
    +        "conversation_history": history,
    +    }
    +    await update_questionnaire_state(project_id, new_state)
    +
    +    # Check if all sections are now complete
    +    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]
    +    is_complete = len(remaining) == 0
    +
    +    if is_complete and project["status"] != "contracts_ready":
    +        await update_project_status(project_id, "contracts_ready")
    +
    +    return {
    +        "reply": parsed["reply"],
    +        "section": parsed.get("section", current_section),
    +        "completed_sections": completed,
    +        "remaining_sections": remaining,
    +        "is_complete": is_complete,
    +    }
    +
    +
    +async def get_questionnaire_state(
    +    user_id: UUID,
    +    project_id: UUID,
    +) -> dict:
    +    """Return current questionnaire progress."""
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    qs = project.get("questionnaire_state") or {}
    +    return _questionnaire_progress(qs)
    +
    +
    +# ---------------------------------------------------------------------------
    +# Contract generation
    +# ---------------------------------------------------------------------------
    +
    +
    +async def generate_contracts(
    +    user_id: UUID,
    +    project_id: UUID,
    +) -> list[dict]:
    +    """Generate all contract files from questionnaire answers.
    +
    +    Raises ValueError if questionnaire is not complete.
    +    """
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    qs = project.get("questionnaire_state") or {}
    +    completed = qs.get("completed_sections", [])
    +    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]
    +
    +    if remaining:
    +        raise ValueError(
    +            f"Questionnaire is not complete. Remaining sections: {', '.join(remaining)}"
    +        )
    +
    +    answers = qs.get("answers", {})
    +    template_vars = _build_template_vars(project, answers)
    +
    +    generated = []
    +    for contract_type in CONTRACT_TYPES:
    +        content = _render_template(contract_type, template_vars)
    +        row = await upsert_contract(project_id, contract_type, content)
    +        generated.append({
    +            "id": str(row["id"]),
    +            "project_id": str(row["project_id"]),
    +            "contract_type": row["contract_type"],
    +            "version": row["version"],
    +            "created_at": row["created_at"],
    +            "updated_at": row["updated_at"],
    +        })
    +
    +    await update_project_status(project_id, "contracts_ready")
    +    return generated
    +
    +
    +async def list_contracts(
    +    user_id: UUID,
    +    project_id: UUID,
    +) -> list[dict]:
    +    """List all contracts for a project."""
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    return await get_contracts_by_project(project_id)
    +
    +
    +async def get_contract(
    +    user_id: UUID,
    +    project_id: UUID,
    +    contract_type: str,
    +) -> dict:
    +    """Get a single contract. Raises ValueError if not found."""
    +    if contract_type not in CONTRACT_TYPES:
    +        raise ValueError(f"Invalid contract type: {contract_type}")
    +
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    contract = await get_contract_by_type(project_id, contract_type)
    +    if not contract:
    +        raise ValueError(f"Contract '{contract_type}' not found")
    +    return contract
    +
    +
    +async def update_contract(
    +    user_id: UUID,
    +    project_id: UUID,
    +    contract_type: str,
    +    content: str,
    +) -> dict:
    +    """Update a contract's content. Raises ValueError if not found."""
    +    if contract_type not in CONTRACT_TYPES:
    +        raise ValueError(f"Invalid contract type: {contract_type}")
    +
    +    project = await get_project_by_id(project_id)
    +    if not project:
    +        raise ValueError("Project not found")
    +    if str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    result = await repo_update_contract_content(project_id, contract_type, content)
    +    if not result:
    +        raise ValueError(f"Contract '{contract_type}' not found")
    +    return result
    +
    +
    +# ---------------------------------------------------------------------------
    +# Helpers
    +# ---------------------------------------------------------------------------
    +
    +
    +def _questionnaire_progress(qs: dict) -> dict:
    +    """Build a questionnaire progress dict from state."""
    +    completed = qs.get("completed_sections", [])
    +    remaining = [s for s in QUESTIONNAIRE_SECTIONS if s not in completed]
    +    current = remaining[0] if remaining else None
    +    return {
    +        "current_section": current,
    +        "completed_sections": completed,
    +        "remaining_sections": remaining,
    +        "is_complete": len(remaining) == 0,
    +    }
    +
    +
    +def _parse_llm_response(raw: str) -> dict:
    +    """Parse structured JSON from LLM response.
    +
    +    Falls back to treating the whole reply as plain text if JSON parsing fails.
    +    """
    +    # Try to extract JSON from the response (might be wrapped in markdown)
    +    text = raw.strip()
    +    if text.startswith("```"):
    +        # Strip markdown code fences
    +        lines = text.split("\n")
    +        lines = [l for l in lines if not l.strip().startswith("```")]
    +        text = "\n".join(lines).strip()
    +
    +    try:
    +        parsed = json.loads(text)
    +        if isinstance(parsed, dict) and "reply" in parsed:
    +            return parsed
    +    except (json.JSONDecodeError, TypeError):
    +        pass
    +
    +    # Fallback: treat as plain text reply
    +    return {
    +        "reply": raw,
    +        "section": None,
    +        "section_complete": False,
    +        "extracted_data": None,
    +    }
    +
    +
    +def _build_template_vars(project: dict, answers: dict) -> dict:
    +    """Flatten questionnaire answers into template variables."""
    +    variables = {
    +        "project_name": project["name"],
    +        "project_description": project.get("description", ""),
    +    }
    +
    +    # Flatten all answer sections into the variables dict
    +    for section_name, section_data in answers.items():
    +        if isinstance(section_data, dict):
    +            for key, value in section_data.items():
    +                if isinstance(value, list):
    +                    variables[key] = "\n".join(f"- {v}" for v in value)
    +                else:
    +                    variables[key] = str(value)
    +        elif isinstance(section_data, str):
    +            variables[section_name] = section_data
    +
    +    return variables
    +
    +
    +def _render_template(contract_type: str, variables: dict) -> str:
    +    """Render a contract template with the given variables.
    +
    +    Uses safe substitution -- missing variables become empty strings.
    +    """
    +    template_file = TEMPLATES_DIR / f"{contract_type}.md"
    +    if contract_type == "physics":
    +        template_file = TEMPLATES_DIR / "physics.yaml"
    +    elif contract_type == "boundaries":
    +        template_file = TEMPLATES_DIR / "boundaries.json"
    +
    +    if not template_file.exists():
    +        return f"# {variables.get('project_name', 'Project')} ÔÇö {contract_type}\n\nTemplate not found."
    +
    +    raw = template_file.read_text(encoding="utf-8")
    +
    +    # Safe substitution: replace {key} with value, leave unknown keys empty
    +    import re
    +
    +    def _replacer(match: re.Match) -> str:
    +        key = match.group(1)
    +        return variables.get(key, "")
    +
    +    return re.sub(r"\{(\w+)\}", _replacer, raw)
    diff --git a/app/templates/contracts/blueprint.md b/app/templates/contracts/blueprint.md
    new file mode 100644
    index 0000000..ee3ce62
    --- /dev/null
    +++ b/app/templates/contracts/blueprint.md
    @@ -0,0 +1,16 @@
    +# {project_name} ÔÇö Blueprint
    +
    +## Project Overview
    +{project_description}
    +
    +## Product Intent
    +{product_intent}
    +
    +## Target Users
    +{target_users}
    +
    +## Key Features
    +{key_features}
    +
    +## Success Criteria
    +{success_criteria}
    diff --git a/app/templates/contracts/boundaries.json b/app/templates/contracts/boundaries.json
    new file mode 100644
    index 0000000..2d89b0f
    --- /dev/null
    +++ b/app/templates/contracts/boundaries.json
    @@ -0,0 +1 @@
    +{boundaries_json}
    diff --git a/app/templates/contracts/builder_contract.md b/app/templates/contracts/builder_contract.md
    new file mode 100644
    index 0000000..02dd4da
    --- /dev/null
    +++ b/app/templates/contracts/builder_contract.md
    @@ -0,0 +1,33 @@
    +# Builder Contract ÔÇö {project_name}
    +
    +## ┬º1 Read Gate
    +
    +The builder MUST read these contract files before any work:
    +1. `builder_contract.md` (this file)
    +2. `phases.md`
    +3. `blueprint.md`
    +4. `manifesto.md`
    +5. `stack.md`
    +6. `schema.md`
    +7. `physics.yaml`
    +8. `boundaries.json`
    +9. `ui.md`
    +10. `builder_directive.md`
    +
    +## ┬º2 Verification Hierarchy
    +
    +Every commit requires four-step verification:
    +1. **Static** ÔÇö Syntax checks, linting
    +2. **Runtime** ÔÇö Application boots successfully
    +3. **Behavior** ÔÇö All tests pass
    +4. **Contract** ÔÇö Boundary compliance, schema conformance
    +
    +## ┬º3 Diff Log
    +
    +The builder MUST overwrite `evidence/updatedifflog.md` before every commit.
    +
    +## ┬º4 Audit Ledger
    +
    +Append-only audit trail in `evidence/audit_ledger.md`.
    +
    +{builder_contract_extras}
    diff --git a/app/templates/contracts/builder_directive.md b/app/templates/contracts/builder_directive.md
    new file mode 100644
    index 0000000..92d2acc
    --- /dev/null
    +++ b/app/templates/contracts/builder_directive.md
    @@ -0,0 +1,13 @@
    +# Builder Directive ÔÇö {project_name}
    +
    +AEM: enabled
    +Auto-authorize: enabled
    +
    +## Current Phase
    +Phase: 0 ÔÇö Scaffold
    +
    +## Settings
    +boot_script: true
    +max_loopback: 3
    +
    +{directive_extras}
    diff --git a/app/templates/contracts/manifesto.md b/app/templates/contracts/manifesto.md
    new file mode 100644
    index 0000000..04faf48
    --- /dev/null
    +++ b/app/templates/contracts/manifesto.md
    @@ -0,0 +1,16 @@
    +# {project_name} ÔÇö Manifesto
    +
    +## Core Principles
    +
    +1. **Quality over speed** ÔÇö Every commit must pass automated governance checks.
    +2. **Evidence-based progress** ÔÇö All changes are audited.
    +3. **Deterministic builds** ÔÇö Same inputs always produce same outputs.
    +
    +## Project Values
    +{project_values}
    +
    +## Non-Negotiables
    +{non_negotiables}
    +
    +## Architecture Philosophy
    +{architecture_philosophy}
    diff --git a/app/templates/contracts/phases.md b/app/templates/contracts/phases.md
    new file mode 100644
    index 0000000..e09a8a1
    --- /dev/null
    +++ b/app/templates/contracts/phases.md
    @@ -0,0 +1,3 @@
    +# {project_name} ÔÇö Phases
    +
    +{phases_content}
    diff --git a/app/templates/contracts/physics.yaml b/app/templates/contracts/physics.yaml
    new file mode 100644
    index 0000000..951e421
    --- /dev/null
    +++ b/app/templates/contracts/physics.yaml
    @@ -0,0 +1,13 @@
    +# {project_name} -- API Physics (v0.1)
    +# Canonical API specification. If it's not here, it doesn't exist.
    +
    +info:
    +  title: "{project_name} API"
    +  version: "0.1.0"
    +  description: "{project_description}"
    +
    +paths:
    +{api_paths}
    +
    +schemas:
    +{api_schemas}
    diff --git a/app/templates/contracts/schema.md b/app/templates/contracts/schema.md
    new file mode 100644
    index 0000000..c8ac915
    --- /dev/null
    +++ b/app/templates/contracts/schema.md
    @@ -0,0 +1,30 @@
    +# {project_name} ÔÇö Database Schema
    +
    +Canonical database schema. All migrations must implement this schema.
    +
    +---
    +
    +## Schema Version: 0.1 (initial)
    +
    +### Conventions
    +
    +- Table names: snake_case, plural
    +- Column names: snake_case
    +- Primary keys: UUID (gen_random_uuid())
    +- Timestamps: TIMESTAMPTZ
    +- Soft delete: No
    +
    +---
    +
    +## Tables
    +
    +{schema_tables}
    +
    +---
    +
    +## Migration Files
    +
    +```
    +db/migrations/
    +  001_initial_schema.sql
    +```
    diff --git a/app/templates/contracts/stack.md b/app/templates/contracts/stack.md
    new file mode 100644
    index 0000000..e914610
    --- /dev/null
    +++ b/app/templates/contracts/stack.md
    @@ -0,0 +1,23 @@
    +# {project_name} ÔÇö Tech Stack
    +
    +## Backend
    +- Language: {backend_language}
    +- Framework: {backend_framework}
    +- Runtime version: {backend_runtime_version}
    +
    +## Frontend
    +- Language: {frontend_language}
    +- Framework: {frontend_framework}
    +- Build tool: {frontend_build_tool}
    +
    +## Database
    +- Engine: {database_engine}
    +- Driver: {database_driver}
    +- ORM/Query: {database_query_approach}
    +
    +## Deployment
    +- Target: {deployment_target}
    +- CI/CD: {ci_cd}
    +
    +## Additional Libraries
    +{additional_libraries}
    diff --git a/app/templates/contracts/ui.md b/app/templates/contracts/ui.md
    new file mode 100644
    index 0000000..c772498
    --- /dev/null
    +++ b/app/templates/contracts/ui.md
    @@ -0,0 +1,13 @@
    +# {project_name} ÔÇö UI Specification
    +
    +## Design System
    +{design_system}
    +
    +## Pages
    +{ui_pages}
    +
    +## Components
    +{ui_components}
    +
    +## Responsive Breakpoints
    +{responsive_breakpoints}
    diff --git a/db/migrations/002_projects.sql b/db/migrations/002_projects.sql
    new file mode 100644
    index 0000000..46518c6
    --- /dev/null
    +++ b/db/migrations/002_projects.sql
    @@ -0,0 +1,28 @@
    +-- Phase 8: Project Intake & Questionnaire tables
    +
    +CREATE TABLE projects (
    +    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    +    name                  VARCHAR(255) NOT NULL,
    +    description           TEXT,
    +    status                VARCHAR(20) NOT NULL DEFAULT 'draft',
    +    repo_id               UUID REFERENCES repos(id) ON DELETE SET NULL,
    +    questionnaire_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
    +    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    +    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +
    +CREATE INDEX idx_projects_user_id ON projects(user_id);
    +
    +CREATE TABLE project_contracts (
    +    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    +    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    +    contract_type   VARCHAR(50) NOT NULL,
    +    content         TEXT NOT NULL,
    +    version         INTEGER NOT NULL DEFAULT 1,
    +    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    +    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    +);
    +
    +CREATE UNIQUE INDEX idx_project_contracts_project_type ON project_contracts(project_id, contract_type);
    +CREATE INDEX idx_project_contracts_project_id ON project_contracts(project_id);
    diff --git a/tests/test_audit_runner.py b/tests/test_audit_runner.py
    index 10ac45e..195bf69 100644
    --- a/tests/test_audit_runner.py
    +++ b/tests/test_audit_runner.py
    @@ -357,7 +357,7 @@ class TestA7VerificationOrder:
             evidence = tmp_project / "Forge" / "evidence"
             evidence.mkdir(parents=True, exist_ok=True)
             (evidence / "updatedifflog.md").write_text(
    -            "# Diff Log\n"
    +            "# Diff Log\n\n## Verification\n"
                 "- Contract: PASS\n"
                 "- Behavior: PASS\n"
                 "- Runtime: PASS\n"
    @@ -372,7 +372,7 @@ class TestA7VerificationOrder:
             evidence = tmp_project / "Forge" / "evidence"
             evidence.mkdir(parents=True, exist_ok=True)
             (evidence / "updatedifflog.md").write_text(
    -            "# Diff Log\n- Static: PASS\n- Runtime: PASS\n"
    +            "# Diff Log\n\n## Verification\n- Static: PASS\n- Runtime: PASS\n"
             )
             gov_root = str(tmp_project / "Forge")
             result = check_a7_verification_order(gov_root)
    diff --git a/tests/test_llm_client.py b/tests/test_llm_client.py
    new file mode 100644
    index 0000000..3e86367
    --- /dev/null
    +++ b/tests/test_llm_client.py
    @@ -0,0 +1,125 @@
    +"""Tests for LLM client -- Anthropic Messages API wrapper."""
    +
    +from unittest.mock import AsyncMock, MagicMock, patch
    +
    +import pytest
    +
    +from app.clients.llm_client import chat
    +
    +
    +def _make_mock_client(response_data):
    +    """Create a mock httpx.AsyncClient with given response data."""
    +    mock_response = MagicMock()
    +    mock_response.status_code = 200
    +    mock_response.raise_for_status = MagicMock()
    +    mock_response.json.return_value = response_data
    +
    +    mock_client = AsyncMock()
    +    mock_client.post.return_value = mock_response
    +    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    +    mock_client.__aexit__ = AsyncMock(return_value=False)
    +    return mock_client
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.clients.llm_client.httpx.AsyncClient")
    +async def test_chat_success(mock_client_cls):
    +    """Successful chat returns text content."""
    +    mock_client = _make_mock_client({
    +        "content": [{"type": "text", "text": "Hello from Haiku!"}],
    +        "model": "claude-3-5-haiku-20241022",
    +        "role": "assistant",
    +    })
    +    mock_client_cls.return_value = mock_client
    +
    +    result = await chat(
    +        api_key="test-key",
    +        model="claude-3-5-haiku-20241022",
    +        system_prompt="You are helpful.",
    +        messages=[{"role": "user", "content": "Hi"}],
    +    )
    +
    +    assert result == "Hello from Haiku!"
    +    mock_client.post.assert_called_once()
    +    call_kwargs = mock_client.post.call_args
    +    body = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    +    assert body["model"] == "claude-3-5-haiku-20241022"
    +    assert body["system"] == "You are helpful."
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.clients.llm_client.httpx.AsyncClient")
    +async def test_chat_empty_content(mock_client_cls):
    +    """Empty content raises ValueError."""
    +    mock_client = _make_mock_client({"content": []})
    +    mock_client_cls.return_value = mock_client
    +
    +    with pytest.raises(ValueError, match="Empty response"):
    +        await chat(
    +            api_key="test-key",
    +            model="claude-3-5-haiku-20241022",
    +            system_prompt="System",
    +            messages=[{"role": "user", "content": "Hi"}],
    +        )
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.clients.llm_client.httpx.AsyncClient")
    +async def test_chat_no_text_block(mock_client_cls):
    +    """Response with no text block raises ValueError."""
    +    mock_client = _make_mock_client({
    +        "content": [{"type": "tool_use", "id": "xyz", "name": "tool", "input": {}}]
    +    })
    +    mock_client_cls.return_value = mock_client
    +
    +    with pytest.raises(ValueError, match="No text block"):
    +        await chat(
    +            api_key="test-key",
    +            model="claude-3-5-haiku-20241022",
    +            system_prompt="System",
    +            messages=[{"role": "user", "content": "Hi"}],
    +        )
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.clients.llm_client.httpx.AsyncClient")
    +async def test_chat_sends_correct_headers(mock_client_cls):
    +    """Verify correct headers are sent to Anthropic API."""
    +    mock_client = _make_mock_client({
    +        "content": [{"type": "text", "text": "ok"}]
    +    })
    +    mock_client_cls.return_value = mock_client
    +
    +    await chat(
    +        api_key="sk-ant-test123",
    +        model="claude-3-5-haiku-20241022",
    +        system_prompt="System",
    +        messages=[{"role": "user", "content": "Hi"}],
    +    )
    +
    +    call_kwargs = mock_client.post.call_args
    +    headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
    +    assert headers["x-api-key"] == "sk-ant-test123"
    +    assert headers["anthropic-version"] == "2023-06-01"
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.clients.llm_client.httpx.AsyncClient")
    +async def test_chat_max_tokens_parameter(mock_client_cls):
    +    """Custom max_tokens is passed through."""
    +    mock_client = _make_mock_client({
    +        "content": [{"type": "text", "text": "ok"}]
    +    })
    +    mock_client_cls.return_value = mock_client
    +
    +    await chat(
    +        api_key="test-key",
    +        model="claude-3-5-haiku-20241022",
    +        system_prompt="System",
    +        messages=[{"role": "user", "content": "Hi"}],
    +        max_tokens=4096,
    +    )
    +
    +    call_kwargs = mock_client.post.call_args
    +    body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
    +    assert body["max_tokens"] == 4096
    diff --git a/tests/test_project_service.py b/tests/test_project_service.py
    new file mode 100644
    index 0000000..4410f26
    --- /dev/null
    +++ b/tests/test_project_service.py
    @@ -0,0 +1,336 @@
    +"""Tests for project service -- questionnaire logic and contract generation."""
    +
    +import json
    +from unittest.mock import AsyncMock, patch
    +from uuid import UUID
    +
    +import pytest
    +
    +from app.services.project_service import (
    +    QUESTIONNAIRE_SECTIONS,
    +    _build_template_vars,
    +    _parse_llm_response,
    +    _questionnaire_progress,
    +    _render_template,
    +    create_new_project,
    +    generate_contracts,
    +    get_project_detail,
    +    get_questionnaire_state,
    +    list_user_projects,
    +    process_questionnaire_message,
    +    update_contract,
    +)
    +
    +
    +USER_ID = UUID("22222222-2222-2222-2222-222222222222")
    +PROJECT_ID = UUID("44444444-4444-4444-4444-444444444444")
    +
    +
    +# ---------------------------------------------------------------------------
    +# _parse_llm_response
    +# ---------------------------------------------------------------------------
    +
    +
    +def test_parse_valid_json():
    +    raw = '{"reply": "Hello!", "section": "product_intent", "section_complete": false, "extracted_data": null}'
    +    result = _parse_llm_response(raw)
    +    assert result["reply"] == "Hello!"
    +    assert result["section"] == "product_intent"
    +    assert result["section_complete"] is False
    +
    +
    +def test_parse_json_with_code_fences():
    +    raw = '```json\n{"reply": "Hi", "section": "tech_stack", "section_complete": true, "extracted_data": {"lang": "python"}}\n```'
    +    result = _parse_llm_response(raw)
    +    assert result["reply"] == "Hi"
    +    assert result["section_complete"] is True
    +    assert result["extracted_data"]["lang"] == "python"
    +
    +
    +def test_parse_fallback_plain_text():
    +    raw = "I'm just a plain text response without JSON."
    +    result = _parse_llm_response(raw)
    +    assert result["reply"] == raw
    +    assert result["section_complete"] is False
    +
    +
    +def test_parse_invalid_json():
    +    raw = '{"reply": "missing closing brace'
    +    result = _parse_llm_response(raw)
    +    assert result["reply"] == raw
    +    assert result["section_complete"] is False
    +
    +
    +# ---------------------------------------------------------------------------
    +# _questionnaire_progress
    +# ---------------------------------------------------------------------------
    +
    +
    +def test_progress_empty():
    +    result = _questionnaire_progress({})
    +    assert result["current_section"] == "product_intent"
    +    assert result["completed_sections"] == []
    +    assert result["is_complete"] is False
    +    assert len(result["remaining_sections"]) == 8
    +
    +
    +def test_progress_partial():
    +    result = _questionnaire_progress(
    +        {"completed_sections": ["product_intent", "tech_stack"]}
    +    )
    +    assert result["current_section"] == "database_schema"
    +    assert len(result["completed_sections"]) == 2
    +    assert result["is_complete"] is False
    +
    +
    +def test_progress_complete():
    +    result = _questionnaire_progress(
    +        {"completed_sections": list(QUESTIONNAIRE_SECTIONS)}
    +    )
    +    assert result["current_section"] is None
    +    assert result["is_complete"] is True
    +    assert len(result["remaining_sections"]) == 0
    +
    +
    +# ---------------------------------------------------------------------------
    +# _build_template_vars
    +# ---------------------------------------------------------------------------
    +
    +
    +def test_build_template_vars():
    +    project = {"name": "TestApp", "description": "A test app"}
    +    answers = {
    +        "product_intent": {"product_intent": "Build a dashboard", "target_users": "devs"},
    +        "tech_stack": {"backend_language": "Python"},
    +    }
    +    result = _build_template_vars(project, answers)
    +    assert result["project_name"] == "TestApp"
    +    assert result["product_intent"] == "Build a dashboard"
    +    assert result["backend_language"] == "Python"
    +
    +
    +def test_build_template_vars_with_list():
    +    project = {"name": "TestApp", "description": ""}
    +    answers = {"product_intent": {"key_features": ["auth", "dashboard", "api"]}}
    +    result = _build_template_vars(project, answers)
    +    assert "- auth" in result["key_features"]
    +    assert "- dashboard" in result["key_features"]
    +
    +
    +# ---------------------------------------------------------------------------
    +# _render_template
    +# ---------------------------------------------------------------------------
    +
    +
    +def test_render_template_blueprint():
    +    variables = {
    +        "project_name": "TestApp",
    +        "project_description": "A test app",
    +        "product_intent": "Build something",
    +        "target_users": "developers",
    +        "key_features": "- feature1",
    +        "success_criteria": "works",
    +    }
    +    result = _render_template("blueprint", variables)
    +    assert "TestApp" in result
    +    assert "A test app" in result
    +    assert "Build something" in result
    +
    +
    +def test_render_template_missing_vars():
    +    variables = {"project_name": "TestApp"}
    +    result = _render_template("blueprint", variables)
    +    assert "TestApp" in result
    +    # Missing vars should become empty strings, not raise
    +    assert "{" not in result
    +
    +
    +# ---------------------------------------------------------------------------
    +# create_new_project (async, mocked repo)
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
    +async def test_create_new_project(mock_create):
    +    mock_create.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "My Project",
    +        "description": None,
    +        "status": "draft",
    +        "repo_id": None,
    +        "questionnaire_state": {},
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +
    +    result = await create_new_project(USER_ID, "My Project")
    +    assert result["name"] == "My Project"
    +    mock_create.assert_called_once_with(USER_ID, "My Project", None)
    +
    +
    +# ---------------------------------------------------------------------------
    +# process_questionnaire_message (async, mocked deps)
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.update_questionnaire_state", new_callable=AsyncMock)
    +@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
    +@patch("app.services.project_service.llm_chat", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_process_questionnaire_first_message(
    +    mock_project, mock_llm, mock_status, mock_qs
    +):
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "My Project",
    +        "description": "test",
    +        "status": "draft",
    +        "questionnaire_state": {},
    +    }
    +    mock_llm.return_value = json.dumps({
    +        "reply": "Tell me about your product.",
    +        "section": "product_intent",
    +        "section_complete": False,
    +        "extracted_data": None,
    +    })
    +
    +    result = await process_questionnaire_message(USER_ID, PROJECT_ID, "Hi")
    +
    +    assert result["reply"] == "Tell me about your product."
    +    assert result["is_complete"] is False
    +    mock_status.assert_called_once()  # draft -> questionnaire
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_process_questionnaire_not_found(mock_project):
    +    mock_project.return_value = None
    +
    +    with pytest.raises(ValueError, match="not found"):
    +        await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_process_questionnaire_wrong_user(mock_project):
    +    other_user = UUID("99999999-9999-9999-9999-999999999999")
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": other_user,
    +        "name": "Not mine",
    +        "description": None,
    +        "status": "draft",
    +        "questionnaire_state": {},
    +    }
    +
    +    with pytest.raises(ValueError, match="not found"):
    +        await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_process_questionnaire_already_complete(mock_project):
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "My Project",
    +        "description": None,
    +        "status": "contracts_ready",
    +        "questionnaire_state": {
    +            "completed_sections": list(QUESTIONNAIRE_SECTIONS),
    +        },
    +    }
    +
    +    result = await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")
    +    assert result["is_complete"] is True
    +
    +
    +# ---------------------------------------------------------------------------
    +# generate_contracts (async, mocked deps)
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
    +@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_generate_contracts_success(mock_project, mock_upsert, mock_status):
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "My Project",
    +        "description": "A test",
    +        "status": "contracts_ready",
    +        "questionnaire_state": {
    +            "completed_sections": list(QUESTIONNAIRE_SECTIONS),
    +            "answers": {s: {"key": "value"} for s in QUESTIONNAIRE_SECTIONS},
    +        },
    +    }
    +    mock_upsert.return_value = {
    +        "id": UUID("55555555-5555-5555-5555-555555555555"),
    +        "project_id": PROJECT_ID,
    +        "contract_type": "blueprint",
    +        "content": "# content",
    +        "version": 1,
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +
    +    result = await generate_contracts(USER_ID, PROJECT_ID)
    +    assert len(result) == 10
    +    assert mock_upsert.call_count == 10
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_generate_contracts_incomplete(mock_project):
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "My Project",
    +        "description": None,
    +        "status": "questionnaire",
    +        "questionnaire_state": {"completed_sections": ["product_intent"]},
    +    }
    +
    +    with pytest.raises(ValueError, match="not complete"):
    +        await generate_contracts(USER_ID, PROJECT_ID)
    +
    +
    +# ---------------------------------------------------------------------------
    +# get_questionnaire_state
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +async def test_get_questionnaire_state(mock_project):
    +    mock_project.return_value = {
    +        "id": PROJECT_ID,
    +        "user_id": USER_ID,
    +        "name": "P",
    +        "description": None,
    +        "status": "questionnaire",
    +        "questionnaire_state": {
    +            "completed_sections": ["product_intent", "tech_stack"],
    +        },
    +    }
    +
    +    result = await get_questionnaire_state(USER_ID, PROJECT_ID)
    +    assert result["current_section"] == "database_schema"
    +    assert result["is_complete"] is False
    +
    +
    +# ---------------------------------------------------------------------------
    +# update_contract
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +async def test_update_contract_invalid_type():
    +    with pytest.raises(ValueError, match="Invalid contract type"):
    +        await update_contract(USER_ID, PROJECT_ID, "not_a_type", "content")
    diff --git a/tests/test_projects_router.py b/tests/test_projects_router.py
    new file mode 100644
    index 0000000..b6d2611
    --- /dev/null
    +++ b/tests/test_projects_router.py
    @@ -0,0 +1,449 @@
    +"""Tests for projects router endpoints."""
    +
    +from unittest.mock import AsyncMock, patch
    +from uuid import UUID
    +
    +import pytest
    +from fastapi.testclient import TestClient
    +
    +from app.auth import create_token
    +from app.main import app
    +
    +
    +@pytest.fixture(autouse=True)
    +def _set_test_config(monkeypatch):
    +    """Set test configuration for all projects router tests."""
    +    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    +    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    +    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    +    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    +    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    +    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    +    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")
    +    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", "test-api-key")
    +    monkeypatch.setattr("app.config.settings.LLM_QUESTIONNAIRE_MODEL", "claude-3-5-haiku-20241022")
    +
    +
    +USER_ID = "22222222-2222-2222-2222-222222222222"
    +PROJECT_ID = "44444444-4444-4444-4444-444444444444"
    +CONTRACT_ID = "55555555-5555-5555-5555-555555555555"
    +MOCK_USER = {
    +    "id": UUID(USER_ID),
    +    "github_id": 99999,
    +    "github_login": "octocat",
    +    "avatar_url": "https://example.com/avatar.png",
    +    "access_token": "gho_testtoken123",
    +}
    +
    +client = TestClient(app)
    +
    +
    +def _auth_header():
    +    token = create_token(USER_ID, "octocat")
    +    return {"Authorization": f"Bearer {token}"}
    +
    +
    +# ---------- POST /projects ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
    +def test_create_project(mock_create, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_create.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": "A test project",
    +        "status": "draft",
    +        "repo_id": None,
    +        "questionnaire_state": {},
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +
    +    resp = client.post(
    +        "/projects",
    +        json={"name": "My Project", "description": "A test project"},
    +        headers=_auth_header(),
    +    )
    +
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert data["name"] == "My Project"
    +    assert data["status"] == "draft"
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_create_project_missing_name(mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    resp = client.post("/projects", json={}, headers=_auth_header())
    +    assert resp.status_code == 422
    +
    +
    +def test_create_project_unauthenticated():
    +    resp = client.post("/projects", json={"name": "Test"})
    +    assert resp.status_code == 401
    +
    +
    +# ---------- GET /projects ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_projects_by_user", new_callable=AsyncMock)
    +def test_list_projects(mock_list, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_list.return_value = [
    +        {
    +            "id": UUID(PROJECT_ID),
    +            "user_id": UUID(USER_ID),
    +            "name": "My Project",
    +            "description": None,
    +            "status": "draft",
    +            "created_at": "2025-01-01T00:00:00Z",
    +            "updated_at": "2025-01-01T00:00:00Z",
    +        }
    +    ]
    +
    +    resp = client.get("/projects", headers=_auth_header())
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert len(data["items"]) == 1
    +    assert data["items"][0]["name"] == "My Project"
    +
    +
    +# ---------- GET /projects/{id} ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock)
    +def test_get_project_detail(mock_contracts, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": "Desc",
    +        "status": "draft",
    +        "repo_id": None,
    +        "questionnaire_state": {},
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +    mock_contracts.return_value = []
    +
    +    resp = client.get(f"/projects/{PROJECT_ID}", headers=_auth_header())
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert data["name"] == "My Project"
    +    assert data["questionnaire_progress"]["is_complete"] is False
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +def test_get_project_not_found(mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = None
    +
    +    resp = client.get(f"/projects/{PROJECT_ID}", headers=_auth_header())
    +    assert resp.status_code == 404
    +
    +
    +# ---------- DELETE /projects/{id} ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.repo_delete_project", new_callable=AsyncMock)
    +def test_delete_project(mock_delete, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "draft",
    +    }
    +    mock_delete.return_value = True
    +
    +    resp = client.delete(f"/projects/{PROJECT_ID}", headers=_auth_header())
    +    assert resp.status_code == 200
    +    assert resp.json()["status"] == "deleted"
    +
    +
    +# ---------- POST /projects/{id}/questionnaire ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
    +@patch("app.services.project_service.update_questionnaire_state", new_callable=AsyncMock)
    +@patch("app.services.project_service.llm_chat", new_callable=AsyncMock)
    +def test_questionnaire_message(
    +    mock_llm, mock_update_qs, mock_update_status, mock_project, mock_get_user
    +):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": "A test",
    +        "status": "draft",
    +        "questionnaire_state": {},
    +    }
    +    mock_llm.return_value = '{"reply": "What does your product do?", "section": "product_intent", "section_complete": false, "extracted_data": null}'
    +
    +    resp = client.post(
    +        f"/projects/{PROJECT_ID}/questionnaire",
    +        json={"message": "I want to build an app"},
    +        headers=_auth_header(),
    +    )
    +
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert "reply" in data
    +    assert data["is_complete"] is False
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +def test_questionnaire_project_not_found(mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = None
    +
    +    resp = client.post(
    +        f"/projects/{PROJECT_ID}/questionnaire",
    +        json={"message": "hello"},
    +        headers=_auth_header(),
    +    )
    +    assert resp.status_code == 404
    +
    +
    +# ---------- GET /projects/{id}/questionnaire/state ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +def test_questionnaire_state(mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "questionnaire",
    +        "questionnaire_state": {
    +            "completed_sections": ["product_intent"],
    +            "answers": {"product_intent": {"intent": "build an app"}},
    +        },
    +    }
    +
    +    resp = client.get(
    +        f"/projects/{PROJECT_ID}/questionnaire/state", headers=_auth_header()
    +    )
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert data["completed_sections"] == ["product_intent"]
    +    assert data["current_section"] == "tech_stack"
    +    assert data["is_complete"] is False
    +
    +
    +# ---------- POST /projects/{id}/contracts/generate ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +def test_generate_contracts_incomplete(mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "questionnaire",
    +        "questionnaire_state": {"completed_sections": ["product_intent"]},
    +    }
    +
    +    resp = client.post(
    +        f"/projects/{PROJECT_ID}/contracts/generate", headers=_auth_header()
    +    )
    +    assert resp.status_code == 400
    +    assert "not complete" in resp.json()["detail"].lower()
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
    +@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
    +def test_generate_contracts_success(
    +    mock_status, mock_upsert, mock_project, mock_get_user
    +):
    +    mock_get_user.return_value = MOCK_USER
    +    all_sections = [
    +        "product_intent", "tech_stack", "database_schema", "api_endpoints",
    +        "ui_requirements", "architectural_boundaries", "deployment_target",
    +        "phase_breakdown",
    +    ]
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": "A test",
    +        "status": "contracts_ready",
    +        "questionnaire_state": {
    +            "completed_sections": all_sections,
    +            "answers": {s: {"key": "value"} for s in all_sections},
    +        },
    +    }
    +    mock_upsert.return_value = {
    +        "id": UUID(CONTRACT_ID),
    +        "project_id": UUID(PROJECT_ID),
    +        "contract_type": "blueprint",
    +        "content": "# content",
    +        "version": 1,
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +
    +    resp = client.post(
    +        f"/projects/{PROJECT_ID}/contracts/generate", headers=_auth_header()
    +    )
    +    assert resp.status_code == 200
    +    data = resp.json()
    +    assert len(data["contracts"]) == 10
    +
    +
    +# ---------- GET /projects/{id}/contracts ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock)
    +def test_list_contracts(mock_contracts, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "contracts_ready",
    +    }
    +    mock_contracts.return_value = [
    +        {
    +            "id": UUID(CONTRACT_ID),
    +            "project_id": UUID(PROJECT_ID),
    +            "contract_type": "blueprint",
    +            "content": "# content",
    +            "version": 1,
    +            "created_at": "2025-01-01T00:00:00Z",
    +            "updated_at": "2025-01-01T00:00:00Z",
    +        }
    +    ]
    +
    +    resp = client.get(
    +        f"/projects/{PROJECT_ID}/contracts", headers=_auth_header()
    +    )
    +    assert resp.status_code == 200
    +    assert len(resp.json()["items"]) == 1
    +
    +
    +# ---------- GET /projects/{id}/contracts/{type} ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_contract_by_type", new_callable=AsyncMock)
    +def test_get_contract(mock_contract, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "contracts_ready",
    +    }
    +    mock_contract.return_value = {
    +        "id": UUID(CONTRACT_ID),
    +        "project_id": UUID(PROJECT_ID),
    +        "contract_type": "blueprint",
    +        "content": "# My Blueprint",
    +        "version": 1,
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-01T00:00:00Z",
    +    }
    +
    +    resp = client.get(
    +        f"/projects/{PROJECT_ID}/contracts/blueprint", headers=_auth_header()
    +    )
    +    assert resp.status_code == 200
    +    assert resp.json()["content"] == "# My Blueprint"
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_contract_by_type", new_callable=AsyncMock)
    +def test_get_contract_not_found(mock_contract, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "contracts_ready",
    +    }
    +    mock_contract.return_value = None
    +
    +    resp = client.get(
    +        f"/projects/{PROJECT_ID}/contracts/blueprint", headers=_auth_header()
    +    )
    +    assert resp.status_code == 404
    +
    +
    +# ---------- PUT /projects/{id}/contracts/{type} ----------
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
    +@patch(
    +    "app.services.project_service.repo_update_contract_content",
    +    new_callable=AsyncMock,
    +)
    +def test_update_contract(mock_update, mock_project, mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +    mock_project.return_value = {
    +        "id": UUID(PROJECT_ID),
    +        "user_id": UUID(USER_ID),
    +        "name": "My Project",
    +        "description": None,
    +        "status": "contracts_ready",
    +    }
    +    mock_update.return_value = {
    +        "id": UUID(CONTRACT_ID),
    +        "project_id": UUID(PROJECT_ID),
    +        "contract_type": "blueprint",
    +        "content": "# Updated",
    +        "version": 2,
    +        "created_at": "2025-01-01T00:00:00Z",
    +        "updated_at": "2025-01-02T00:00:00Z",
    +    }
    +
    +    resp = client.put(
    +        f"/projects/{PROJECT_ID}/contracts/blueprint",
    +        json={"content": "# Updated"},
    +        headers=_auth_header(),
    +    )
    +    assert resp.status_code == 200
    +    assert resp.json()["version"] == 2
    +
    +
    +@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +def test_update_contract_invalid_type(mock_get_user):
    +    mock_get_user.return_value = MOCK_USER
    +
    +    resp = client.put(
    +        f"/projects/{PROJECT_ID}/contracts/invalid_type",
    +        json={"content": "# test"},
    +        headers=_auth_header(),
    +    )
    +    assert resp.status_code == 400

