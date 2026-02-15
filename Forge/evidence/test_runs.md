## Test Run 2026-02-14T22:11:36Z
- Status: PASS
- Start: 2026-02-14T22:11:36Z
- End: 2026-02-14T22:11:38Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: git unavailable
- HEAD: git unavailable
- import_sanity exit: 0
- compileall exit: 0
- pytest exit: 0
- git status -sb:
```
## No commits yet on master
?? .env.example
?? .gitignore
?? Forge/
?? USER_INSTRUCTIONS.md
?? app/
?? boot.ps1
?? db/
?? forge.json
?? requirements.txt
?? tests/
?? web/
```
- git diff --stat:
```

```

## Test Run 2026-02-14T22:21:20Z
- Status: PASS
- Start: 2026-02-14T22:21:20Z
- End: 2026-02-14T22:21:22Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 33d19700643b5d51d9ae1b436dae5cb72ba2a166
- import_sanity exit: 0
- pytest exit: 0
- compileall exit: 0
- git status -sb:
```
## master
 M Forge/scripts/watch_audit.ps1
 M app/main.py
 M web/src/App.tsx
 M web/src/__tests__/App.test.tsx
?? Forge/evidence/audit_ledger.md
?? app/api/deps.py
?? app/api/routers/auth.py
?? app/auth.py
?? app/clients/github_client.py
?? app/config.py
?? app/repos/db.py
?? app/repos/user_repo.py
?? app/services/auth_service.py
?? tests/test_auth.py
?? tests/test_auth_router.py
?? web/src/context/
?? web/src/pages/
```
- git diff --stat:
```
 Forge/scripts/watch_audit.ps1  | 57 +++++++++++++++++++++---------------------
 app/main.py                    | 24 ++++++++++++++++++
 web/src/App.tsx                | 31 ++++++++++++++++++++---
 web/src/__tests__/App.test.tsx | 11 +++++---
 4 files changed, 88 insertions(+), 35 deletions(-)
```

## Test Run 2026-02-14T22:30:30Z
- Status: PASS
- Start: 2026-02-14T22:30:30Z
- End: 2026-02-14T22:30:33Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: af904f59503c1317c34412bd31f15988927bdc49
- pytest exit: 0
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master
 M .env.example
 M Forge/evidence/audit_ledger.md
 M app/clients/github_client.py
 M app/config.py
 M app/main.py
 M app/repos/user_repo.py
 M web/src/__tests__/App.test.tsx
 M web/src/pages/Dashboard.tsx
?? app/api/routers/repos.py
?? app/repos/repo_repo.py
?? app/services/repo_service.py
?? tests/test_repos_router.py
?? web/src/components/
```
- git diff --stat:
```
 .env.example                   |   1 +
 Forge/evidence/audit_ledger.md |  46 +++++++++++++++
 app/clients/github_client.py   | 101 ++++++++++++++++++++++++++++++--
 app/config.py                  |   1 +
 app/main.py                    |   2 +
 app/repos/user_repo.py         |   2 +-
 web/src/__tests__/App.test.tsx |  32 ++++++++++
 web/src/pages/Dashboard.tsx    | 130 +++++++++++++++++++++++++++++++++++++----
 8 files changed, 298 insertions(+), 17 deletions(-)
```

## Test Run 2026-02-14T22:30:42Z
- Status: PASS
- Start: 2026-02-14T22:30:42Z
- End: 2026-02-14T22:30:44Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: af904f59503c1317c34412bd31f15988927bdc49
- compileall exit: 0
- pytest exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master
 M .env.example
 M Forge/evidence/audit_ledger.md
 M Forge/evidence/test_runs.md
 M Forge/evidence/test_runs_latest.md
 M app/clients/github_client.py
 M app/config.py
 M app/main.py
 M app/repos/user_repo.py
 M web/src/__tests__/App.test.tsx
 M web/src/pages/Dashboard.tsx
?? app/api/routers/repos.py
?? app/repos/repo_repo.py
?? app/services/repo_service.py
?? tests/test_repos_router.py
?? web/src/components/
```
- git diff --stat:
```
 .env.example                       |   1 +
 Forge/evidence/audit_ledger.md     |  46 +++++++++++++
 Forge/evidence/test_runs.md        |  40 ++++++++++++
 Forge/evidence/test_runs_latest.md |  48 +++++++-------
 app/clients/github_client.py       | 101 ++++++++++++++++++++++++++--
 app/config.py                      |   1 +
 app/main.py                        |   2 +
 app/repos/user_repo.py             |   2 +-
 web/src/__tests__/App.test.tsx     |  32 +++++++++
 web/src/pages/Dashboard.tsx        | 130 +++++++++++++++++++++++++++++++++----
 10 files changed, 362 insertions(+), 41 deletions(-)
```

