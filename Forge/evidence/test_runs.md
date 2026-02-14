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

