Status: PASS
Start: 2026-02-15T02:55:23Z
End: 2026-02-15T02:55:37Z
Branch: master
HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
import_sanity exit: 0
pytest exit: 0
compileall exit: 0
git status -sb:
```
## master...origin/master
M  Forge/Contracts/physics.yaml
M  Forge/Contracts/schema.md
M  Forge/evidence/audit_ledger.md
MM Forge/evidence/test_runs.md
MM Forge/evidence/test_runs_latest.md
M  Forge/evidence/updatedifflog.md
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
```
git diff --stat:
```
 Forge/evidence/test_runs.md        | 48 ++++++++++++++++++++++++++++++++
 Forge/evidence/test_runs_latest.md | 56 +++++++++++++++++++++++---------------
 2 files changed, 82 insertions(+), 22 deletions(-)
```