## Test Run 2026-02-14T22:39:38Z
- Status: PASS
- Start: 2026-02-14T22:39:38Z
- End: 2026-02-14T22:39:40Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
- import_sanity exit: 0
- compileall exit: 0
- pytest exit: 0
- git status -sb:
```
## master
 M Forge/Contracts/builder_contract.md
 M Forge/evidence/audit_ledger.md
 M Forge/scripts/run_audit.ps1
 M Forge/scripts/watch_audit.ps1
 M app/api/routers/repos.py
 M app/audit/__init__.py
 M app/clients/github_client.py
 M app/main.py
 M web/src/App.tsx
 M web/src/__tests__/App.test.tsx
 M web/src/pages/Dashboard.tsx
?? app/api/routers/webhooks.py
?? app/audit/engine.py
?? app/repos/audit_repo.py
?? app/services/audit_service.py
?? app/webhooks.py
?? tests/test_audit_engine.py
?? tests/test_webhook_router.py
?? tests/test_webhooks.py
?? web/src/components/CheckResultCard.tsx
?? web/src/components/CommitRow.tsx
?? web/src/components/ResultBadge.tsx
?? web/src/pages/AuditDetail.tsx
?? web/src/pages/CommitTimeline.tsx
```
- git diff --stat:
```
 Forge/Contracts/builder_contract.md | 55 ++++++++++++++++++++++++----------
 Forge/evidence/audit_ledger.md      | 44 +++++++++++++++++++++++++++
 Forge/scripts/run_audit.ps1         | 12 ++++----
 Forge/scripts/watch_audit.ps1       | 60 ++++++++++++++++++-------------------
 app/api/routers/repos.py            | 49 ++++++++++++++++++++++++++++--
 app/audit/__init__.py               |  1 +
 app/clients/github_client.py        | 43 ++++++++++++++++++++++++++
 app/main.py                         |  2 ++
 web/src/App.tsx                     | 33 +++++++++++++++++++-
 web/src/__tests__/App.test.tsx      | 37 +++++++++++++++++++++++
 web/src/pages/Dashboard.tsx         |  6 ++--
 11 files changed, 286 insertions(+), 56 deletions(-)
```

## Test Run 2026-02-14T22:56:45Z
- Status: PASS
- Start: 2026-02-14T22:56:45Z
- End: 2026-02-14T22:56:47Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
- pytest exit: 0
- import_sanity exit: 0
- compileall exit: 0
- git status -sb:
```
## master
M  Forge/Contracts/builder_contract.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
M  Forge/evidence/updatedifflog.md
M  Forge/scripts/watch_audit.ps1
M  app/api/routers/repos.py
A  app/api/routers/webhooks.py
M  app/audit/__init__.py
AM app/audit/engine.py
M  app/clients/github_client.py
M  app/main.py
A  app/repos/audit_repo.py
AM app/services/audit_service.py
AM app/webhooks.py
A  tests/test_audit_engine.py
AM tests/test_webhook_router.py
AM tests/test_webhooks.py
M  web/src/App.tsx
M  web/src/__tests__/App.test.tsx
A  web/src/components/CheckResultCard.tsx
A  web/src/components/CommitRow.tsx
A  web/src/components/ResultBadge.tsx
A  web/src/pages/AuditDetail.tsx
A  web/src/pages/CommitTimeline.tsx
M  web/src/pages/Dashboard.tsx
```
- git diff --stat:
```
 app/audit/engine.py           |  2 --
 app/services/audit_service.py |  2 --
 app/webhooks.py               | 27 +++++++++++++++++++--------
 tests/test_webhook_router.py  |  5 ++---
 tests/test_webhooks.py        |  9 +++------
 5 files changed, 24 insertions(+), 21 deletions(-)
```

## Test Run 2026-02-14T23:09:21Z
- Status: PASS
- Start: 2026-02-14T23:09:21Z
- End: 2026-02-14T23:09:23Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
- import_sanity exit: 0
- pytest exit: 0
- compileall exit: 0
- git status -sb:
```
## master
M  Forge/Contracts/builder_contract.md
M  Forge/evidence/audit_ledger.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
M  Forge/evidence/updatedifflog.md
M  Forge/scripts/watch_audit.ps1
M  app/api/routers/repos.py
A  app/api/routers/webhooks.py
M  app/audit/__init__.py
A  app/audit/engine.py
M  app/clients/github_client.py
M  app/main.py
A  app/repos/audit_repo.py
A  app/services/audit_service.py
A  app/webhooks.py
A  tests/test_audit_engine.py
A  tests/test_webhook_router.py
A  tests/test_webhooks.py
M  web/src/App.tsx
M  web/src/__tests__/App.test.tsx
A  web/src/components/CheckResultCard.tsx
A  web/src/components/CommitRow.tsx
A  web/src/components/ResultBadge.tsx
A  web/src/pages/AuditDetail.tsx
A  web/src/pages/CommitTimeline.tsx
M  web/src/pages/Dashboard.tsx
```
- git diff --stat:
```

```

## Test Run 2026-02-14T23:24:21Z
- Status: PASS
- Start: 2026-02-14T23:24:21Z
- End: 2026-02-14T23:24:23Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master
 M Forge/scripts/watch_audit.ps1
 M app/main.py
 M app/repos/audit_repo.py
 M app/repos/repo_repo.py
 M app/services/audit_service.py
 M app/services/repo_service.py
 M tests/test_repos_router.py
 M web/src/App.tsx
 M web/src/__tests__/App.test.tsx
 M web/src/components/CommitRow.tsx
 M web/src/index.css
 M web/src/pages/AuditDetail.tsx
 M web/src/pages/CommitTimeline.tsx
 M web/src/pages/Dashboard.tsx
?? app/api/routers/ws.py
?? app/ws_manager.py
?? tests/test_repo_health.py
?? tests/test_ws_manager.py
?? tests/test_ws_router.py
?? web/src/components/AppShell.tsx
?? web/src/components/EmptyState.tsx
?? web/src/components/Skeleton.tsx
?? web/src/context/ToastContext.tsx
?? web/src/hooks/
```
- git diff --stat:
```
 Forge/scripts/watch_audit.ps1    |   9 +++
 app/main.py                      |   2 +
 app/repos/audit_repo.py          |  16 ++--
 app/repos/repo_repo.py           |  38 ++++++++++
 app/services/audit_service.py    |  18 +++++
 app/services/repo_service.py     |  28 +++++--
 tests/test_repos_router.py       |   8 +-
 web/src/App.tsx                  |  73 ++++++++----------
 web/src/__tests__/App.test.tsx   |  41 +++++++++-
 web/src/components/CommitRow.tsx |   8 +-
 web/src/index.css                |   5 ++
 web/src/pages/AuditDetail.tsx    | 156 ++++++++++++++++++++++-----------------
 web/src/pages/CommitTimeline.tsx | 143 +++++++++++++++++++++--------------
 web/src/pages/Dashboard.tsx      |  90 +++++++++-------------
 14 files changed, 405 insertions(+), 230 deletions(-)
```

## Test Run 2026-02-14T23:39:46Z
- Status: PASS
- Start: 2026-02-14T23:39:46Z
- End: 2026-02-14T23:39:48Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 007855acf36050e87fe70e837aae2b6c6e5716fa
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master
```
- git diff --stat:
```

```

## Test Run 2026-02-14T23:39:52Z
- Status: PASS
- Start: 2026-02-14T23:39:52Z
- End: 2026-02-14T23:39:53Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: git unavailable
- HEAD: git unavailable
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
git unavailable
```
- git diff --stat:
```
git unavailable
```

## Test Run 2026-02-14T23:40:07Z
- Status: PASS
- Start: 2026-02-14T23:40:07Z
- End: 2026-02-14T23:40:08Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: git unavailable
- HEAD: git unavailable
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
git unavailable
```
- git diff --stat:
```
git unavailable
```

## Test Run 2026-02-14T23:56:12Z
- Status: PASS
- Start: 2026-02-14T23:56:12Z
- End: 2026-02-14T23:56:13Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: e5f7dcd282338470cfdab5fa2dab9d27e544fac5
- compileall exit: 0
- git status -sb:
```
## master
 M Forge/scripts/watch_audit.ps1
 M USER_INSTRUCTIONS.md
 M app/api/routers/repos.py
 M app/api/routers/webhooks.py
 M app/config.py
 M app/main.py
 M boot.ps1
?? app/api/rate_limit.py
?? tests/test_config.py
?? tests/test_hardening.py
?? tests/test_rate_limit.py
```
- git diff --stat:
```
 Forge/scripts/watch_audit.ps1 |  99 ++++++++++++++++++++
 USER_INSTRUCTIONS.md          | 142 +++++++++++++++++++++++++---
 app/api/routers/repos.py      |  25 ++++-
 app/api/routers/webhooks.py   |  22 ++++-
 app/config.py                 |  49 ++++++++--
 app/main.py                   |  21 ++++-
 boot.ps1                      | 209 +++++++++++++++++++++++++++++++-----------
 7 files changed, 488 insertions(+), 79 deletions(-)
```

## Test Run 2026-02-15T01:08:47Z
- Status: PASS
- Start: 2026-02-15T01:08:47Z
- End: 2026-02-15T01:08:49Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 210a82cd02a26464539c7bbff5f45c8918f63802
- import_sanity exit: 0
- pytest exit: 0
- compileall exit: 0
- git status -sb:
```
## master...origin/master
 M Forge/Contracts/physics.yaml
 M Forge/evidence/updatedifflog.md
 M app/api/routers/health.py
 M app/config.py
 M tests/test_health.py
 M web/src/components/AppShell.tsx
```
- git diff --stat:
```
 Forge/Contracts/physics.yaml    |    8 +
 Forge/evidence/updatedifflog.md | 1137 +--------------------------------------
 app/api/routers/health.py       |    8 +
 app/config.py                   |    2 +
 tests/test_health.py            |    9 +
 web/src/components/AppShell.tsx |   13 +
 6 files changed, 65 insertions(+), 1112 deletions(-)
```

## Test Run 2026-02-15T02:04:47Z
- Status: PASS
- Start: 2026-02-15T02:04:47Z
- End: 2026-02-15T02:05:00Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: b4a6987db4bfb57648d4cec42b81d47b0d6ce0d0
- import_sanity exit: 0
- compileall exit: 0
- pytest exit: 0
- git status -sb:
```
## master...origin/master
 M Forge/Contracts/physics.yaml
 M Forge/evidence/audit_ledger.md
 M Forge/evidence/updatedifflog.md
 M app/main.py
 M app/services/audit_service.py
?? app/api/routers/audit.py
?? app/audit/__main__.py
?? app/audit/runner.py
?? tests/test_audit_runner.py
```
- git diff --stat:
```
 Forge/Contracts/physics.yaml    |   22 +
 Forge/evidence/audit_ledger.md  |   38 ++
 Forge/evidence/updatedifflog.md | 1438 +--------------------------------------
 app/main.py                     |    2 +
 app/services/audit_service.py   |   23 +
 5 files changed, 120 insertions(+), 1403 deletions(-)
```

## Test Run 2026-02-15T02:08:15Z
- Status: PASS
- Start: 2026-02-15T02:08:15Z
- End: 2026-02-15T02:08:29Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: b4a6987db4bfb57648d4cec42b81d47b0d6ce0d0
- import_sanity exit: 0
- compileall exit: 0
- pytest exit: 0
- git status -sb:
```
## master...origin/master
M  Forge/Contracts/physics.yaml
MM Forge/evidence/audit_ledger.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
M  Forge/evidence/updatedifflog.md
A  app/api/routers/audit.py
A  app/audit/__main__.py
AM app/audit/runner.py
M  app/main.py
M  app/services/audit_service.py
AM tests/test_audit_runner.py
```
- git diff --stat:
```
 Forge/evidence/audit_ledger.md | 80 ++++++++++++++++++++++++++++++++++++++++++
 app/audit/runner.py            | 19 +++++-----
 tests/test_audit_runner.py     |  5 +--
 3 files changed, 94 insertions(+), 10 deletions(-)
```

## Test Run 2026-02-15T02:21:05Z
- Status: PASS
- Start: 2026-02-15T02:21:05Z
- End: 2026-02-15T02:21:19Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
- compileall exit: 0
- pytest exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master...origin/master
 M Forge/evidence/updatedifflog.md
```
- git diff --stat:
```
 Forge/evidence/updatedifflog.md | 6757 +--------------------------------------
 1 file changed, 10 insertions(+), 6747 deletions(-)
```

## Test Run 2026-02-15T02:21:41Z
- Status: PASS
- Start: 2026-02-15T02:21:41Z
- End: 2026-02-15T02:21:54Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
- import_sanity exit: 0
- pytest exit: 0
- compileall exit: 0
- git status -sb:
```
## master...origin/master
 M Forge/evidence/test_runs.md
 M Forge/evidence/test_runs_latest.md
 M Forge/evidence/updatedifflog.md
```
- git diff --stat:
```
 Forge/evidence/test_runs.md        |   21 +
 Forge/evidence/test_runs_latest.md |   26 +-
 Forge/evidence/updatedifflog.md    | 6757 +-----------------------------------
 3 files changed, 38 insertions(+), 6766 deletions(-)
```

## Test Run 2026-02-15T02:26:18Z
- Status: PASS
- Start: 2026-02-15T02:26:18Z
- End: 2026-02-15T02:26:31Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
- compileall exit: 0
- pytest exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master...origin/master
M  Forge/evidence/audit_ledger.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
 M Forge/evidence/updatedifflog.md
M  Forge/scripts/run_audit.ps1
M  app/audit/runner.py
M  tests/test_audit_runner.py
```
- git diff --stat:
```
 Forge/evidence/updatedifflog.md | 13442 +++++++++++++++++++-------------------
 1 file changed, 6824 insertions(+), 6618 deletions(-)
```

## Test Run 2026-02-15T02:28:50Z
- Status: PASS
- Start: 2026-02-15T02:28:50Z
- End: 2026-02-15T02:29:04Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 11cee3483d7b9f4bf8e66f7af1ff443e01654e4b
- compileall exit: 0
- pytest exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master...origin/master
M  Forge/evidence/audit_ledger.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
M  Forge/evidence/updatedifflog.md
M  Forge/scripts/overwrite_diff_log.ps1
M  Forge/scripts/run_audit.ps1
M  app/audit/runner.py
M  tests/test_audit_runner.py
```
- git diff --stat:
```

```

## Test Run 2026-02-15T02:50:18Z
- Status: PASS
- Start: 2026-02-15T02:50:18Z
- End: 2026-02-15T02:50:31Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
- pytest exit: 0
- import_sanity exit: 0
- compileall exit: 0
- git status -sb:
```
## master...origin/master
 M Forge/Contracts/physics.yaml
 M Forge/Contracts/schema.md
 M app/config.py
 M app/main.py
?? app/api/routers/projects.py
?? app/clients/llm_client.py
?? app/repos/project_repo.py
?? app/services/project_service.py
?? app/templates/
?? db/migrations/002_projects.sql
?? tests/test_llm_client.py
?? tests/test_project_service.py
?? tests/test_projects_router.py
```
- git diff --stat:
```
 Forge/Contracts/physics.yaml | 121 +++++++++++++++++++++++++++++++++++++++++++
 Forge/Contracts/schema.md    |  54 +++++++++++++++++++
 app/config.py                |   4 ++
 app/main.py                  |   4 +-
 4 files changed, 182 insertions(+), 1 deletion(-)
```

## Test Run 2026-02-15T02:55:09Z
- Status: PASS
- Start: 2026-02-15T02:55:09Z
- End: 2026-02-15T02:55:23Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
- compileall exit: 0
- import_sanity exit: 0
- pytest exit: 0
- git status -sb:
```
## master...origin/master
M  Forge/Contracts/physics.yaml
M  Forge/Contracts/schema.md
M  Forge/evidence/audit_ledger.md
M  Forge/evidence/test_runs.md
M  Forge/evidence/test_runs_latest.md
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
- git diff --stat:
```

```

## Test Run 2026-02-15T02:55:23Z
- Status: PASS
- Start: 2026-02-15T02:55:23Z
- End: 2026-02-15T02:55:37Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: f45371e9d2cd5f51b9bc3f070b9bc4ef289b5463
- import_sanity exit: 0
- pytest exit: 0
- compileall exit: 0
- git status -sb:
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
- git diff --stat:
```
 Forge/evidence/test_runs.md        | 48 ++++++++++++++++++++++++++++++++
 Forge/evidence/test_runs_latest.md | 56 +++++++++++++++++++++++---------------
 2 files changed, 82 insertions(+), 22 deletions(-)
```

## Test Run 2026-02-15T03:22:59Z
- Status: PASS
- Start: 2026-02-15T03:22:59Z
- End: 2026-02-15T03:23:01Z
- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
- Branch: master
- HEAD: 33db3562f5899b1190453b53b1fdb21f05aabddf
- compileall exit: 0
- import_sanity exit: 0
- git status -sb:
```
## master...origin/master [ahead 1]
M  Forge/Contracts/physics.yaml
M  Forge/Contracts/schema.md
 M Forge/evidence/updatedifflog.md
 M Forge/scripts/watch_audit.ps1
A  app/api/routers/builds.py
A  app/clients/agent_client.py
M  app/config.py
M  app/main.py
A  app/repos/build_repo.py
A  app/services/build_service.py
A  db/migrations/003_builds.sql
A  tests/test_agent_client.py
A  tests/test_build_repo.py
A  tests/test_build_service.py
A  tests/test_builds_router.py
```
- git diff --stat:
```
 Forge/evidence/updatedifflog.md | 3015 ++-------------------------------------
 Forge/scripts/watch_audit.ps1   |    2 +-
 2 files changed, 96 insertions(+), 2921 deletions(-)
```

