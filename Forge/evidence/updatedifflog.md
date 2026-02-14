# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-14T23:42:00+00:00
- Branch: master
- HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
- BASE_HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- Verification Evidence: Static analysis clean, Runtime endpoints tested, Behavior assertions pass, Contract boundaries enforced
- Phase 4 Dashboard and Real-Time: WebSocket manager, health badges, app shell, skeleton loaders, toast notifications, empty states
- Backend: ConnectionManager, WS endpoint with JWT auth, repo health via LATERAL JOIN, audit broadcast, check_summary aggregation
- Frontend: AppShell with responsive sidebar, Skeleton/SkeletonCard/SkeletonRow, EmptyState, ToastContext, useWebSocket with auto-reconnect
- Pages: Dashboard with live WS updates and skeletons, CommitTimeline with computed health and live refresh, AuditDetail with skeleton loading
- Tests: 56 backend (14 new for WS and health), 15 frontend (6 new for Skeleton and EmptyState)

## Files Changed (staged)
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md
- app/api/routers/ws.py
- app/main.py
- app/repos/audit_repo.py
- app/repos/repo_repo.py
- app/services/audit_service.py
- app/services/repo_service.py
- app/ws_manager.py
- tests/test_repo_health.py
- tests/test_repos_router.py
- tests/test_ws_manager.py
- tests/test_ws_router.py
- web/src/App.tsx
- web/src/__tests__/App.test.tsx
- web/src/components/AppShell.tsx
- web/src/components/CommitRow.tsx
- web/src/components/EmptyState.tsx
- web/src/components/Skeleton.tsx
- web/src/context/ToastContext.tsx
- web/src/hooks/useWebSocket.ts
- web/src/index.css
- web/src/pages/AuditDetail.tsx
- web/src/pages/CommitTimeline.tsx
- web/src/pages/Dashboard.tsx

## git status -sb
    ## master
    M  Forge/evidence/test_runs.md
    M  Forge/evidence/test_runs_latest.md
    M  Forge/evidence/updatedifflog.md
    A  app/api/routers/ws.py
    M  app/main.py
    M  app/repos/audit_repo.py
    M  app/repos/repo_repo.py
    M  app/services/audit_service.py
    M  app/services/repo_service.py
    A  app/ws_manager.py
    A  tests/test_repo_health.py
    M  tests/test_repos_router.py
    A  tests/test_ws_manager.py
    A  tests/test_ws_router.py
    M  web/src/App.tsx
    M  web/src/__tests__/App.test.tsx
    A  web/src/components/AppShell.tsx
    M  web/src/components/CommitRow.tsx
    A  web/src/components/EmptyState.tsx
    A  web/src/components/Skeleton.tsx
    A  web/src/context/ToastContext.tsx
    A  web/src/hooks/useWebSocket.ts
    M  web/src/index.css
    M  web/src/pages/AuditDetail.tsx
    M  web/src/pages/CommitTimeline.tsx
    M  web/src/pages/Dashboard.tsx

## Minimal Diff Hunks
    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    index 9d32af7..6cb3261 100644
    --- a/Forge/evidence/test_runs.md
    +++ b/Forge/evidence/test_runs.md
    @@ -300,3 +300,113 @@ M  web/src/pages/Dashboard.tsx
     
     ```
     
    +## Test Run 2026-02-14T23:24:21Z
    +- Status: PASS
    +- Start: 2026-02-14T23:24:21Z
    +- End: 2026-02-14T23:24:23Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- git status -sb:
    +```
    +## master
    + M Forge/scripts/watch_audit.ps1
    + M app/main.py
    + M app/repos/audit_repo.py
    + M app/repos/repo_repo.py
    + M app/services/audit_service.py
    + M app/services/repo_service.py
    + M tests/test_repos_router.py
    + M web/src/App.tsx
    + M web/src/__tests__/App.test.tsx
    + M web/src/components/CommitRow.tsx
    + M web/src/index.css
    + M web/src/pages/AuditDetail.tsx
    + M web/src/pages/CommitTimeline.tsx
    + M web/src/pages/Dashboard.tsx
    +?? app/api/routers/ws.py
    +?? app/ws_manager.py
    +?? tests/test_repo_health.py
    +?? tests/test_ws_manager.py
    +?? tests/test_ws_router.py
    +?? web/src/components/AppShell.tsx
    +?? web/src/components/EmptyState.tsx
    +?? web/src/components/Skeleton.tsx
    +?? web/src/context/ToastContext.tsx
    +?? web/src/hooks/
    +```
    +- git diff --stat:
    +```
    + Forge/scripts/watch_audit.ps1    |   9 +++
    + app/main.py                      |   2 +
    + app/repos/audit_repo.py          |  16 ++--
    + app/repos/repo_repo.py           |  38 ++++++++++
    + app/services/audit_service.py    |  18 +++++
    + app/services/repo_service.py     |  28 +++++--
    + tests/test_repos_router.py       |   8 +-
    + web/src/App.tsx                  |  73 ++++++++----------
    + web/src/__tests__/App.test.tsx   |  41 +++++++++-
    + web/src/components/CommitRow.tsx |   8 +-
    + web/src/index.css                |   5 ++
    + web/src/pages/AuditDetail.tsx    | 156 ++++++++++++++++++++++-----------------
    + web/src/pages/CommitTimeline.tsx | 143 +++++++++++++++++++++--------------
    + web/src/pages/Dashboard.tsx      |  90 +++++++++-------------
    + 14 files changed, 405 insertions(+), 230 deletions(-)
    +```
    +
    +## Test Run 2026-02-14T23:39:46Z
    +- Status: PASS
    +- Start: 2026-02-14T23:39:46Z
    +- End: 2026-02-14T23:39:48Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: master
    +- HEAD: 007855acf36050e87fe70e837aae2b6c6e5716fa
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- git status -sb:
    +```
    +## master
    +```
    +- git diff --stat:
    +```
    +
    +```
    +
    +## Test Run 2026-02-14T23:39:52Z
    +- Status: PASS
    +- Start: 2026-02-14T23:39:52Z
    +- End: 2026-02-14T23:39:53Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: git unavailable
    +- HEAD: git unavailable
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- git status -sb:
    +```
    +git unavailable
    +```
    +- git diff --stat:
    +```
    +git unavailable
    +```
    +
    +## Test Run 2026-02-14T23:40:07Z
    +- Status: PASS
    +- Start: 2026-02-14T23:40:07Z
    +- End: 2026-02-14T23:40:08Z
    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +- Branch: git unavailable
    +- HEAD: git unavailable
    +- compileall exit: 0
    +- import_sanity exit: 0
    +- git status -sb:
    +```
    +git unavailable
    +```
    +- git diff --stat:
    +```
    +git unavailable
    +```
    +
    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    index 463f936..a856761 100644
    --- a/Forge/evidence/test_runs_latest.md
    +++ b/Forge/evidence/test_runs_latest.md
    @@ -1,44 +1,17 @@
    -Status: PASS
    -Start: 2026-02-14T23:09:21Z
    -End: 2026-02-14T23:09:23Z
    -Branch: master
    -HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
    +´╗┐Status: PASS
    +Start: 2026-02-14T23:40:07Z
    +End: 2026-02-14T23:40:08Z
    +Branch: git unavailable
    +HEAD: git unavailable
     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    -import_sanity exit: 0
    -pytest exit: 0
     compileall exit: 0
    +import_sanity exit: 0
     git status -sb:
     ```
    -## master
    -M  Forge/Contracts/builder_contract.md
    -M  Forge/evidence/audit_ledger.md
    -M  Forge/evidence/test_runs.md
    -M  Forge/evidence/test_runs_latest.md
    -M  Forge/evidence/updatedifflog.md
    -M  Forge/scripts/watch_audit.ps1
    -M  app/api/routers/repos.py
    -A  app/api/routers/webhooks.py
    -M  app/audit/__init__.py
    -A  app/audit/engine.py
    -M  app/clients/github_client.py
    -M  app/main.py
    -A  app/repos/audit_repo.py
    -A  app/services/audit_service.py
    -A  app/webhooks.py
    -A  tests/test_audit_engine.py
    -A  tests/test_webhook_router.py
    -A  tests/test_webhooks.py
    -M  web/src/App.tsx
    -M  web/src/__tests__/App.test.tsx
    -A  web/src/components/CheckResultCard.tsx
    -A  web/src/components/CommitRow.tsx
    -A  web/src/components/ResultBadge.tsx
    -A  web/src/pages/AuditDetail.tsx
    -A  web/src/pages/CommitTimeline.tsx
    -M  web/src/pages/Dashboard.tsx
    +git unavailable
     ```
     git diff --stat:
     ```
    -
    +git unavailable
     ```
     
    diff --git a/Forge/evidence/updatedifflog.md b/Forge/evidence/updatedifflog.md
    index dee9ffb..5b06b45 100644
    --- a/Forge/evidence/updatedifflog.md
    +++ b/Forge/evidence/updatedifflog.md
    @@ -1,48 +1,2068 @@
    -# Phase 3 -- Audit Engine: Diff Log
    +´╗┐# Diff Log (overwrite each cycle)
     
    -## Verification Evidence
    -- Static: PASS (compileall clean, import app.main OK)
    -- Runtime: PASS (uvicorn boots on port 8004, /health 200, /webhooks/github 401 on bad signature)
    -- Behavior: PASS (pytest 42/42, vitest 9/9)
    -- Contract: PASS (physics webhook + audit endpoints matched, boundaries respected, schema audit tables match migration)
    +## Cycle Metadata
    +- Timestamp: 2026-02-14T23:32:38+00:00
    +- Branch: master
    +- HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
    +- BASE_HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
    +- Diff basis: staged
     
    -## Files Changed (13 new, 6 modified)
    +## Cycle Status
    +- Status: COMPLETE
     
    -### New Files
    -- app/webhooks.py
    -- app/audit/engine.py
    +## Summary
    +- Verification Evidence: Static analysis clean, Runtime endpoints tested, Behavior assertions pass, Contract boundaries enforced
    +- Phase 4 Dashboard and Real-Time: WebSocket manager, health badges, app shell, skeleton loaders, toast notifications, empty states
    +- Backend: ConnectionManager, WS endpoint with JWT auth, repo health computation via LATERAL JOIN, audit broadcast on completion, check_summary aggregation
    +- Frontend: AppShell with responsive sidebar, Skeleton/SkeletonCard/SkeletonRow components, EmptyState component, ToastContext provider, useWebSocket hook with auto-reconnect
    +- Pages: Dashboard with live WS updates and skeletons, CommitTimeline with computed health and live refresh, AuditDetail with skeleton loading
    +- Tests: 56 backend (14 new for WS and health), 15 frontend (6 new for Skeleton and EmptyState)
    +
    +## Files Changed (staged)
    +- Forge/evidence/test_runs.md
    +- Forge/evidence/test_runs_latest.md
    +- app/api/routers/ws.py
    +- app/main.py
     - app/repos/audit_repo.py
    +- app/repos/repo_repo.py
     - app/services/audit_service.py
    -- app/api/routers/webhooks.py
    -- web/src/components/ResultBadge.tsx
    +- app/services/repo_service.py
    +- app/ws_manager.py
    +- tests/test_repo_health.py
    +- tests/test_repos_router.py
    +- tests/test_ws_manager.py
    +- tests/test_ws_router.py
    +- web/src/App.tsx
    +- web/src/__tests__/App.test.tsx
    +- web/src/components/AppShell.tsx
     - web/src/components/CommitRow.tsx
    -- web/src/components/CheckResultCard.tsx
    -- web/src/pages/CommitTimeline.tsx
    +- web/src/components/EmptyState.tsx
    +- web/src/components/Skeleton.tsx
    +- web/src/context/ToastContext.tsx
    +- web/src/hooks/useWebSocket.ts
    +- web/src/index.css
     - web/src/pages/AuditDetail.tsx
    -- tests/test_audit_engine.py
    -- tests/test_webhooks.py
    -- tests/test_webhook_router.py
    -
    -### Modified Files
    -- app/api/routers/repos.py
    -- app/clients/github_client.py
    -- app/main.py
    -- web/src/App.tsx
    +- web/src/pages/CommitTimeline.tsx
     - web/src/pages/Dashboard.tsx
    -- web/src/__tests__/App.test.tsx
    -- app/audit/__init__.py
    -- Forge/Contracts/builder_contract.md
    -- Forge/evidence/audit_ledger.md
    -- Forge/evidence/test_runs.md
    -- Forge/evidence/test_runs_latest.md
    -- Forge/evidence/updatedifflog.md
    -- Forge/scripts/watch_audit.ps1
     
    -## Summary
    -- Webhook receiver with hashlib-only HMAC-SHA256 signature verification (RFC 2104)
    -- Pure audit engine with A4 boundary compliance, A9 dependency gate, W1 secrets scan
    -- Audit DB layer for audit_runs and audit_checks tables
    -- Audit service orchestrating webhook push events through to stored results
    -- Frontend commit timeline (paginated) and audit detail views with result badges
    -- Added audit listing and detail endpoints to repos router
    +## git status -sb
    +    ## master
    +    M  Forge/evidence/test_runs.md
    +    M  Forge/evidence/test_runs_latest.md
    +     M Forge/evidence/updatedifflog.md
    +    A  app/api/routers/ws.py
    +    M  app/main.py
    +    M  app/repos/audit_repo.py
    +    M  app/repos/repo_repo.py
    +    M  app/services/audit_service.py
    +    M  app/services/repo_service.py
    +    A  app/ws_manager.py
    +    A  tests/test_repo_health.py
    +    M  tests/test_repos_router.py
    +    A  tests/test_ws_manager.py
    +    A  tests/test_ws_router.py
    +    M  web/src/App.tsx
    +    M  web/src/__tests__/App.test.tsx
    +    A  web/src/components/AppShell.tsx
    +    M  web/src/components/CommitRow.tsx
    +    A  web/src/components/EmptyState.tsx
    +    A  web/src/components/Skeleton.tsx
    +    A  web/src/context/ToastContext.tsx
    +    A  web/src/hooks/useWebSocket.ts
    +    M  web/src/index.css
    +    M  web/src/pages/AuditDetail.tsx
    +    M  web/src/pages/CommitTimeline.tsx
    +    M  web/src/pages/Dashboard.tsx
    +
    +## Minimal Diff Hunks
    +    diff --git a/Forge/evidence/test_runs.md b/Forge/evidence/test_runs.md
    +    index 9d32af7..7ec6367 100644
    +    --- a/Forge/evidence/test_runs.md
    +    +++ b/Forge/evidence/test_runs.md
    +    @@ -300,3 +300,59 @@ M  web/src/pages/Dashboard.tsx
    +     
    +     ```
    +     
    +    +## Test Run 2026-02-14T23:24:21Z
    +    +- Status: PASS
    +    +- Start: 2026-02-14T23:24:21Z
    +    +- End: 2026-02-14T23:24:23Z
    +    +- Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +    +- Branch: master
    +    +- HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
    +    +- compileall exit: 0
    +    +- import_sanity exit: 0
    +    +- git status -sb:
    +    +```
    +    +## master
    +    + M Forge/scripts/watch_audit.ps1
    +    + M app/main.py
    +    + M app/repos/audit_repo.py
    +    + M app/repos/repo_repo.py
    +    + M app/services/audit_service.py
    +    + M app/services/repo_service.py
    +    + M tests/test_repos_router.py
    +    + M web/src/App.tsx
    +    + M web/src/__tests__/App.test.tsx
    +    + M web/src/components/CommitRow.tsx
    +    + M web/src/index.css
    +    + M web/src/pages/AuditDetail.tsx
    +    + M web/src/pages/CommitTimeline.tsx
    +    + M web/src/pages/Dashboard.tsx
    +    +?? app/api/routers/ws.py
    +    +?? app/ws_manager.py
    +    +?? tests/test_repo_health.py
    +    +?? tests/test_ws_manager.py
    +    +?? tests/test_ws_router.py
    +    +?? web/src/components/AppShell.tsx
    +    +?? web/src/components/EmptyState.tsx
    +    +?? web/src/components/Skeleton.tsx
    +    +?? web/src/context/ToastContext.tsx
    +    +?? web/src/hooks/
    +    +```
    +    +- git diff --stat:
    +    +```
    +    + Forge/scripts/watch_audit.ps1    |   9 +++
    +    + app/main.py                      |   2 +
    +    + app/repos/audit_repo.py          |  16 ++--
    +    + app/repos/repo_repo.py           |  38 ++++++++++
    +    + app/services/audit_service.py    |  18 +++++
    +    + app/services/repo_service.py     |  28 +++++--
    +    + tests/test_repos_router.py       |   8 +-
    +    + web/src/App.tsx                  |  73 ++++++++----------
    +    + web/src/__tests__/App.test.tsx   |  41 +++++++++-
    +    + web/src/components/CommitRow.tsx |   8 +-
    +    + web/src/index.css                |   5 ++
    +    + web/src/pages/AuditDetail.tsx    | 156 ++++++++++++++++++++++-----------------
    +    + web/src/pages/CommitTimeline.tsx | 143 +++++++++++++++++++++--------------
    +    + web/src/pages/Dashboard.tsx      |  90 +++++++++-------------
    +    + 14 files changed, 405 insertions(+), 230 deletions(-)
    +    +```
    +    +
    +    diff --git a/Forge/evidence/test_runs_latest.md b/Forge/evidence/test_runs_latest.md
    +    index 463f936..b2a3699 100644
    +    --- a/Forge/evidence/test_runs_latest.md
    +    +++ b/Forge/evidence/test_runs_latest.md
    +    @@ -1,44 +1,55 @@
    +    -Status: PASS
    +    -Start: 2026-02-14T23:09:21Z
    +    -End: 2026-02-14T23:09:23Z
    +    +┬┤ÔòùÔöÉStatus: PASS
    +    +Start: 2026-02-14T23:24:21Z
    +    +End: 2026-02-14T23:24:23Z
    +     Branch: master
    +    -HEAD: 39afcadcc0d0468cd71d918168e978f7d39daa49
    +    +HEAD: 12f5c11d91044c2a1b030bdd16444f85c1090444
    +     Runtime: Z:\ForgeCollection\ForgeGuard\.venv\Scripts\python.exe
    +    -import_sanity exit: 0
    +    -pytest exit: 0
    +     compileall exit: 0
    +    +import_sanity exit: 0
    +     git status -sb:
    +     ```
    +     ## master
    +    -M  Forge/Contracts/builder_contract.md
    +    -M  Forge/evidence/audit_ledger.md
    +    -M  Forge/evidence/test_runs.md
    +    -M  Forge/evidence/test_runs_latest.md
    +    -M  Forge/evidence/updatedifflog.md
    +    -M  Forge/scripts/watch_audit.ps1
    +    -M  app/api/routers/repos.py
    +    -A  app/api/routers/webhooks.py
    +    -M  app/audit/__init__.py
    +    -A  app/audit/engine.py
    +    -M  app/clients/github_client.py
    +    -M  app/main.py
    +    -A  app/repos/audit_repo.py
    +    -A  app/services/audit_service.py
    +    -A  app/webhooks.py
    +    -A  tests/test_audit_engine.py
    +    -A  tests/test_webhook_router.py
    +    -A  tests/test_webhooks.py
    +    -M  web/src/App.tsx
    +    -M  web/src/__tests__/App.test.tsx
    +    -A  web/src/components/CheckResultCard.tsx
    +    -A  web/src/components/CommitRow.tsx
    +    -A  web/src/components/ResultBadge.tsx
    +    -A  web/src/pages/AuditDetail.tsx
    +    -A  web/src/pages/CommitTimeline.tsx
    +    -M  web/src/pages/Dashboard.tsx
    +    + M Forge/scripts/watch_audit.ps1
    +    + M app/main.py
    +    + M app/repos/audit_repo.py
    +    + M app/repos/repo_repo.py
    +    + M app/services/audit_service.py
    +    + M app/services/repo_service.py
    +    + M tests/test_repos_router.py
    +    + M web/src/App.tsx
    +    + M web/src/__tests__/App.test.tsx
    +    + M web/src/components/CommitRow.tsx
    +    + M web/src/index.css
    +    + M web/src/pages/AuditDetail.tsx
    +    + M web/src/pages/CommitTimeline.tsx
    +    + M web/src/pages/Dashboard.tsx
    +    +?? app/api/routers/ws.py
    +    +?? app/ws_manager.py
    +    +?? tests/test_repo_health.py
    +    +?? tests/test_ws_manager.py
    +    +?? tests/test_ws_router.py
    +    +?? web/src/components/AppShell.tsx
    +    +?? web/src/components/EmptyState.tsx
    +    +?? web/src/components/Skeleton.tsx
    +    +?? web/src/context/ToastContext.tsx
    +    +?? web/src/hooks/
    +     ```
    +     git diff --stat:
    +     ```
    +    -
    +    + Forge/scripts/watch_audit.ps1    |   9 +++
    +    + app/main.py                      |   2 +
    +    + app/repos/audit_repo.py          |  16 ++--
    +    + app/repos/repo_repo.py           |  38 ++++++++++
    +    + app/services/audit_service.py    |  18 +++++
    +    + app/services/repo_service.py     |  28 +++++--
    +    + tests/test_repos_router.py       |   8 +-
    +    + web/src/App.tsx                  |  73 ++++++++----------
    +    + web/src/__tests__/App.test.tsx   |  41 +++++++++-
    +    + web/src/components/CommitRow.tsx |   8 +-
    +    + web/src/index.css                |   5 ++
    +    + web/src/pages/AuditDetail.tsx    | 156 ++++++++++++++++++++++-----------------
    +    + web/src/pages/CommitTimeline.tsx | 143 +++++++++++++++++++++--------------
    +    + web/src/pages/Dashboard.tsx      |  90 +++++++++-------------
    +    + 14 files changed, 405 insertions(+), 230 deletions(-)
    +     ```
    +     
    +    diff --git a/app/api/routers/ws.py b/app/api/routers/ws.py
    +    new file mode 100644
    +    index 0000000..e3a3f61
    +    --- /dev/null
    +    +++ b/app/api/routers/ws.py
    +    @@ -0,0 +1,43 @@
    +    +"""WebSocket router -- real-time audit result updates."""
    +    +
    +    +import jwt as pyjwt
    +    +from fastapi import APIRouter, WebSocket, WebSocketDisconnect
    +    +
    +    +from app.auth import decode_token
    +    +from app.ws_manager import manager
    +    +
    +    +router = APIRouter(tags=["websocket"])
    +    +
    +    +
    +    +@router.websocket("/ws")
    +    +async def websocket_endpoint(websocket: WebSocket) -> None:
    +    +    """WebSocket endpoint for real-time audit updates.
    +    +
    +    +    Auth via query param: /ws?token=<jwt>
    +    +    Server sends messages of type "audit_update" with AuditRunSummary payload.
    +    +    """
    +    +    token = websocket.query_params.get("token")
    +    +    if not token:
    +    +        await websocket.close(code=4001, reason="Missing token")
    +    +        return
    +    +
    +    +    try:
    +    +        payload = decode_token(token)
    +    +    except (pyjwt.ExpiredSignatureError, pyjwt.PyJWTError):
    +    +        await websocket.close(code=4001, reason="Invalid token")
    +    +        return
    +    +
    +    +    user_id = payload.get("sub")
    +    +    if not user_id:
    +    +        await websocket.close(code=4001, reason="Invalid token payload")
    +    +        return
    +    +
    +    +    await websocket.accept()
    +    +    await manager.connect(user_id, websocket)
    +    +
    +    +    try:
    +    +        while True:
    +    +            # Keep connection alive; ignore client messages
    +    +            await websocket.receive_text()
    +    +    except WebSocketDisconnect:
    +    +        await manager.disconnect(user_id, websocket)
    +    diff --git a/app/main.py b/app/main.py
    +    index b012194..4d80f1b 100644
    +    --- a/app/main.py
    +    +++ b/app/main.py
    +    @@ -9,6 +9,7 @@ from app.api.routers.auth import router as auth_router
    +     from app.api.routers.health import router as health_router
    +     from app.api.routers.repos import router as repos_router
    +     from app.api.routers.webhooks import router as webhooks_router
    +    +from app.api.routers.ws import router as ws_router
    +     from app.config import settings
    +     from app.repos.db import close_pool
    +     
    +    @@ -41,6 +42,7 @@ def create_app() -> FastAPI:
    +         application.include_router(auth_router)
    +         application.include_router(repos_router)
    +         application.include_router(webhooks_router)
    +    +    application.include_router(ws_router)
    +         return application
    +     
    +     
    +    diff --git a/app/repos/audit_repo.py b/app/repos/audit_repo.py
    +    index f979476..f18d94c 100644
    +    --- a/app/repos/audit_repo.py
    +    +++ b/app/repos/audit_repo.py
    +    @@ -95,11 +95,17 @@ async def get_audit_runs_by_repo(
    +     
    +         rows = await pool.fetch(
    +             """
    +    -        SELECT id, repo_id, commit_sha, commit_message, commit_author, branch,
    +    -               status, overall_result, started_at, completed_at, files_checked, created_at
    +    -        FROM audit_runs
    +    -        WHERE repo_id = $1
    +    -        ORDER BY created_at DESC
    +    +        SELECT a.id, a.repo_id, a.commit_sha, a.commit_message, a.commit_author, a.branch,
    +    +               a.status, a.overall_result, a.started_at, a.completed_at, a.files_checked, a.created_at,
    +    +               cs.check_summary
    +    +        FROM audit_runs a
    +    +        LEFT JOIN LATERAL (
    +    +            SELECT string_agg(c.check_code || ':' || c.result, ' ' ORDER BY c.created_at) AS check_summary
    +    +            FROM audit_checks c
    +    +            WHERE c.audit_run_id = a.id
    +    +        ) cs ON true
    +    +        WHERE a.repo_id = $1
    +    +        ORDER BY a.created_at DESC
    +             LIMIT $2 OFFSET $3
    +             """,
    +             repo_id,
    +    diff --git a/app/repos/repo_repo.py b/app/repos/repo_repo.py
    +    index d5ce62c..0d6c813 100644
    +    --- a/app/repos/repo_repo.py
    +    +++ b/app/repos/repo_repo.py
    +    @@ -88,6 +88,44 @@ async def delete_repo(repo_id: UUID) -> bool:
    +         return result == "DELETE 1"
    +     
    +     
    +    +async def get_repos_with_health(user_id: UUID) -> list[dict]:
    +    +    """Fetch repos with recent audit health data for a user.
    +    +
    +    +    Returns each repo with last_audit_at and pass/total counts from the
    +    +    10 most recent completed audit runs.
    +    +    """
    +    +    pool = await get_pool()
    +    +    rows = await pool.fetch(
    +    +        """
    +    +        SELECT
    +    +            r.id, r.user_id, r.github_repo_id, r.full_name,
    +    +            r.default_branch, r.webhook_id, r.webhook_active,
    +    +            r.created_at, r.updated_at,
    +    +            h.last_audit_at,
    +    +            h.pass_count,
    +    +            h.total_count
    +    +        FROM repos r
    +    +        LEFT JOIN LATERAL (
    +    +            SELECT
    +    +                max(a.completed_at) AS last_audit_at,
    +    +                count(*) FILTER (WHERE a.overall_result = 'PASS') AS pass_count,
    +    +                count(*) AS total_count
    +    +            FROM (
    +    +                SELECT overall_result, completed_at
    +    +                FROM audit_runs
    +    +                WHERE repo_id = r.id AND status = 'completed'
    +    +                ORDER BY created_at DESC
    +    +                LIMIT 10
    +    +            ) a
    +    +        ) h ON true
    +    +        WHERE r.user_id = $1
    +    +        ORDER BY r.created_at DESC
    +    +        """,
    +    +        user_id,
    +    +    )
    +    +    return [dict(r) for r in rows]
    +    +
    +    +
    +     async def update_webhook(
    +         repo_id: UUID,
    +         webhook_id: int | None,
    +    diff --git a/app/services/audit_service.py b/app/services/audit_service.py
    +    index cd2a72e..aebb1bc 100644
    +    --- a/app/services/audit_service.py
    +    +++ b/app/services/audit_service.py
    +    @@ -12,6 +12,7 @@ from app.repos.audit_repo import (
    +     )
    +     from app.repos.repo_repo import get_repo_by_github_id
    +     from app.repos.user_repo import get_user_by_id
    +    +from app.ws_manager import manager as ws_manager
    +     
    +     
    +     async def process_push_event(payload: dict) -> dict | None:
    +    @@ -134,6 +135,22 @@ async def process_push_event(payload: dict) -> dict | None:
    +                 files_checked=len(files),
    +             )
    +     
    +    +        # Broadcast real-time update via WebSocket
    +    +        user_id_str = str(repo["user_id"])
    +    +        await ws_manager.broadcast_audit_update(user_id_str, {
    +    +            "id": str(audit_run["id"]),
    +    +            "repo_id": str(repo["id"]),
    +    +            "commit_sha": commit_sha,
    +    +            "commit_message": commit_message,
    +    +            "commit_author": commit_author,
    +    +            "branch": branch,
    +    +            "status": "completed",
    +    +            "overall_result": overall,
    +    +            "started_at": audit_run["started_at"].isoformat() if audit_run.get("started_at") else None,
    +    +            "completed_at": None,
    +    +            "files_checked": len(files),
    +    +        })
    +    +
    +         except Exception:
    +             await update_audit_run(
    +                 audit_run_id=audit_run["id"],
    +    @@ -174,6 +191,7 @@ async def get_repo_audits(
    +                 "started_at": item["started_at"].isoformat() if item["started_at"] else None,
    +                 "completed_at": item["completed_at"].isoformat() if item["completed_at"] else None,
    +                 "files_checked": item["files_checked"],
    +    +            "check_summary": item.get("check_summary"),
    +             })
    +         return result, total
    +     
    +    diff --git a/app/services/repo_service.py b/app/services/repo_service.py
    +    index 2784795..10f035b 100644
    +    --- a/app/services/repo_service.py
    +    +++ b/app/services/repo_service.py
    +    @@ -10,6 +10,7 @@ from app.repos.repo_repo import (
    +         get_repo_by_github_id,
    +         get_repo_by_id,
    +         get_repos_by_user,
    +    +    get_repos_with_health,
    +     )
    +     from app.repos.user_repo import get_user_by_id
    +     
    +    @@ -85,18 +86,35 @@ async def disconnect_repo(user_id: UUID, repo_id: UUID) -> None:
    +     
    +     
    +     async def list_connected_repos(user_id: UUID) -> list[dict]:
    +    -    """List all connected repos for a user with placeholder health data."""
    +    -    repos = await get_repos_by_user(user_id)
    +    +    """List all connected repos for a user with computed health data."""
    +    +    repos = await get_repos_with_health(user_id)
    +         result = []
    +         for repo in repos:
    +    +        total = repo.get("total_count") or 0
    +    +        pass_count = repo.get("pass_count") or 0
    +    +
    +    +        if total == 0:
    +    +            health = "pending"
    +    +            rate = None
    +    +        else:
    +    +            rate = pass_count / total
    +    +            if rate == 1.0:
    +    +                health = "green"
    +    +            elif rate >= 0.5:
    +    +                health = "yellow"
    +    +            else:
    +    +                health = "red"
    +    +
    +    +        last_audit = repo.get("last_audit_at")
    +    +
    +             result.append({
    +                 "id": str(repo["id"]),
    +                 "full_name": repo["full_name"],
    +                 "default_branch": repo["default_branch"],
    +                 "webhook_active": repo["webhook_active"],
    +    -            "health_score": "pending",
    +    -            "last_audit_at": None,
    +    -            "recent_pass_rate": None,
    +    +            "health_score": health,
    +    +            "last_audit_at": last_audit.isoformat() if last_audit else None,
    +    +            "recent_pass_rate": rate,
    +             })
    +         return result
    +     
    +    diff --git a/app/ws_manager.py b/app/ws_manager.py
    +    new file mode 100644
    +    index 0000000..627419e
    +    --- /dev/null
    +    +++ b/app/ws_manager.py
    +    @@ -0,0 +1,51 @@
    +    +"""WebSocket connection manager for real-time audit updates."""
    +    +
    +    +import asyncio
    +    +import json
    +    +from uuid import UUID
    +    +
    +    +
    +    +class ConnectionManager:
    +    +    """Manages active WebSocket connections keyed by user_id."""
    +    +
    +    +    def __init__(self) -> None:
    +    +        self._connections: dict[str, list] = {}  # user_id -> list of websockets
    +    +        self._lock = asyncio.Lock()
    +    +
    +    +    async def connect(self, user_id: str, websocket) -> None:  # noqa: ANN001
    +    +        """Register a WebSocket connection for a user."""
    +    +        async with self._lock:
    +    +            if user_id not in self._connections:
    +    +                self._connections[user_id] = []
    +    +            self._connections[user_id].append(websocket)
    +    +
    +    +    async def disconnect(self, user_id: str, websocket) -> None:  # noqa: ANN001
    +    +        """Remove a WebSocket connection for a user."""
    +    +        async with self._lock:
    +    +            conns = self._connections.get(user_id, [])
    +    +            if websocket in conns:
    +    +                conns.remove(websocket)
    +    +            if not conns:
    +    +                self._connections.pop(user_id, None)
    +    +
    +    +    async def send_to_user(self, user_id: str, data: dict) -> None:
    +    +        """Send a JSON message to all connections for a specific user."""
    +    +        async with self._lock:
    +    +            conns = list(self._connections.get(user_id, []))
    +    +        message = json.dumps(data, default=str)
    +    +        for ws in conns:
    +    +            try:
    +    +                await ws.send_text(message)
    +    +            except Exception:
    +    +                pass  # dead connection -- will be cleaned up on disconnect
    +    +
    +    +    async def broadcast_audit_update(self, user_id: str, audit_summary: dict) -> None:
    +    +        """Broadcast an audit_update event to the given user."""
    +    +        payload = {
    +    +            "type": "audit_update",
    +    +            "payload": audit_summary,
    +    +        }
    +    +        await self.send_to_user(user_id, payload)
    +    +
    +    +
    +    +manager = ConnectionManager()
    +    diff --git a/tests/test_repo_health.py b/tests/test_repo_health.py
    +    new file mode 100644
    +    index 0000000..e5a0e9b
    +    --- /dev/null
    +    +++ b/tests/test_repo_health.py
    +    @@ -0,0 +1,79 @@
    +    +"""Tests for repo service health score computation."""
    +    +
    +    +import pytest
    +    +from unittest.mock import AsyncMock, patch
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +@patch("app.services.repo_service.get_repos_with_health")
    +    +async def test_health_green_all_pass(mock_get):
    +    +    """All 10 audits passed -> green health, 1.0 rate."""
    +    +    mock_get.return_value = [{
    +    +        "id": "repo-1",
    +    +        "full_name": "org/repo",
    +    +        "default_branch": "main",
    +    +        "webhook_active": True,
    +    +        "total_count": 10,
    +    +        "pass_count": 10,
    +    +        "last_audit_at": None,
    +    +    }]
    +    +    from app.services.repo_service import list_connected_repos
    +    +    result = await list_connected_repos("user-1")
    +    +    assert result[0]["health_score"] == "green"
    +    +    assert result[0]["recent_pass_rate"] == 1.0
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +@patch("app.services.repo_service.get_repos_with_health")
    +    +async def test_health_red_low_pass(mock_get):
    +    +    """Less than half pass -> red health."""
    +    +    mock_get.return_value = [{
    +    +        "id": "repo-1",
    +    +        "full_name": "org/repo",
    +    +        "default_branch": "main",
    +    +        "webhook_active": True,
    +    +        "total_count": 10,
    +    +        "pass_count": 3,
    +    +        "last_audit_at": None,
    +    +    }]
    +    +    from app.services.repo_service import list_connected_repos
    +    +    result = await list_connected_repos("user-1")
    +    +    assert result[0]["health_score"] == "red"
    +    +    assert result[0]["recent_pass_rate"] == 0.3
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +@patch("app.services.repo_service.get_repos_with_health")
    +    +async def test_health_yellow_mixed(mock_get):
    +    +    """50-99% pass rate -> yellow health."""
    +    +    mock_get.return_value = [{
    +    +        "id": "repo-1",
    +    +        "full_name": "org/repo",
    +    +        "default_branch": "main",
    +    +        "webhook_active": True,
    +    +        "total_count": 10,
    +    +        "pass_count": 7,
    +    +        "last_audit_at": None,
    +    +    }]
    +    +    from app.services.repo_service import list_connected_repos
    +    +    result = await list_connected_repos("user-1")
    +    +    assert result[0]["health_score"] == "yellow"
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +@patch("app.services.repo_service.get_repos_with_health")
    +    +async def test_health_pending_no_audits(mock_get):
    +    +    """No audit runs -> pending health, null rate."""
    +    +    mock_get.return_value = [{
    +    +        "id": "repo-1",
    +    +        "full_name": "org/repo",
    +    +        "default_branch": "main",
    +    +        "webhook_active": True,
    +    +        "total_count": 0,
    +    +        "pass_count": 0,
    +    +        "last_audit_at": None,
    +    +    }]
    +    +    from app.services.repo_service import list_connected_repos
    +    +    result = await list_connected_repos("user-1")
    +    +    assert result[0]["health_score"] == "pending"
    +    +    assert result[0]["recent_pass_rate"] is None
    +    diff --git a/tests/test_repos_router.py b/tests/test_repos_router.py
    +    index 05e7fe3..c647569 100644
    +    --- a/tests/test_repos_router.py
    +    +++ b/tests/test_repos_router.py
    +    @@ -43,7 +43,7 @@ def _auth_header():
    +     
    +     
    +     @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +    -@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
    +    +@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
    +     def test_list_repos_returns_items(mock_get_repos, mock_get_user):
    +         mock_get_user.return_value = MOCK_USER
    +         mock_get_repos.return_value = [
    +    @@ -57,6 +57,9 @@ def test_list_repos_returns_items(mock_get_repos, mock_get_user):
    +                 "webhook_active": True,
    +                 "created_at": "2025-01-01T00:00:00Z",
    +                 "updated_at": "2025-01-01T00:00:00Z",
    +    +            "total_count": 5,
    +    +            "pass_count": 5,
    +    +            "last_audit_at": None,
    +             }
    +         ]
    +     
    +    @@ -66,10 +69,11 @@ def test_list_repos_returns_items(mock_get_repos, mock_get_user):
    +         assert "items" in data
    +         assert len(data["items"]) == 1
    +         assert data["items"][0]["full_name"] == "octocat/hello-world"
    +    +    assert data["items"][0]["health_score"] == "green"
    +     
    +     
    +     @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    +    -@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
    +    +@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
    +     def test_list_repos_empty(mock_get_repos, mock_get_user):
    +         mock_get_user.return_value = MOCK_USER
    +         mock_get_repos.return_value = []
    +    diff --git a/tests/test_ws_manager.py b/tests/test_ws_manager.py
    +    new file mode 100644
    +    index 0000000..441a076
    +    --- /dev/null
    +    +++ b/tests/test_ws_manager.py
    +    @@ -0,0 +1,90 @@
    +    +"""Tests for WebSocket connection manager."""
    +    +
    +    +import asyncio
    +    +
    +    +import pytest
    +    +
    +    +from app.ws_manager import ConnectionManager
    +    +
    +    +
    +    +class FakeWebSocket:
    +    +    """Fake WebSocket for testing send_text."""
    +    +
    +    +    def __init__(self):
    +    +        self.messages: list[str] = []
    +    +        self.closed = False
    +    +
    +    +    async def send_text(self, text: str) -> None:
    +    +        if self.closed:
    +    +            raise RuntimeError("WebSocket closed")
    +    +        self.messages.append(text)
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_connect_and_send():
    +    +    """Messages reach connected sockets."""
    +    +    mgr = ConnectionManager()
    +    +    ws = FakeWebSocket()
    +    +    await mgr.connect("user-1", ws)
    +    +    await mgr.send_to_user("user-1", {"hello": "world"})
    +    +    assert len(ws.messages) == 1
    +    +    assert '"hello"' in ws.messages[0]
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_disconnect_removes_socket():
    +    +    """After disconnect, no messages reach the socket."""
    +    +    mgr = ConnectionManager()
    +    +    ws = FakeWebSocket()
    +    +    await mgr.connect("user-1", ws)
    +    +    await mgr.disconnect("user-1", ws)
    +    +    await mgr.send_to_user("user-1", {"hello": "world"})
    +    +    assert len(ws.messages) == 0
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_multiple_connections():
    +    +    """Multiple sockets for one user all receive messages."""
    +    +    mgr = ConnectionManager()
    +    +    ws1 = FakeWebSocket()
    +    +    ws2 = FakeWebSocket()
    +    +    await mgr.connect("user-1", ws1)
    +    +    await mgr.connect("user-1", ws2)
    +    +    await mgr.send_to_user("user-1", {"data": 1})
    +    +    assert len(ws1.messages) == 1
    +    +    assert len(ws2.messages) == 1
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_send_to_nonexistent_user():
    +    +    """Sending to a user with no connections does not error."""
    +    +    mgr = ConnectionManager()
    +    +    await mgr.send_to_user("nobody", {"data": 1})
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_broadcast_audit_update():
    +    +    """broadcast_audit_update sends correctly typed message."""
    +    +    mgr = ConnectionManager()
    +    +    ws = FakeWebSocket()
    +    +    await mgr.connect("user-1", ws)
    +    +    await mgr.broadcast_audit_update("user-1", {"id": "abc", "status": "completed"})
    +    +    assert len(ws.messages) == 1
    +    +    import json
    +    +    msg = json.loads(ws.messages[0])
    +    +    assert msg["type"] == "audit_update"
    +    +    assert msg["payload"]["id"] == "abc"
    +    +
    +    +
    +    +@pytest.mark.asyncio
    +    +async def test_dead_socket_ignored():
    +    +    """Dead socket doesn't prevent messages to other sockets."""
    +    +    mgr = ConnectionManager()
    +    +    ws_dead = FakeWebSocket()
    +    +    ws_dead.closed = True
    +    +    ws_live = FakeWebSocket()
    +    +    await mgr.connect("user-1", ws_dead)
    +    +    await mgr.connect("user-1", ws_live)
    +    +    await mgr.send_to_user("user-1", {"x": 1})
    +    +    assert len(ws_live.messages) == 1
    +    +    assert len(ws_dead.messages) == 0
    +    diff --git a/tests/test_ws_router.py b/tests/test_ws_router.py
    +    new file mode 100644
    +    index 0000000..0ff6c1e
    +    --- /dev/null
    +    +++ b/tests/test_ws_router.py
    +    @@ -0,0 +1,56 @@
    +    +"""Tests for WebSocket router endpoint."""
    +    +
    +    +from unittest.mock import AsyncMock, patch
    +    +
    +    +import pytest
    +    +from fastapi.testclient import TestClient
    +    +
    +    +from app.main import create_app
    +    +
    +    +
    +    +@pytest.fixture
    +    +def client():
    +    +    app = create_app()
    +    +    return TestClient(app)
    +    +
    +    +
    +    +def _make_token_payload(user_id: str = "test-user-id"):
    +    +    return {"sub": user_id, "github_login": "testuser"}
    +    +
    +    +
    +    +def test_ws_rejects_missing_token(client):
    +    +    """WebSocket connection without token should be rejected."""
    +    +    with pytest.raises(Exception):
    +    +        with client.websocket_connect("/ws"):
    +    +            pass
    +    +
    +    +
    +    +def test_ws_rejects_invalid_token(client):
    +    +    """WebSocket connection with invalid token should be rejected."""
    +    +    with pytest.raises(Exception):
    +    +        with client.websocket_connect("/ws?token=badtoken"):
    +    +            pass
    +    +
    +    +
    +    +@patch("app.api.routers.ws.decode_token")
    +    +def test_ws_accepts_valid_token(mock_decode, client):
    +    +    """WebSocket connection with valid token should be accepted."""
    +    +    mock_decode.return_value = _make_token_payload()
    +    +    with client.websocket_connect("/ws?token=validtoken") as ws:
    +    +        # Connection established - no immediate message expected
    +    +        # Just verify it connected
    +    +        assert ws is not None
    +    +
    +    +
    +    +@patch("app.api.routers.ws.decode_token")
    +    +@patch("app.api.routers.ws.manager")
    +    +def test_ws_connects_and_disconnects(mock_manager, mock_decode, client):
    +    +    """WebSocket lifecycle: connect -> manager.connect called."""
    +    +    mock_decode.return_value = _make_token_payload("uid-123")
    +    +    mock_manager.connect = AsyncMock()
    +    +    mock_manager.disconnect = AsyncMock()
    +    +
    +    +    with client.websocket_connect("/ws?token=validtoken"):
    +    +        mock_manager.connect.assert_called_once()
    +    +        call_args = mock_manager.connect.call_args
    +    +        assert call_args[0][0] == "uid-123"
    +    diff --git a/web/src/App.tsx b/web/src/App.tsx
    +    index a7bc43a..b85d295 100644
    +    --- a/web/src/App.tsx
    +    +++ b/web/src/App.tsx
    +    @@ -5,6 +5,7 @@ import Dashboard from './pages/Dashboard';
    +     import CommitTimeline from './pages/CommitTimeline';
    +     import AuditDetailPage from './pages/AuditDetail';
    +     import { AuthProvider, useAuth } from './context/AuthContext';
    +    +import { ToastProvider } from './context/ToastContext';
    +     
    +     function ProtectedRoute({ children }: { children: React.ReactNode }) {
    +       const { token } = useAuth();
    +    @@ -12,52 +13,42 @@ function ProtectedRoute({ children }: { children: React.ReactNode }) {
    +       return <>{children}</>;
    +     }
    +     
    +    -function AppLayout({ children }: { children: React.ReactNode }) {
    +    -  return (
    +    -    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
    +    -      {children}
    +    -    </div>
    +    -  );
    +    -}
    +    -
    +     function App() {
    +       return (
    +         <AuthProvider>
    +    -      <BrowserRouter>
    +    -        <Routes>
    +    -          <Route path="/login" element={<Login />} />
    +    -          <Route path="/auth/callback" element={<AuthCallback />} />
    +    -          <Route
    +    -            path="/"
    +    -            element={
    +    -              <ProtectedRoute>
    +    -                <Dashboard />
    +    -              </ProtectedRoute>
    +    -            }
    +    -          />
    +    -          <Route
    +    -            path="/repos/:repoId"
    +    -            element={
    +    -              <ProtectedRoute>
    +    -                <AppLayout>
    +    +      <ToastProvider>
    +    +        <BrowserRouter>
    +    +          <Routes>
    +    +            <Route path="/login" element={<Login />} />
    +    +            <Route path="/auth/callback" element={<AuthCallback />} />
    +    +            <Route
    +    +              path="/"
    +    +              element={
    +    +                <ProtectedRoute>
    +    +                  <Dashboard />
    +    +                </ProtectedRoute>
    +    +              }
    +    +            />
    +    +            <Route
    +    +              path="/repos/:repoId"
    +    +              element={
    +    +                <ProtectedRoute>
    +                       <CommitTimeline />
    +    -                </AppLayout>
    +    -              </ProtectedRoute>
    +    -            }
    +    -          />
    +    -          <Route
    +    -            path="/repos/:repoId/audits/:auditId"
    +    -            element={
    +    -              <ProtectedRoute>
    +    -                <AppLayout>
    +    +                </ProtectedRoute>
    +    +              }
    +    +            />
    +    +            <Route
    +    +              path="/repos/:repoId/audits/:auditId"
    +    +              element={
    +    +                <ProtectedRoute>
    +                       <AuditDetailPage />
    +    -                </AppLayout>
    +    -              </ProtectedRoute>
    +    -            }
    +    -          />
    +    -          <Route path="*" element={<Navigate to="/" replace />} />
    +    -        </Routes>
    +    -      </BrowserRouter>
    +    +                </ProtectedRoute>
    +    +              }
    +    +            />
    +    +            <Route path="*" element={<Navigate to="/" replace />} />
    +    +          </Routes>
    +    +        </BrowserRouter>
    +    +      </ToastProvider>
    +         </AuthProvider>
    +       );
    +     }
    +    diff --git a/web/src/__tests__/App.test.tsx b/web/src/__tests__/App.test.tsx
    +    index b9cf339..ea10fbc 100644
    +    --- a/web/src/__tests__/App.test.tsx
    +    +++ b/web/src/__tests__/App.test.tsx
    +    @@ -1,10 +1,12 @@
    +     import { describe, it, expect } from 'vitest';
    +    -import { render, screen } from '@testing-library/react';
    +    +import { render, screen, fireEvent } from '@testing-library/react';
    +     import Login from '../pages/Login';
    +     import HealthBadge from '../components/HealthBadge';
    +     import ConfirmDialog from '../components/ConfirmDialog';
    +     import ResultBadge from '../components/ResultBadge';
    +     import CheckResultCard from '../components/CheckResultCard';
    +    +import Skeleton, { SkeletonCard, SkeletonRow } from '../components/Skeleton';
    +    +import EmptyState from '../components/EmptyState';
    +     
    +     describe('Login', () => {
    +       it('renders the sign in button', () => {
    +    @@ -82,3 +84,40 @@ describe('CheckResultCard', () => {
    +         expect(screen.getByText('Boundary compliance')).toBeInTheDocument();
    +       });
    +     });
    +    +
    +    +describe('Skeleton', () => {
    +    +  it('renders skeleton element', () => {
    +    +    render(<Skeleton />);
    +    +    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
    +    +  });
    +    +
    +    +  it('renders SkeletonCard', () => {
    +    +    render(<SkeletonCard />);
    +    +    const skeletons = screen.getAllByTestId('skeleton');
    +    +    expect(skeletons.length).toBeGreaterThan(0);
    +    +  });
    +    +
    +    +  it('renders SkeletonRow', () => {
    +    +    render(<SkeletonRow />);
    +    +    const skeletons = screen.getAllByTestId('skeleton');
    +    +    expect(skeletons.length).toBeGreaterThan(0);
    +    +  });
    +    +});
    +    +
    +    +describe('EmptyState', () => {
    +    +  it('renders message', () => {
    +    +    render(<EmptyState message="Nothing here" />);
    +    +    expect(screen.getByText('Nothing here')).toBeInTheDocument();
    +    +  });
    +    +
    +    +  it('renders empty-state test id', () => {
    +    +    render(<EmptyState message="Test" />);
    +    +    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    +    +  });
    +    +
    +    +  it('renders action button when provided', () => {
    +    +    const fn = () => {};
    +    +    render(<EmptyState message="Empty" actionLabel="Add Item" onAction={fn} />);
    +    +    expect(screen.getByText('Add Item')).toBeInTheDocument();
    +    +  });
    +    +});
    +    diff --git a/web/src/components/AppShell.tsx b/web/src/components/AppShell.tsx
    +    new file mode 100644
    +    index 0000000..f3102d9
    +    --- /dev/null
    +    +++ b/web/src/components/AppShell.tsx
    +    @@ -0,0 +1,201 @@
    +    +import { useState, useEffect, useCallback, type ReactNode } from 'react';
    +    +import { useNavigate, useLocation } from 'react-router-dom';
    +    +import { useAuth } from '../context/AuthContext';
    +    +import HealthBadge from './HealthBadge';
    +    +
    +    +const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +    +
    +    +interface SidebarRepo {
    +    +  id: string;
    +    +  full_name: string;
    +    +  health_score: string;
    +    +}
    +    +
    +    +interface AppShellProps {
    +    +  children: ReactNode;
    +    +  sidebarRepos?: SidebarRepo[];
    +    +  onReposChange?: () => void;
    +    +}
    +    +
    +    +function AppShell({ children, sidebarRepos, onReposChange }: AppShellProps) {
    +    +  const { user, token, logout } = useAuth();
    +    +  const navigate = useNavigate();
    +    +  const location = useLocation();
    +    +  const [collapsed, setCollapsed] = useState(false);
    +    +  const [repos, setRepos] = useState<SidebarRepo[]>(sidebarRepos ?? []);
    +    +
    +    +  useEffect(() => {
    +    +    if (sidebarRepos) {
    +    +      setRepos(sidebarRepos);
    +    +      return;
    +    +    }
    +    +    // Load repos for sidebar if not provided
    +    +    const load = async () => {
    +    +      try {
    +    +        const res = await fetch(`${API_BASE}/repos`, {
    +    +          headers: { Authorization: `Bearer ${token}` },
    +    +        });
    +    +        if (res.ok) {
    +    +          const data = await res.json();
    +    +          setRepos(data.items);
    +    +        }
    +    +      } catch {
    +    +        // best effort
    +    +      }
    +    +    };
    +    +    load();
    +    +  }, [token, sidebarRepos]);
    +    +
    +    +  // Responsive: collapse sidebar below 1024px
    +    +  useEffect(() => {
    +    +    const check = () => setCollapsed(window.innerWidth < 1024);
    +    +    check();
    +    +    window.addEventListener('resize', check);
    +    +    return () => window.removeEventListener('resize', check);
    +    +  }, []);
    +    +
    +    +  const sidebarWidth = collapsed ? 0 : 240;
    +    +
    +    +  return (
    +    +    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: '#0F172A', color: '#F8FAFC' }}>
    +    +      {/* Header */}
    +    +      <header
    +    +        style={{
    +    +          display: 'flex',
    +    +          alignItems: 'center',
    +    +          justifyContent: 'space-between',
    +    +          padding: '12px 24px',
    +    +          borderBottom: '1px solid #1E293B',
    +    +          background: '#0F172A',
    +    +          zIndex: 10,
    +    +        }}
    +    +      >
    +    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    +    +          {collapsed && (
    +    +            <button
    +    +              onClick={() => setCollapsed(false)}
    +    +              aria-label="Open menu"
    +    +              style={{
    +    +                background: 'transparent',
    +    +                color: '#94A3B8',
    +    +                border: '1px solid #334155',
    +    +                borderRadius: '6px',
    +    +                padding: '4px 8px',
    +    +                cursor: 'pointer',
    +    +                fontSize: '1rem',
    +    +              }}
    +    +            >
    +    +              &#9776;
    +    +            </button>
    +    +          )}
    +    +          <h1
    +    +            onClick={() => navigate('/')}
    +    +            style={{ fontSize: '1.15rem', fontWeight: 700, cursor: 'pointer', margin: 0 }}
    +    +          >
    +    +            ForgeGuard
    +    +          </h1>
    +    +        </div>
    +    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    +    +          {user?.avatar_url && (
    +    +            <img
    +    +              src={user.avatar_url}
    +    +              alt={user.github_login}
    +    +              style={{ width: 28, height: 28, borderRadius: '50%' }}
    +    +            />
    +    +          )}
    +    +          <span style={{ color: '#94A3B8', fontSize: '0.85rem' }}>{user?.github_login}</span>
    +    +          <button
    +    +            onClick={logout}
    +    +            style={{
    +    +              background: 'transparent',
    +    +              color: '#94A3B8',
    +    +              border: '1px solid #334155',
    +    +              borderRadius: '6px',
    +    +              padding: '4px 14px',
    +    +              cursor: 'pointer',
    +    +              fontSize: '0.8rem',
    +    +            }}
    +    +          >
    +    +            Logout
    +    +          </button>
    +    +        </div>
    +    +      </header>
    +    +
    +    +      <div style={{ display: 'flex', flex: 1 }}>
    +    +        {/* Sidebar */}
    +    +        {!collapsed && (
    +    +          <aside
    +    +            style={{
    +    +              width: sidebarWidth,
    +    +              borderRight: '1px solid #1E293B',
    +    +              padding: '16px 0',
    +    +              overflowY: 'auto',
    +    +              flexShrink: 0,
    +    +              background: '#0F172A',
    +    +            }}
    +    +          >
    +    +            <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
    +    +              <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
    +    +                Repos
    +    +              </span>
    +    +              {window.innerWidth < 1024 && (
    +    +                <button
    +    +                  onClick={() => setCollapsed(true)}
    +    +                  aria-label="Close menu"
    +    +                  style={{
    +    +                    background: 'transparent',
    +    +                    color: '#94A3B8',
    +    +                    border: 'none',
    +    +                    cursor: 'pointer',
    +    +                    fontSize: '1rem',
    +    +                  }}
    +    +                >
    +    +                  &times;
    +    +                </button>
    +    +              )}
    +    +            </div>
    +    +            {repos.length === 0 ? (
    +    +              <div style={{ padding: '12px 16px', color: '#64748B', fontSize: '0.8rem' }}>
    +    +                No repos connected
    +    +              </div>
    +    +            ) : (
    +    +              repos.map((repo) => {
    +    +                const isActive = location.pathname.startsWith(`/repos/${repo.id}`);
    +    +                return (
    +    +                  <div
    +    +                    key={repo.id}
    +    +                    onClick={() => navigate(`/repos/${repo.id}`)}
    +    +                    style={{
    +    +                      display: 'flex',
    +    +                      alignItems: 'center',
    +    +                      gap: '8px',
    +    +                      padding: '8px 16px',
    +    +                      cursor: 'pointer',
    +    +                      background: isActive ? '#1E293B' : 'transparent',
    +    +                      borderLeft: isActive ? '3px solid #2563EB' : '3px solid transparent',
    +    +                      transition: 'background 0.15s',
    +    +                      fontSize: '0.8rem',
    +    +                    }}
    +    +                  >
    +    +                    <HealthBadge score={repo.health_score} size={8} />
    +    +                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
    +    +                      {repo.full_name}
    +    +                    </span>
    +    +                  </div>
    +    +                );
    +    +              })
    +    +            )}
    +    +          </aside>
    +    +        )}
    +    +
    +    +        {/* Main */}
    +    +        <main style={{ flex: 1, overflow: 'auto' }}>
    +    +          {children}
    +    +        </main>
    +    +      </div>
    +    +    </div>
    +    +  );
    +    +}
    +    +
    +    +export type { SidebarRepo };
    +    +export default AppShell;
    +    diff --git a/web/src/components/CommitRow.tsx b/web/src/components/CommitRow.tsx
    +    index 5db2162..179dfda 100644
    +    --- a/web/src/components/CommitRow.tsx
    +    +++ b/web/src/components/CommitRow.tsx
    +    @@ -11,6 +11,7 @@ interface AuditRun {
    +       started_at: string | null;
    +       completed_at: string | null;
    +       files_checked: number;
    +    +  check_summary: string | null;
    +     }
    +     
    +     interface CommitRowProps {
    +    @@ -71,7 +72,12 @@ function CommitRow({ audit, onClick }: CommitRowProps) {
    +               </div>
    +             </div>
    +           </div>
    +    -      <div style={{ flexShrink: 0, marginLeft: '12px' }}>
    +    +      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, marginLeft: '12px' }}>
    +    +        {audit.check_summary && (
    +    +          <span style={{ color: '#64748B', fontSize: '0.65rem', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
    +    +            {audit.check_summary}
    +    +          </span>
    +    +        )}
    +             <ResultBadge result={audit.overall_result} />
    +           </div>
    +         </div>
    +    diff --git a/web/src/components/EmptyState.tsx b/web/src/components/EmptyState.tsx
    +    new file mode 100644
    +    index 0000000..429e852
    +    --- /dev/null
    +    +++ b/web/src/components/EmptyState.tsx
    +    @@ -0,0 +1,38 @@
    +    +interface EmptyStateProps {
    +    +  message: string;
    +    +  actionLabel?: string;
    +    +  onAction?: () => void;
    +    +}
    +    +
    +    +function EmptyState({ message, actionLabel, onAction }: EmptyStateProps) {
    +    +  return (
    +    +    <div
    +    +      data-testid="empty-state"
    +    +      style={{
    +    +        textAlign: 'center',
    +    +        padding: '64px 24px',
    +    +        color: '#94A3B8',
    +    +      }}
    +    +    >
    +    +      <p style={{ marginBottom: actionLabel ? '16px' : '0' }}>{message}</p>
    +    +      {actionLabel && onAction && (
    +    +        <button
    +    +          onClick={onAction}
    +    +          style={{
    +    +            background: '#2563EB',
    +    +            color: '#fff',
    +    +            border: 'none',
    +    +            borderRadius: '6px',
    +    +            padding: '8px 16px',
    +    +            cursor: 'pointer',
    +    +            fontSize: '0.85rem',
    +    +          }}
    +    +        >
    +    +          {actionLabel}
    +    +        </button>
    +    +      )}
    +    +    </div>
    +    +  );
    +    +}
    +    +
    +    +export default EmptyState;
    +    diff --git a/web/src/components/Skeleton.tsx b/web/src/components/Skeleton.tsx
    +    new file mode 100644
    +    index 0000000..19bb14d
    +    --- /dev/null
    +    +++ b/web/src/components/Skeleton.tsx
    +    @@ -0,0 +1,68 @@
    +    +interface SkeletonProps {
    +    +  width?: string;
    +    +  height?: string;
    +    +  borderRadius?: string;
    +    +  style?: React.CSSProperties;
    +    +}
    +    +
    +    +function Skeleton({ width = '100%', height = '16px', borderRadius = '4px', style }: SkeletonProps) {
    +    +  return (
    +    +    <div
    +    +      data-testid="skeleton"
    +    +      style={{
    +    +        width,
    +    +        height,
    +    +        borderRadius,
    +    +        background: 'linear-gradient(90deg, #1E293B 25%, #2D3B4F 50%, #1E293B 75%)',
    +    +        backgroundSize: '200% 100%',
    +    +        animation: 'skeleton-shimmer 1.5s ease-in-out infinite',
    +    +        ...style,
    +    +      }}
    +    +    />
    +    +  );
    +    +}
    +    +
    +    +export function SkeletonCard() {
    +    +  return (
    +    +    <div
    +    +      style={{
    +    +        background: '#1E293B',
    +    +        borderRadius: '8px',
    +    +        padding: '16px 20px',
    +    +        display: 'flex',
    +    +        alignItems: 'center',
    +    +        gap: '12px',
    +    +      }}
    +    +    >
    +    +      <Skeleton width="12px" height="12px" borderRadius="50%" />
    +    +      <div style={{ flex: 1 }}>
    +    +        <Skeleton width="40%" height="14px" style={{ marginBottom: '8px' }} />
    +    +        <Skeleton width="60%" height="10px" />
    +    +      </div>
    +    +    </div>
    +    +  );
    +    +}
    +    +
    +    +export function SkeletonRow() {
    +    +  return (
    +    +    <div
    +    +      style={{
    +    +        background: '#1E293B',
    +    +        borderRadius: '6px',
    +    +        padding: '12px 16px',
    +    +        display: 'flex',
    +    +        alignItems: 'center',
    +    +        gap: '12px',
    +    +      }}
    +    +    >
    +    +      <Skeleton width="56px" height="14px" />
    +    +      <div style={{ flex: 1 }}>
    +    +        <Skeleton width="50%" height="12px" style={{ marginBottom: '6px' }} />
    +    +        <Skeleton width="30%" height="10px" />
    +    +      </div>
    +    +      <Skeleton width="48px" height="20px" borderRadius="4px" />
    +    +    </div>
    +    +  );
    +    +}
    +    +
    +    +export default Skeleton;
    +    diff --git a/web/src/context/ToastContext.tsx b/web/src/context/ToastContext.tsx
    +    new file mode 100644
    +    index 0000000..c1ae210
    +    --- /dev/null
    +    +++ b/web/src/context/ToastContext.tsx
    +    @@ -0,0 +1,99 @@
    +    +import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
    +    +
    +    +interface Toast {
    +    +  id: number;
    +    +  message: string;
    +    +  type: 'error' | 'success' | 'info';
    +    +}
    +    +
    +    +interface ToastContextValue {
    +    +  addToast: (message: string, type?: Toast['type']) => void;
    +    +}
    +    +
    +    +const ToastContext = createContext<ToastContextValue | null>(null);
    +    +
    +    +let nextId = 1;
    +    +
    +    +export function ToastProvider({ children }: { children: ReactNode }) {
    +    +  const [toasts, setToasts] = useState<Toast[]>([]);
    +    +
    +    +  const addToast = useCallback((message: string, type: Toast['type'] = 'error') => {
    +    +    const id = nextId++;
    +    +    setToasts((prev) => [...prev, { id, message, type }]);
    +    +    setTimeout(() => {
    +    +      setToasts((prev) => prev.filter((t) => t.id !== id));
    +    +    }, 5000);
    +    +  }, []);
    +    +
    +    +  const removeToast = useCallback((id: number) => {
    +    +    setToasts((prev) => prev.filter((t) => t.id !== id));
    +    +  }, []);
    +    +
    +    +  const COLORS: Record<string, { bg: string; border: string }> = {
    +    +    error: { bg: '#7F1D1D', border: '#EF4444' },
    +    +    success: { bg: '#14532D', border: '#22C55E' },
    +    +    info: { bg: '#1E3A5F', border: '#2563EB' },
    +    +  };
    +    +
    +    +  return (
    +    +    <ToastContext.Provider value={{ addToast }}>
    +    +      {children}
    +    +      <div
    +    +        style={{
    +    +          position: 'fixed',
    +    +          bottom: '24px',
    +    +          right: '24px',
    +    +          zIndex: 1000,
    +    +          display: 'flex',
    +    +          flexDirection: 'column',
    +    +          gap: '8px',
    +    +          maxWidth: '360px',
    +    +        }}
    +    +      >
    +    +        {toasts.map((toast) => {
    +    +          const colors = COLORS[toast.type] ?? COLORS.info;
    +    +          return (
    +    +            <div
    +    +              key={toast.id}
    +    +              role="alert"
    +    +              style={{
    +    +                background: colors.bg,
    +    +                borderLeft: `3px solid ${colors.border}`,
    +    +                borderRadius: '6px',
    +    +                padding: '12px 16px',
    +    +                fontSize: '0.85rem',
    +    +                color: '#F8FAFC',
    +    +                display: 'flex',
    +    +                alignItems: 'center',
    +    +                justifyContent: 'space-between',
    +    +                gap: '8px',
    +    +              }}
    +    +            >
    +    +              <span>{toast.message}</span>
    +    +              <button
    +    +                onClick={() => removeToast(toast.id)}
    +    +                style={{
    +    +                  background: 'transparent',
    +    +                  color: '#94A3B8',
    +    +                  border: 'none',
    +    +                  cursor: 'pointer',
    +    +                  fontSize: '1rem',
    +    +                  padding: 0,
    +    +                  lineHeight: 1,
    +    +                }}
    +    +              >
    +    +                &times;
    +    +              </button>
    +    +            </div>
    +    +          );
    +    +        })}
    +    +      </div>
    +    +    </ToastContext.Provider>
    +    +  );
    +    +}
    +    +
    +    +export function useToast(): ToastContextValue {
    +    +  const ctx = useContext(ToastContext);
    +    +  if (!ctx) throw new Error('useToast must be used within ToastProvider');
    +    +  return ctx;
    +    +}
    +    diff --git a/web/src/hooks/useWebSocket.ts b/web/src/hooks/useWebSocket.ts
    +    new file mode 100644
    +    index 0000000..86131f1
    +    --- /dev/null
    +    +++ b/web/src/hooks/useWebSocket.ts
    +    @@ -0,0 +1,53 @@
    +    +import { useEffect, useRef, useCallback } from 'react';
    +    +import { useAuth } from '../context/AuthContext';
    +    +
    +    +type MessageHandler = (data: { type: string; payload: unknown }) => void;
    +    +
    +    +const WS_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/^http/, 'ws');
    +    +
    +    +export function useWebSocket(onMessage: MessageHandler) {
    +    +  const { token } = useAuth();
    +    +  const wsRef = useRef<WebSocket | null>(null);
    +    +  const handlerRef = useRef(onMessage);
    +    +  handlerRef.current = onMessage;
    +    +
    +    +  const connect = useCallback(() => {
    +    +    if (!token) return;
    +    +
    +    +    const url = `${WS_BASE}/ws?token=${encodeURIComponent(token)}`;
    +    +    const ws = new WebSocket(url);
    +    +
    +    +    ws.onmessage = (event) => {
    +    +      try {
    +    +        const data = JSON.parse(event.data);
    +    +        handlerRef.current(data);
    +    +      } catch {
    +    +        // ignore malformed messages
    +    +      }
    +    +    };
    +    +
    +    +    ws.onclose = () => {
    +    +      // Reconnect after 3 seconds
    +    +      setTimeout(() => {
    +    +        if (wsRef.current === ws) {
    +    +          connect();
    +    +        }
    +    +      }, 3000);
    +    +    };
    +    +
    +    +    ws.onerror = () => {
    +    +      ws.close();
    +    +    };
    +    +
    +    +    wsRef.current = ws;
    +    +  }, [token]);
    +    +
    +    +  useEffect(() => {
    +    +    connect();
    +    +    return () => {
    +    +      const ws = wsRef.current;
    +    +      wsRef.current = null;
    +    +      if (ws) ws.close();
    +    +    };
    +    +  }, [connect]);
    +    +}
    +    diff --git a/web/src/index.css b/web/src/index.css
    +    index bacf9f8..23889d9 100644
    +    --- a/web/src/index.css
    +    +++ b/web/src/index.css
    +    @@ -11,3 +11,8 @@ body {
    +       -webkit-font-smoothing: antialiased;
    +       -moz-osx-font-smoothing: grayscale;
    +     }
    +    +
    +    +@keyframes skeleton-shimmer {
    +    +  0% { background-position: 200% 0; }
    +    +  100% { background-position: -200% 0; }
    +    +}
    +    diff --git a/web/src/pages/AuditDetail.tsx b/web/src/pages/AuditDetail.tsx
    +    index 3e51080..9fa2e71 100644
    +    --- a/web/src/pages/AuditDetail.tsx
    +    +++ b/web/src/pages/AuditDetail.tsx
    +    @@ -1,9 +1,12 @@
    +     import { useState, useEffect } from 'react';
    +     import { useParams, useNavigate } from 'react-router-dom';
    +     import { useAuth } from '../context/AuthContext';
    +    +import { useToast } from '../context/ToastContext';
    +    +import AppShell from '../components/AppShell';
    +     import ResultBadge from '../components/ResultBadge';
    +     import CheckResultCard from '../components/CheckResultCard';
    +     import type { CheckResultData } from '../components/CheckResultCard';
    +    +import Skeleton from '../components/Skeleton';
    +     
    +     const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +     
    +    @@ -24,6 +27,7 @@ interface AuditDetail {
    +     function AuditDetailPage() {
    +       const { repoId, auditId } = useParams<{ repoId: string; auditId: string }>();
    +       const { token } = useAuth();
    +    +  const { addToast } = useToast();
    +       const navigate = useNavigate();
    +     
    +       const [detail, setDetail] = useState<AuditDetail | null>(null);
    +    @@ -38,29 +42,47 @@ function AuditDetailPage() {
    +             );
    +             if (res.ok) {
    +               setDetail(await res.json());
    +    +        } else {
    +    +          addToast('Failed to load audit detail');
    +             }
    +           } catch {
    +    -        // network error
    +    +        addToast('Network error loading audit detail');
    +           } finally {
    +             setLoading(false);
    +           }
    +         };
    +         fetchDetail();
    +    -  }, [repoId, auditId, token]);
    +    +  }, [repoId, auditId, token, addToast]);
    +     
    +       if (loading) {
    +         return (
    +    -      <div style={{ padding: '24px', color: '#94A3B8' }}>
    +    -        Loading audit detail...
    +    -      </div>
    +    +      <AppShell>
    +    +        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    +          <Skeleton width="80px" height="28px" style={{ marginBottom: '20px' }} />
    +    +          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '20px', marginBottom: '16px' }}>
    +    +            <Skeleton width="30%" height="14px" style={{ marginBottom: '12px' }} />
    +    +            <Skeleton width="60%" height="12px" style={{ marginBottom: '12px' }} />
    +    +            <Skeleton width="100px" height="24px" />
    +    +          </div>
    +    +          <Skeleton width="120px" height="16px" style={{ marginBottom: '12px' }} />
    +    +          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
    +    +            <Skeleton width="50%" height="14px" />
    +    +          </div>
    +    +          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
    +    +            <Skeleton width="50%" height="14px" />
    +    +          </div>
    +    +        </div>
    +    +      </AppShell>
    +         );
    +       }
    +     
    +       if (!detail) {
    +         return (
    +    -      <div style={{ padding: '24px', color: '#94A3B8' }}>
    +    -        Audit not found.
    +    -      </div>
    +    +      <AppShell>
    +    +        <div style={{ padding: '24px', color: '#94A3B8' }}>
    +    +          Audit not found.
    +    +        </div>
    +    +      </AppShell>
    +         );
    +       }
    +     
    +    @@ -70,67 +92,69 @@ function AuditDetailPage() {
    +           : null;
    +     
    +       return (
    +    -    <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    -      <button
    +    -        onClick={() => navigate(`/repos/${repoId}`)}
    +    -        style={{
    +    -          background: 'transparent',
    +    -          color: '#94A3B8',
    +    -          border: '1px solid #334155',
    +    -          borderRadius: '6px',
    +    -          padding: '6px 12px',
    +    -          cursor: 'pointer',
    +    -          fontSize: '0.8rem',
    +    -          marginBottom: '20px',
    +    -        }}
    +    -      >
    +    -        Back to Timeline
    +    -      </button>
    +    +    <AppShell>
    +    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    +        <button
    +    +          onClick={() => navigate(`/repos/${repoId}`)}
    +    +          style={{
    +    +            background: 'transparent',
    +    +            color: '#94A3B8',
    +    +            border: '1px solid #334155',
    +    +            borderRadius: '6px',
    +    +            padding: '6px 12px',
    +    +            cursor: 'pointer',
    +    +            fontSize: '0.8rem',
    +    +            marginBottom: '20px',
    +    +          }}
    +    +        >
    +    +          Back to Timeline
    +    +        </button>
    +     
    +    -      {/* Commit Info */}
    +    -      <div
    +    -        style={{
    +    -          background: '#1E293B',
    +    -          borderRadius: '8px',
    +    -          padding: '20px',
    +    -          marginBottom: '16px',
    +    -        }}
    +    -      >
    +    -        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
    +    -          <a
    +    -            href={`https://github.com/commit/${detail.commit_sha}`}
    +    -            target="_blank"
    +    -            rel="noopener noreferrer"
    +    -            style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem' }}
    +    -          >
    +    -            {detail.commit_sha.substring(0, 7)}
    +    -          </a>
    +    -          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +    -            {detail.branch} &middot; {detail.commit_author}
    +    -          </span>
    +    +        {/* Commit Info */}
    +    +        <div
    +    +          style={{
    +    +            background: '#1E293B',
    +    +            borderRadius: '8px',
    +    +            padding: '20px',
    +    +            marginBottom: '16px',
    +    +          }}
    +    +        >
    +    +          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
    +    +            <a
    +    +              href={`https://github.com/commit/${detail.commit_sha}`}
    +    +              target="_blank"
    +    +              rel="noopener noreferrer"
    +    +              style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem' }}
    +    +            >
    +    +              {detail.commit_sha.substring(0, 7)}
    +    +            </a>
    +    +            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +    +              {detail.branch} &middot; {detail.commit_author}
    +    +            </span>
    +    +          </div>
    +    +          <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
    +    +            {detail.commit_message}
    +    +          </p>
    +    +          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
    +    +            <ResultBadge result={detail.overall_result} size="large" />
    +    +            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +    +              {detail.files_checked} files checked
    +    +              {duration && <span> &middot; {duration}</span>}
    +    +            </span>
    +    +          </div>
    +             </div>
    +    -        <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
    +    -          {detail.commit_message}
    +    -        </p>
    +    -        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
    +    -          <ResultBadge result={detail.overall_result} size="large" />
    +    -          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +    -            {detail.files_checked} files checked
    +    -            {duration && <span> &middot; {duration}</span>}
    +    -          </span>
    +    -        </div>
    +    -      </div>
    +     
    +    -      {/* Check Results */}
    +    -      <h3 style={{ fontSize: '1rem', marginBottom: '12px' }}>Check Results</h3>
    +    -      {detail.checks.length === 0 ? (
    +    -        <p style={{ color: '#94A3B8' }}>No check results.</p>
    +    -      ) : (
    +    -        detail.checks.map((check) => (
    +    -          <CheckResultCard key={check.id} check={check} />
    +    -        ))
    +    -      )}
    +    -    </div>
    +    +        {/* Check Results */}
    +    +        <h3 style={{ fontSize: '1rem', marginBottom: '12px' }}>Check Results</h3>
    +    +        {detail.checks.length === 0 ? (
    +    +          <p style={{ color: '#94A3B8' }}>No check results.</p>
    +    +        ) : (
    +    +          detail.checks.map((check) => (
    +    +            <CheckResultCard key={check.id} check={check} />
    +    +          ))
    +    +        )}
    +    +      </div>
    +    +    </AppShell>
    +       );
    +     }
    +     
    +    diff --git a/web/src/pages/CommitTimeline.tsx b/web/src/pages/CommitTimeline.tsx
    +    index 07d85b7..1fcfb5b 100644
    +    --- a/web/src/pages/CommitTimeline.tsx
    +    +++ b/web/src/pages/CommitTimeline.tsx
    +    @@ -1,15 +1,21 @@
    +     import { useState, useEffect, useCallback } from 'react';
    +     import { useParams, useNavigate } from 'react-router-dom';
    +     import { useAuth } from '../context/AuthContext';
    +    +import { useToast } from '../context/ToastContext';
    +    +import { useWebSocket } from '../hooks/useWebSocket';
    +    +import AppShell from '../components/AppShell';
    +     import CommitRow from '../components/CommitRow';
    +     import type { AuditRun } from '../components/CommitRow';
    +     import HealthBadge from '../components/HealthBadge';
    +    +import EmptyState from '../components/EmptyState';
    +    +import { SkeletonRow } from '../components/Skeleton';
    +     
    +     const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +     
    +     function CommitTimeline() {
    +       const { repoId } = useParams<{ repoId: string }>();
    +       const { token } = useAuth();
    +    +  const { addToast } = useToast();
    +       const navigate = useNavigate();
    +     
    +       const [audits, setAudits] = useState<AuditRun[]>([]);
    +    @@ -28,78 +34,107 @@ function CommitTimeline() {
    +             const data = await res.json();
    +             setAudits(data.items);
    +             setTotal(data.total);
    +    +      } else {
    +    +        addToast('Failed to load audits');
    +           }
    +         } catch {
    +    -      // network error
    +    +      addToast('Network error loading audits');
    +         } finally {
    +           setLoading(false);
    +         }
    +    -  }, [repoId, token, offset]);
    +    +  }, [repoId, token, offset, addToast]);
    +     
    +       useEffect(() => {
    +         fetchAudits();
    +       }, [fetchAudits]);
    +     
    +    +  // Real-time: refresh when audit for this repo completes
    +    +  useWebSocket(useCallback((data) => {
    +    +    if (data.type === 'audit_update') {
    +    +      const payload = data.payload as { repo_id?: string };
    +    +      if (payload.repo_id === repoId) {
    +    +        fetchAudits();
    +    +      }
    +    +    }
    +    +  }, [fetchAudits, repoId]));
    +    +
    +       const handleAuditClick = (audit: AuditRun) => {
    +         navigate(`/repos/${repoId}/audits/${audit.id}`);
    +       };
    +     
    +    -  return (
    +    -    <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    -      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
    +    -        <button
    +    -          onClick={() => navigate('/')}
    +    -          style={{
    +    -            background: 'transparent',
    +    -            color: '#94A3B8',
    +    -            border: '1px solid #334155',
    +    -            borderRadius: '6px',
    +    -            padding: '6px 12px',
    +    -            cursor: 'pointer',
    +    -            fontSize: '0.8rem',
    +    -          }}
    +    -        >
    +    -          Back
    +    -        </button>
    +    -        <HealthBadge score="pending" />
    +    -        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
    +    -        <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
    +    -      </div>
    +    +  // Compute health from loaded audits
    +    +  const computedHealth = (() => {
    +    +    const completed = audits.filter((a) => a.status === 'completed');
    +    +    if (completed.length === 0) return 'pending';
    +    +    const allPass = completed.every((a) => a.overall_result === 'PASS');
    +    +    const anyFail = completed.some((a) => a.overall_result === 'FAIL' || a.overall_result === 'ERROR');
    +    +    if (allPass) return 'green';
    +    +    if (anyFail) return 'red';
    +    +    return 'yellow';
    +    +  })();
    +     
    +    -      {loading ? (
    +    -        <p style={{ color: '#94A3B8' }}>Loading audits...</p>
    +    -      ) : audits.length === 0 ? (
    +    -        <div style={{ textAlign: 'center', padding: '64px 24px', color: '#94A3B8' }}>
    +    -          <p>No audit results yet. Push a commit to trigger the first audit.</p>
    +    +  return (
    +    +    <AppShell>
    +    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
    +    +          <button
    +    +            onClick={() => navigate('/')}
    +    +            style={{
    +    +              background: 'transparent',
    +    +              color: '#94A3B8',
    +    +              border: '1px solid #334155',
    +    +              borderRadius: '6px',
    +    +              padding: '6px 12px',
    +    +              cursor: 'pointer',
    +    +              fontSize: '0.8rem',
    +    +            }}
    +    +          >
    +    +            Back
    +    +          </button>
    +    +          <HealthBadge score={computedHealth} />
    +    +          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
    +    +          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
    +             </div>
    +    -      ) : (
    +    -        <>
    +    +
    +    +        {loading ? (
    +               <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    +    -            {audits.map((audit) => (
    +    -              <CommitRow key={audit.id} audit={audit} onClick={handleAuditClick} />
    +    -            ))}
    +    +            <SkeletonRow />
    +    +            <SkeletonRow />
    +    +            <SkeletonRow />
    +    +            <SkeletonRow />
    +    +            <SkeletonRow />
    +               </div>
    +    -          {total > offset + limit && (
    +    -            <button
    +    -              onClick={() => setOffset((o) => o + limit)}
    +    -              style={{
    +    -                display: 'block',
    +    -                margin: '16px auto',
    +    -                background: '#1E293B',
    +    -                color: '#94A3B8',
    +    -                border: '1px solid #334155',
    +    -                borderRadius: '6px',
    +    -                padding: '8px 24px',
    +    -                cursor: 'pointer',
    +    -                fontSize: '0.85rem',
    +    -              }}
    +    -            >
    +    -              Load More
    +    -            </button>
    +    -          )}
    +    -        </>
    +    -      )}
    +    -    </div>
    +    +        ) : audits.length === 0 ? (
    +    +          <EmptyState message="No audit results yet. Push a commit to trigger the first audit." />
    +    +        ) : (
    +    +          <>
    +    +            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    +    +              {audits.map((audit) => (
    +    +                <CommitRow key={audit.id} audit={audit} onClick={handleAuditClick} />
    +    +              ))}
    +    +            </div>
    +    +            {total > offset + limit && (
    +    +              <button
    +    +                onClick={() => setOffset((o) => o + limit)}
    +    +                style={{
    +    +                  display: 'block',
    +    +                  margin: '16px auto',
    +    +                  background: '#1E293B',
    +    +                  color: '#94A3B8',
    +    +                  border: '1px solid #334155',
    +    +                  borderRadius: '6px',
    +    +                  padding: '8px 24px',
    +    +                  cursor: 'pointer',
    +    +                  fontSize: '0.85rem',
    +    +                }}
    +    +              >
    +    +                Load More
    +    +              </button>
    +    +            )}
    +    +          </>
    +    +        )}
    +    +      </div>
    +    +    </AppShell>
    +       );
    +     }
    +     
    +    diff --git a/web/src/pages/Dashboard.tsx b/web/src/pages/Dashboard.tsx
    +    index c6ebb70..aa05884 100644
    +    --- a/web/src/pages/Dashboard.tsx
    +    +++ b/web/src/pages/Dashboard.tsx
    +    @@ -1,15 +1,21 @@
    +     import { useState, useEffect, useCallback } from 'react';
    +     import { useNavigate } from 'react-router-dom';
    +     import { useAuth } from '../context/AuthContext';
    +    +import { useToast } from '../context/ToastContext';
    +    +import { useWebSocket } from '../hooks/useWebSocket';
    +    +import AppShell from '../components/AppShell';
    +     import RepoCard from '../components/RepoCard';
    +     import type { Repo } from '../components/RepoCard';
    +     import RepoPickerModal from '../components/RepoPickerModal';
    +     import ConfirmDialog from '../components/ConfirmDialog';
    +    +import EmptyState from '../components/EmptyState';
    +    +import { SkeletonCard } from '../components/Skeleton';
    +     
    +     const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +     
    +     function Dashboard() {
    +    -  const { user, token, logout } = useAuth();
    +    +  const { token } = useAuth();
    +    +  const { addToast } = useToast();
    +       const navigate = useNavigate();
    +       const [repos, setRepos] = useState<Repo[]>([]);
    +       const [loading, setLoading] = useState(true);
    +    @@ -24,29 +30,39 @@ function Dashboard() {
    +           if (res.ok) {
    +             const data = await res.json();
    +             setRepos(data.items);
    +    +      } else {
    +    +        addToast('Failed to load repos');
    +           }
    +         } catch {
    +    -      // network error -- keep existing
    +    +      addToast('Network error loading repos');
    +         } finally {
    +           setLoading(false);
    +         }
    +    -  }, [token]);
    +    +  }, [token, addToast]);
    +     
    +       useEffect(() => {
    +         fetchRepos();
    +       }, [fetchRepos]);
    +     
    +    +  // Real-time: refresh repos when an audit completes
    +    +  useWebSocket(useCallback((data) => {
    +    +    if (data.type === 'audit_update') {
    +    +      fetchRepos();
    +    +    }
    +    +  }, [fetchRepos]));
    +    +
    +       const handleDisconnect = async () => {
    +         if (!disconnectTarget) return;
    +         try {
    +    -      await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
    +    +      const res = await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
    +             method: 'DELETE',
    +             headers: { Authorization: `Bearer ${token}` },
    +           });
    +    +      if (!res.ok) addToast('Failed to disconnect repo');
    +           setDisconnectTarget(null);
    +           fetchRepos();
    +         } catch {
    +    -      // best effort
    +    +      addToast('Network error disconnecting repo');
    +           setDisconnectTarget(null);
    +         }
    +       };
    +    @@ -56,44 +72,8 @@ function Dashboard() {
    +       };
    +     
    +       return (
    +    -    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
    +    -      <header
    +    -        style={{
    +    -          display: 'flex',
    +    -          alignItems: 'center',
    +    -          justifyContent: 'space-between',
    +    -          padding: '16px 24px',
    +    -          borderBottom: '1px solid #1E293B',
    +    -        }}
    +    -      >
    +    -        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>ForgeGuard</h1>
    +    -        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    +    -          {user?.avatar_url && (
    +    -            <img
    +    -              src={user.avatar_url}
    +    -              alt={user.github_login}
    +    -              style={{ width: 32, height: 32, borderRadius: '50%' }}
    +    -            />
    +    -          )}
    +    -          <span style={{ color: '#94A3B8' }}>{user?.github_login}</span>
    +    -          <button
    +    -            onClick={logout}
    +    -            style={{
    +    -              background: 'transparent',
    +    -              color: '#94A3B8',
    +    -              border: '1px solid #334155',
    +    -              borderRadius: '6px',
    +    -              padding: '6px 16px',
    +    -              cursor: 'pointer',
    +    -              fontSize: '0.875rem',
    +    -            }}
    +    -          >
    +    -            Logout
    +    -          </button>
    +    -        </div>
    +    -      </header>
    +    -
    +    -      <main style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    +    <AppShell sidebarRepos={repos} onReposChange={fetchRepos}>
    +    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +             <div
    +               style={{
    +                 display: 'flex',
    +    @@ -120,17 +100,17 @@ function Dashboard() {
    +             </div>
    +     
    +             {loading ? (
    +    -          <p style={{ color: '#94A3B8' }}>Loading repos...</p>
    +    -        ) : repos.length === 0 ? (
    +    -          <div
    +    -            style={{
    +    -              textAlign: 'center',
    +    -              padding: '64px 24px',
    +    -              color: '#94A3B8',
    +    -            }}
    +    -          >
    +    -            <p>No repos connected yet. Click &quot;Connect a Repo&quot; to get started.</p>
    +    +          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
    +    +            <SkeletonCard />
    +    +            <SkeletonCard />
    +    +            <SkeletonCard />
    +               </div>
    +    +        ) : repos.length === 0 ? (
    +    +          <EmptyState
    +    +            message='No repos connected yet. Click "Connect a Repo" to get started.'
    +    +            actionLabel="Connect a Repo"
    +    +            onAction={() => setShowPicker(true)}
    +    +          />
    +             ) : (
    +               <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
    +                 {repos.map((repo) => (
    +    @@ -143,7 +123,7 @@ function Dashboard() {
    +                 ))}
    +               </div>
    +             )}
    +    -      </main>
    +    +      </div>
    +     
    +           {showPicker && (
    +             <RepoPickerModal
    +    @@ -161,7 +141,7 @@ function Dashboard() {
    +               onCancel={() => setDisconnectTarget(null)}
    +             />
    +           )}
    +    -    </div>
    +    +    </AppShell>
    +       );
    +     }
    +     
    +
    +## Verification
    +- Static analysis: compileall pass on all Python modules
    +- Runtime: all FastAPI endpoints verified via test client including WS
    +- Behavior: pytest 56 passed, vitest 15 passed, all assertions green
    +- Contract compliance: boundaries.json respected, no unauthorized imports, no ORM usage
    +
    +## Notes (optional)
    +- No blockers. All Phase 4 features implemented and tested.
    +
    +## Next Steps
    +- Phase 5 Ship Gate: USER_INSTRUCTIONS.md, boot.ps1, rate limiting, input validation, error handling audit, env variable validation
     
    diff --git a/app/api/routers/ws.py b/app/api/routers/ws.py
    new file mode 100644
    index 0000000..e3a3f61
    --- /dev/null
    +++ b/app/api/routers/ws.py
    @@ -0,0 +1,43 @@
    +"""WebSocket router -- real-time audit result updates."""
    +
    +import jwt as pyjwt
    +from fastapi import APIRouter, WebSocket, WebSocketDisconnect
    +
    +from app.auth import decode_token
    +from app.ws_manager import manager
    +
    +router = APIRouter(tags=["websocket"])
    +
    +
    +@router.websocket("/ws")
    +async def websocket_endpoint(websocket: WebSocket) -> None:
    +    """WebSocket endpoint for real-time audit updates.
    +
    +    Auth via query param: /ws?token=<jwt>
    +    Server sends messages of type "audit_update" with AuditRunSummary payload.
    +    """
    +    token = websocket.query_params.get("token")
    +    if not token:
    +        await websocket.close(code=4001, reason="Missing token")
    +        return
    +
    +    try:
    +        payload = decode_token(token)
    +    except (pyjwt.ExpiredSignatureError, pyjwt.PyJWTError):
    +        await websocket.close(code=4001, reason="Invalid token")
    +        return
    +
    +    user_id = payload.get("sub")
    +    if not user_id:
    +        await websocket.close(code=4001, reason="Invalid token payload")
    +        return
    +
    +    await websocket.accept()
    +    await manager.connect(user_id, websocket)
    +
    +    try:
    +        while True:
    +            # Keep connection alive; ignore client messages
    +            await websocket.receive_text()
    +    except WebSocketDisconnect:
    +        await manager.disconnect(user_id, websocket)
    diff --git a/app/main.py b/app/main.py
    index b012194..4d80f1b 100644
    --- a/app/main.py
    +++ b/app/main.py
    @@ -9,6 +9,7 @@ from app.api.routers.auth import router as auth_router
     from app.api.routers.health import router as health_router
     from app.api.routers.repos import router as repos_router
     from app.api.routers.webhooks import router as webhooks_router
    +from app.api.routers.ws import router as ws_router
     from app.config import settings
     from app.repos.db import close_pool
     
    @@ -41,6 +42,7 @@ def create_app() -> FastAPI:
         application.include_router(auth_router)
         application.include_router(repos_router)
         application.include_router(webhooks_router)
    +    application.include_router(ws_router)
         return application
     
     
    diff --git a/app/repos/audit_repo.py b/app/repos/audit_repo.py
    index f979476..f18d94c 100644
    --- a/app/repos/audit_repo.py
    +++ b/app/repos/audit_repo.py
    @@ -95,11 +95,17 @@ async def get_audit_runs_by_repo(
     
         rows = await pool.fetch(
             """
    -        SELECT id, repo_id, commit_sha, commit_message, commit_author, branch,
    -               status, overall_result, started_at, completed_at, files_checked, created_at
    -        FROM audit_runs
    -        WHERE repo_id = $1
    -        ORDER BY created_at DESC
    +        SELECT a.id, a.repo_id, a.commit_sha, a.commit_message, a.commit_author, a.branch,
    +               a.status, a.overall_result, a.started_at, a.completed_at, a.files_checked, a.created_at,
    +               cs.check_summary
    +        FROM audit_runs a
    +        LEFT JOIN LATERAL (
    +            SELECT string_agg(c.check_code || ':' || c.result, ' ' ORDER BY c.created_at) AS check_summary
    +            FROM audit_checks c
    +            WHERE c.audit_run_id = a.id
    +        ) cs ON true
    +        WHERE a.repo_id = $1
    +        ORDER BY a.created_at DESC
             LIMIT $2 OFFSET $3
             """,
             repo_id,
    diff --git a/app/repos/repo_repo.py b/app/repos/repo_repo.py
    index d5ce62c..0d6c813 100644
    --- a/app/repos/repo_repo.py
    +++ b/app/repos/repo_repo.py
    @@ -88,6 +88,44 @@ async def delete_repo(repo_id: UUID) -> bool:
         return result == "DELETE 1"
     
     
    +async def get_repos_with_health(user_id: UUID) -> list[dict]:
    +    """Fetch repos with recent audit health data for a user.
    +
    +    Returns each repo with last_audit_at and pass/total counts from the
    +    10 most recent completed audit runs.
    +    """
    +    pool = await get_pool()
    +    rows = await pool.fetch(
    +        """
    +        SELECT
    +            r.id, r.user_id, r.github_repo_id, r.full_name,
    +            r.default_branch, r.webhook_id, r.webhook_active,
    +            r.created_at, r.updated_at,
    +            h.last_audit_at,
    +            h.pass_count,
    +            h.total_count
    +        FROM repos r
    +        LEFT JOIN LATERAL (
    +            SELECT
    +                max(a.completed_at) AS last_audit_at,
    +                count(*) FILTER (WHERE a.overall_result = 'PASS') AS pass_count,
    +                count(*) AS total_count
    +            FROM (
    +                SELECT overall_result, completed_at
    +                FROM audit_runs
    +                WHERE repo_id = r.id AND status = 'completed'
    +                ORDER BY created_at DESC
    +                LIMIT 10
    +            ) a
    +        ) h ON true
    +        WHERE r.user_id = $1
    +        ORDER BY r.created_at DESC
    +        """,
    +        user_id,
    +    )
    +    return [dict(r) for r in rows]
    +
    +
     async def update_webhook(
         repo_id: UUID,
         webhook_id: int | None,
    diff --git a/app/services/audit_service.py b/app/services/audit_service.py
    index cd2a72e..aebb1bc 100644
    --- a/app/services/audit_service.py
    +++ b/app/services/audit_service.py
    @@ -12,6 +12,7 @@ from app.repos.audit_repo import (
     )
     from app.repos.repo_repo import get_repo_by_github_id
     from app.repos.user_repo import get_user_by_id
    +from app.ws_manager import manager as ws_manager
     
     
     async def process_push_event(payload: dict) -> dict | None:
    @@ -134,6 +135,22 @@ async def process_push_event(payload: dict) -> dict | None:
                 files_checked=len(files),
             )
     
    +        # Broadcast real-time update via WebSocket
    +        user_id_str = str(repo["user_id"])
    +        await ws_manager.broadcast_audit_update(user_id_str, {
    +            "id": str(audit_run["id"]),
    +            "repo_id": str(repo["id"]),
    +            "commit_sha": commit_sha,
    +            "commit_message": commit_message,
    +            "commit_author": commit_author,
    +            "branch": branch,
    +            "status": "completed",
    +            "overall_result": overall,
    +            "started_at": audit_run["started_at"].isoformat() if audit_run.get("started_at") else None,
    +            "completed_at": None,
    +            "files_checked": len(files),
    +        })
    +
         except Exception:
             await update_audit_run(
                 audit_run_id=audit_run["id"],
    @@ -174,6 +191,7 @@ async def get_repo_audits(
                 "started_at": item["started_at"].isoformat() if item["started_at"] else None,
                 "completed_at": item["completed_at"].isoformat() if item["completed_at"] else None,
                 "files_checked": item["files_checked"],
    +            "check_summary": item.get("check_summary"),
             })
         return result, total
     
    diff --git a/app/services/repo_service.py b/app/services/repo_service.py
    index 2784795..10f035b 100644
    --- a/app/services/repo_service.py
    +++ b/app/services/repo_service.py
    @@ -10,6 +10,7 @@ from app.repos.repo_repo import (
         get_repo_by_github_id,
         get_repo_by_id,
         get_repos_by_user,
    +    get_repos_with_health,
     )
     from app.repos.user_repo import get_user_by_id
     
    @@ -85,18 +86,35 @@ async def disconnect_repo(user_id: UUID, repo_id: UUID) -> None:
     
     
     async def list_connected_repos(user_id: UUID) -> list[dict]:
    -    """List all connected repos for a user with placeholder health data."""
    -    repos = await get_repos_by_user(user_id)
    +    """List all connected repos for a user with computed health data."""
    +    repos = await get_repos_with_health(user_id)
         result = []
         for repo in repos:
    +        total = repo.get("total_count") or 0
    +        pass_count = repo.get("pass_count") or 0
    +
    +        if total == 0:
    +            health = "pending"
    +            rate = None
    +        else:
    +            rate = pass_count / total
    +            if rate == 1.0:
    +                health = "green"
    +            elif rate >= 0.5:
    +                health = "yellow"
    +            else:
    +                health = "red"
    +
    +        last_audit = repo.get("last_audit_at")
    +
             result.append({
                 "id": str(repo["id"]),
                 "full_name": repo["full_name"],
                 "default_branch": repo["default_branch"],
                 "webhook_active": repo["webhook_active"],
    -            "health_score": "pending",
    -            "last_audit_at": None,
    -            "recent_pass_rate": None,
    +            "health_score": health,
    +            "last_audit_at": last_audit.isoformat() if last_audit else None,
    +            "recent_pass_rate": rate,
             })
         return result
     
    diff --git a/app/ws_manager.py b/app/ws_manager.py
    new file mode 100644
    index 0000000..627419e
    --- /dev/null
    +++ b/app/ws_manager.py
    @@ -0,0 +1,51 @@
    +"""WebSocket connection manager for real-time audit updates."""
    +
    +import asyncio
    +import json
    +from uuid import UUID
    +
    +
    +class ConnectionManager:
    +    """Manages active WebSocket connections keyed by user_id."""
    +
    +    def __init__(self) -> None:
    +        self._connections: dict[str, list] = {}  # user_id -> list of websockets
    +        self._lock = asyncio.Lock()
    +
    +    async def connect(self, user_id: str, websocket) -> None:  # noqa: ANN001
    +        """Register a WebSocket connection for a user."""
    +        async with self._lock:
    +            if user_id not in self._connections:
    +                self._connections[user_id] = []
    +            self._connections[user_id].append(websocket)
    +
    +    async def disconnect(self, user_id: str, websocket) -> None:  # noqa: ANN001
    +        """Remove a WebSocket connection for a user."""
    +        async with self._lock:
    +            conns = self._connections.get(user_id, [])
    +            if websocket in conns:
    +                conns.remove(websocket)
    +            if not conns:
    +                self._connections.pop(user_id, None)
    +
    +    async def send_to_user(self, user_id: str, data: dict) -> None:
    +        """Send a JSON message to all connections for a specific user."""
    +        async with self._lock:
    +            conns = list(self._connections.get(user_id, []))
    +        message = json.dumps(data, default=str)
    +        for ws in conns:
    +            try:
    +                await ws.send_text(message)
    +            except Exception:
    +                pass  # dead connection -- will be cleaned up on disconnect
    +
    +    async def broadcast_audit_update(self, user_id: str, audit_summary: dict) -> None:
    +        """Broadcast an audit_update event to the given user."""
    +        payload = {
    +            "type": "audit_update",
    +            "payload": audit_summary,
    +        }
    +        await self.send_to_user(user_id, payload)
    +
    +
    +manager = ConnectionManager()
    diff --git a/tests/test_repo_health.py b/tests/test_repo_health.py
    new file mode 100644
    index 0000000..e5a0e9b
    --- /dev/null
    +++ b/tests/test_repo_health.py
    @@ -0,0 +1,79 @@
    +"""Tests for repo service health score computation."""
    +
    +import pytest
    +from unittest.mock import AsyncMock, patch
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.repo_service.get_repos_with_health")
    +async def test_health_green_all_pass(mock_get):
    +    """All 10 audits passed -> green health, 1.0 rate."""
    +    mock_get.return_value = [{
    +        "id": "repo-1",
    +        "full_name": "org/repo",
    +        "default_branch": "main",
    +        "webhook_active": True,
    +        "total_count": 10,
    +        "pass_count": 10,
    +        "last_audit_at": None,
    +    }]
    +    from app.services.repo_service import list_connected_repos
    +    result = await list_connected_repos("user-1")
    +    assert result[0]["health_score"] == "green"
    +    assert result[0]["recent_pass_rate"] == 1.0
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.repo_service.get_repos_with_health")
    +async def test_health_red_low_pass(mock_get):
    +    """Less than half pass -> red health."""
    +    mock_get.return_value = [{
    +        "id": "repo-1",
    +        "full_name": "org/repo",
    +        "default_branch": "main",
    +        "webhook_active": True,
    +        "total_count": 10,
    +        "pass_count": 3,
    +        "last_audit_at": None,
    +    }]
    +    from app.services.repo_service import list_connected_repos
    +    result = await list_connected_repos("user-1")
    +    assert result[0]["health_score"] == "red"
    +    assert result[0]["recent_pass_rate"] == 0.3
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.repo_service.get_repos_with_health")
    +async def test_health_yellow_mixed(mock_get):
    +    """50-99% pass rate -> yellow health."""
    +    mock_get.return_value = [{
    +        "id": "repo-1",
    +        "full_name": "org/repo",
    +        "default_branch": "main",
    +        "webhook_active": True,
    +        "total_count": 10,
    +        "pass_count": 7,
    +        "last_audit_at": None,
    +    }]
    +    from app.services.repo_service import list_connected_repos
    +    result = await list_connected_repos("user-1")
    +    assert result[0]["health_score"] == "yellow"
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.repo_service.get_repos_with_health")
    +async def test_health_pending_no_audits(mock_get):
    +    """No audit runs -> pending health, null rate."""
    +    mock_get.return_value = [{
    +        "id": "repo-1",
    +        "full_name": "org/repo",
    +        "default_branch": "main",
    +        "webhook_active": True,
    +        "total_count": 0,
    +        "pass_count": 0,
    +        "last_audit_at": None,
    +    }]
    +    from app.services.repo_service import list_connected_repos
    +    result = await list_connected_repos("user-1")
    +    assert result[0]["health_score"] == "pending"
    +    assert result[0]["recent_pass_rate"] is None
    diff --git a/tests/test_repos_router.py b/tests/test_repos_router.py
    index 05e7fe3..c647569 100644
    --- a/tests/test_repos_router.py
    +++ b/tests/test_repos_router.py
    @@ -43,7 +43,7 @@ def _auth_header():
     
     
     @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    -@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
    +@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
     def test_list_repos_returns_items(mock_get_repos, mock_get_user):
         mock_get_user.return_value = MOCK_USER
         mock_get_repos.return_value = [
    @@ -57,6 +57,9 @@ def test_list_repos_returns_items(mock_get_repos, mock_get_user):
                 "webhook_active": True,
                 "created_at": "2025-01-01T00:00:00Z",
                 "updated_at": "2025-01-01T00:00:00Z",
    +            "total_count": 5,
    +            "pass_count": 5,
    +            "last_audit_at": None,
             }
         ]
     
    @@ -66,10 +69,11 @@ def test_list_repos_returns_items(mock_get_repos, mock_get_user):
         assert "items" in data
         assert len(data["items"]) == 1
         assert data["items"][0]["full_name"] == "octocat/hello-world"
    +    assert data["items"][0]["health_score"] == "green"
     
     
     @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    -@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
    +@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
     def test_list_repos_empty(mock_get_repos, mock_get_user):
         mock_get_user.return_value = MOCK_USER
         mock_get_repos.return_value = []
    diff --git a/tests/test_ws_manager.py b/tests/test_ws_manager.py
    new file mode 100644
    index 0000000..441a076
    --- /dev/null
    +++ b/tests/test_ws_manager.py
    @@ -0,0 +1,90 @@
    +"""Tests for WebSocket connection manager."""
    +
    +import asyncio
    +
    +import pytest
    +
    +from app.ws_manager import ConnectionManager
    +
    +
    +class FakeWebSocket:
    +    """Fake WebSocket for testing send_text."""
    +
    +    def __init__(self):
    +        self.messages: list[str] = []
    +        self.closed = False
    +
    +    async def send_text(self, text: str) -> None:
    +        if self.closed:
    +            raise RuntimeError("WebSocket closed")
    +        self.messages.append(text)
    +
    +
    +@pytest.mark.asyncio
    +async def test_connect_and_send():
    +    """Messages reach connected sockets."""
    +    mgr = ConnectionManager()
    +    ws = FakeWebSocket()
    +    await mgr.connect("user-1", ws)
    +    await mgr.send_to_user("user-1", {"hello": "world"})
    +    assert len(ws.messages) == 1
    +    assert '"hello"' in ws.messages[0]
    +
    +
    +@pytest.mark.asyncio
    +async def test_disconnect_removes_socket():
    +    """After disconnect, no messages reach the socket."""
    +    mgr = ConnectionManager()
    +    ws = FakeWebSocket()
    +    await mgr.connect("user-1", ws)
    +    await mgr.disconnect("user-1", ws)
    +    await mgr.send_to_user("user-1", {"hello": "world"})
    +    assert len(ws.messages) == 0
    +
    +
    +@pytest.mark.asyncio
    +async def test_multiple_connections():
    +    """Multiple sockets for one user all receive messages."""
    +    mgr = ConnectionManager()
    +    ws1 = FakeWebSocket()
    +    ws2 = FakeWebSocket()
    +    await mgr.connect("user-1", ws1)
    +    await mgr.connect("user-1", ws2)
    +    await mgr.send_to_user("user-1", {"data": 1})
    +    assert len(ws1.messages) == 1
    +    assert len(ws2.messages) == 1
    +
    +
    +@pytest.mark.asyncio
    +async def test_send_to_nonexistent_user():
    +    """Sending to a user with no connections does not error."""
    +    mgr = ConnectionManager()
    +    await mgr.send_to_user("nobody", {"data": 1})
    +
    +
    +@pytest.mark.asyncio
    +async def test_broadcast_audit_update():
    +    """broadcast_audit_update sends correctly typed message."""
    +    mgr = ConnectionManager()
    +    ws = FakeWebSocket()
    +    await mgr.connect("user-1", ws)
    +    await mgr.broadcast_audit_update("user-1", {"id": "abc", "status": "completed"})
    +    assert len(ws.messages) == 1
    +    import json
    +    msg = json.loads(ws.messages[0])
    +    assert msg["type"] == "audit_update"
    +    assert msg["payload"]["id"] == "abc"
    +
    +
    +@pytest.mark.asyncio
    +async def test_dead_socket_ignored():
    +    """Dead socket doesn't prevent messages to other sockets."""
    +    mgr = ConnectionManager()
    +    ws_dead = FakeWebSocket()
    +    ws_dead.closed = True
    +    ws_live = FakeWebSocket()
    +    await mgr.connect("user-1", ws_dead)
    +    await mgr.connect("user-1", ws_live)
    +    await mgr.send_to_user("user-1", {"x": 1})
    +    assert len(ws_live.messages) == 1
    +    assert len(ws_dead.messages) == 0
    diff --git a/tests/test_ws_router.py b/tests/test_ws_router.py
    new file mode 100644
    index 0000000..0ff6c1e
    --- /dev/null
    +++ b/tests/test_ws_router.py
    @@ -0,0 +1,56 @@
    +"""Tests for WebSocket router endpoint."""
    +
    +from unittest.mock import AsyncMock, patch
    +
    +import pytest
    +from fastapi.testclient import TestClient
    +
    +from app.main import create_app
    +
    +
    +@pytest.fixture
    +def client():
    +    app = create_app()
    +    return TestClient(app)
    +
    +
    +def _make_token_payload(user_id: str = "test-user-id"):
    +    return {"sub": user_id, "github_login": "testuser"}
    +
    +
    +def test_ws_rejects_missing_token(client):
    +    """WebSocket connection without token should be rejected."""
    +    with pytest.raises(Exception):
    +        with client.websocket_connect("/ws"):
    +            pass
    +
    +
    +def test_ws_rejects_invalid_token(client):
    +    """WebSocket connection with invalid token should be rejected."""
    +    with pytest.raises(Exception):
    +        with client.websocket_connect("/ws?token=badtoken"):
    +            pass
    +
    +
    +@patch("app.api.routers.ws.decode_token")
    +def test_ws_accepts_valid_token(mock_decode, client):
    +    """WebSocket connection with valid token should be accepted."""
    +    mock_decode.return_value = _make_token_payload()
    +    with client.websocket_connect("/ws?token=validtoken") as ws:
    +        # Connection established - no immediate message expected
    +        # Just verify it connected
    +        assert ws is not None
    +
    +
    +@patch("app.api.routers.ws.decode_token")
    +@patch("app.api.routers.ws.manager")
    +def test_ws_connects_and_disconnects(mock_manager, mock_decode, client):
    +    """WebSocket lifecycle: connect -> manager.connect called."""
    +    mock_decode.return_value = _make_token_payload("uid-123")
    +    mock_manager.connect = AsyncMock()
    +    mock_manager.disconnect = AsyncMock()
    +
    +    with client.websocket_connect("/ws?token=validtoken"):
    +        mock_manager.connect.assert_called_once()
    +        call_args = mock_manager.connect.call_args
    +        assert call_args[0][0] == "uid-123"
    diff --git a/web/src/App.tsx b/web/src/App.tsx
    index a7bc43a..b85d295 100644
    --- a/web/src/App.tsx
    +++ b/web/src/App.tsx
    @@ -5,6 +5,7 @@ import Dashboard from './pages/Dashboard';
     import CommitTimeline from './pages/CommitTimeline';
     import AuditDetailPage from './pages/AuditDetail';
     import { AuthProvider, useAuth } from './context/AuthContext';
    +import { ToastProvider } from './context/ToastContext';
     
     function ProtectedRoute({ children }: { children: React.ReactNode }) {
       const { token } = useAuth();
    @@ -12,52 +13,42 @@ function ProtectedRoute({ children }: { children: React.ReactNode }) {
       return <>{children}</>;
     }
     
    -function AppLayout({ children }: { children: React.ReactNode }) {
    -  return (
    -    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
    -      {children}
    -    </div>
    -  );
    -}
    -
     function App() {
       return (
         <AuthProvider>
    -      <BrowserRouter>
    -        <Routes>
    -          <Route path="/login" element={<Login />} />
    -          <Route path="/auth/callback" element={<AuthCallback />} />
    -          <Route
    -            path="/"
    -            element={
    -              <ProtectedRoute>
    -                <Dashboard />
    -              </ProtectedRoute>
    -            }
    -          />
    -          <Route
    -            path="/repos/:repoId"
    -            element={
    -              <ProtectedRoute>
    -                <AppLayout>
    +      <ToastProvider>
    +        <BrowserRouter>
    +          <Routes>
    +            <Route path="/login" element={<Login />} />
    +            <Route path="/auth/callback" element={<AuthCallback />} />
    +            <Route
    +              path="/"
    +              element={
    +                <ProtectedRoute>
    +                  <Dashboard />
    +                </ProtectedRoute>
    +              }
    +            />
    +            <Route
    +              path="/repos/:repoId"
    +              element={
    +                <ProtectedRoute>
                       <CommitTimeline />
    -                </AppLayout>
    -              </ProtectedRoute>
    -            }
    -          />
    -          <Route
    -            path="/repos/:repoId/audits/:auditId"
    -            element={
    -              <ProtectedRoute>
    -                <AppLayout>
    +                </ProtectedRoute>
    +              }
    +            />
    +            <Route
    +              path="/repos/:repoId/audits/:auditId"
    +              element={
    +                <ProtectedRoute>
                       <AuditDetailPage />
    -                </AppLayout>
    -              </ProtectedRoute>
    -            }
    -          />
    -          <Route path="*" element={<Navigate to="/" replace />} />
    -        </Routes>
    -      </BrowserRouter>
    +                </ProtectedRoute>
    +              }
    +            />
    +            <Route path="*" element={<Navigate to="/" replace />} />
    +          </Routes>
    +        </BrowserRouter>
    +      </ToastProvider>
         </AuthProvider>
       );
     }
    diff --git a/web/src/__tests__/App.test.tsx b/web/src/__tests__/App.test.tsx
    index b9cf339..ea10fbc 100644
    --- a/web/src/__tests__/App.test.tsx
    +++ b/web/src/__tests__/App.test.tsx
    @@ -1,10 +1,12 @@
     import { describe, it, expect } from 'vitest';
    -import { render, screen } from '@testing-library/react';
    +import { render, screen, fireEvent } from '@testing-library/react';
     import Login from '../pages/Login';
     import HealthBadge from '../components/HealthBadge';
     import ConfirmDialog from '../components/ConfirmDialog';
     import ResultBadge from '../components/ResultBadge';
     import CheckResultCard from '../components/CheckResultCard';
    +import Skeleton, { SkeletonCard, SkeletonRow } from '../components/Skeleton';
    +import EmptyState from '../components/EmptyState';
     
     describe('Login', () => {
       it('renders the sign in button', () => {
    @@ -82,3 +84,40 @@ describe('CheckResultCard', () => {
         expect(screen.getByText('Boundary compliance')).toBeInTheDocument();
       });
     });
    +
    +describe('Skeleton', () => {
    +  it('renders skeleton element', () => {
    +    render(<Skeleton />);
    +    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
    +  });
    +
    +  it('renders SkeletonCard', () => {
    +    render(<SkeletonCard />);
    +    const skeletons = screen.getAllByTestId('skeleton');
    +    expect(skeletons.length).toBeGreaterThan(0);
    +  });
    +
    +  it('renders SkeletonRow', () => {
    +    render(<SkeletonRow />);
    +    const skeletons = screen.getAllByTestId('skeleton');
    +    expect(skeletons.length).toBeGreaterThan(0);
    +  });
    +});
    +
    +describe('EmptyState', () => {
    +  it('renders message', () => {
    +    render(<EmptyState message="Nothing here" />);
    +    expect(screen.getByText('Nothing here')).toBeInTheDocument();
    +  });
    +
    +  it('renders empty-state test id', () => {
    +    render(<EmptyState message="Test" />);
    +    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    +  });
    +
    +  it('renders action button when provided', () => {
    +    const fn = () => {};
    +    render(<EmptyState message="Empty" actionLabel="Add Item" onAction={fn} />);
    +    expect(screen.getByText('Add Item')).toBeInTheDocument();
    +  });
    +});
    diff --git a/web/src/components/AppShell.tsx b/web/src/components/AppShell.tsx
    new file mode 100644
    index 0000000..f3102d9
    --- /dev/null
    +++ b/web/src/components/AppShell.tsx
    @@ -0,0 +1,201 @@
    +import { useState, useEffect, useCallback, type ReactNode } from 'react';
    +import { useNavigate, useLocation } from 'react-router-dom';
    +import { useAuth } from '../context/AuthContext';
    +import HealthBadge from './HealthBadge';
    +
    +const API_BASE = import.meta.env.VITE_API_URL ?? '';
    +
    +interface SidebarRepo {
    +  id: string;
    +  full_name: string;
    +  health_score: string;
    +}
    +
    +interface AppShellProps {
    +  children: ReactNode;
    +  sidebarRepos?: SidebarRepo[];
    +  onReposChange?: () => void;
    +}
    +
    +function AppShell({ children, sidebarRepos, onReposChange }: AppShellProps) {
    +  const { user, token, logout } = useAuth();
    +  const navigate = useNavigate();
    +  const location = useLocation();
    +  const [collapsed, setCollapsed] = useState(false);
    +  const [repos, setRepos] = useState<SidebarRepo[]>(sidebarRepos ?? []);
    +
    +  useEffect(() => {
    +    if (sidebarRepos) {
    +      setRepos(sidebarRepos);
    +      return;
    +    }
    +    // Load repos for sidebar if not provided
    +    const load = async () => {
    +      try {
    +        const res = await fetch(`${API_BASE}/repos`, {
    +          headers: { Authorization: `Bearer ${token}` },
    +        });
    +        if (res.ok) {
    +          const data = await res.json();
    +          setRepos(data.items);
    +        }
    +      } catch {
    +        // best effort
    +      }
    +    };
    +    load();
    +  }, [token, sidebarRepos]);
    +
    +  // Responsive: collapse sidebar below 1024px
    +  useEffect(() => {
    +    const check = () => setCollapsed(window.innerWidth < 1024);
    +    check();
    +    window.addEventListener('resize', check);
    +    return () => window.removeEventListener('resize', check);
    +  }, []);
    +
    +  const sidebarWidth = collapsed ? 0 : 240;
    +
    +  return (
    +    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: '#0F172A', color: '#F8FAFC' }}>
    +      {/* Header */}
    +      <header
    +        style={{
    +          display: 'flex',
    +          alignItems: 'center',
    +          justifyContent: 'space-between',
    +          padding: '12px 24px',
    +          borderBottom: '1px solid #1E293B',
    +          background: '#0F172A',
    +          zIndex: 10,
    +        }}
    +      >
    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    +          {collapsed && (
    +            <button
    +              onClick={() => setCollapsed(false)}
    +              aria-label="Open menu"
    +              style={{
    +                background: 'transparent',
    +                color: '#94A3B8',
    +                border: '1px solid #334155',
    +                borderRadius: '6px',
    +                padding: '4px 8px',
    +                cursor: 'pointer',
    +                fontSize: '1rem',
    +              }}
    +            >
    +              &#9776;
    +            </button>
    +          )}
    +          <h1
    +            onClick={() => navigate('/')}
    +            style={{ fontSize: '1.15rem', fontWeight: 700, cursor: 'pointer', margin: 0 }}
    +          >
    +            ForgeGuard
    +          </h1>
    +        </div>
    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    +          {user?.avatar_url && (
    +            <img
    +              src={user.avatar_url}
    +              alt={user.github_login}
    +              style={{ width: 28, height: 28, borderRadius: '50%' }}
    +            />
    +          )}
    +          <span style={{ color: '#94A3B8', fontSize: '0.85rem' }}>{user?.github_login}</span>
    +          <button
    +            onClick={logout}
    +            style={{
    +              background: 'transparent',
    +              color: '#94A3B8',
    +              border: '1px solid #334155',
    +              borderRadius: '6px',
    +              padding: '4px 14px',
    +              cursor: 'pointer',
    +              fontSize: '0.8rem',
    +            }}
    +          >
    +            Logout
    +          </button>
    +        </div>
    +      </header>
    +
    +      <div style={{ display: 'flex', flex: 1 }}>
    +        {/* Sidebar */}
    +        {!collapsed && (
    +          <aside
    +            style={{
    +              width: sidebarWidth,
    +              borderRight: '1px solid #1E293B',
    +              padding: '16px 0',
    +              overflowY: 'auto',
    +              flexShrink: 0,
    +              background: '#0F172A',
    +            }}
    +          >
    +            <div style={{ padding: '0 16px 12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
    +              <span style={{ color: '#94A3B8', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
    +                Repos
    +              </span>
    +              {window.innerWidth < 1024 && (
    +                <button
    +                  onClick={() => setCollapsed(true)}
    +                  aria-label="Close menu"
    +                  style={{
    +                    background: 'transparent',
    +                    color: '#94A3B8',
    +                    border: 'none',
    +                    cursor: 'pointer',
    +                    fontSize: '1rem',
    +                  }}
    +                >
    +                  &times;
    +                </button>
    +              )}
    +            </div>
    +            {repos.length === 0 ? (
    +              <div style={{ padding: '12px 16px', color: '#64748B', fontSize: '0.8rem' }}>
    +                No repos connected
    +              </div>
    +            ) : (
    +              repos.map((repo) => {
    +                const isActive = location.pathname.startsWith(`/repos/${repo.id}`);
    +                return (
    +                  <div
    +                    key={repo.id}
    +                    onClick={() => navigate(`/repos/${repo.id}`)}
    +                    style={{
    +                      display: 'flex',
    +                      alignItems: 'center',
    +                      gap: '8px',
    +                      padding: '8px 16px',
    +                      cursor: 'pointer',
    +                      background: isActive ? '#1E293B' : 'transparent',
    +                      borderLeft: isActive ? '3px solid #2563EB' : '3px solid transparent',
    +                      transition: 'background 0.15s',
    +                      fontSize: '0.8rem',
    +                    }}
    +                  >
    +                    <HealthBadge score={repo.health_score} size={8} />
    +                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
    +                      {repo.full_name}
    +                    </span>
    +                  </div>
    +                );
    +              })
    +            )}
    +          </aside>
    +        )}
    +
    +        {/* Main */}
    +        <main style={{ flex: 1, overflow: 'auto' }}>
    +          {children}
    +        </main>
    +      </div>
    +    </div>
    +  );
    +}
    +
    +export type { SidebarRepo };
    +export default AppShell;
    diff --git a/web/src/components/CommitRow.tsx b/web/src/components/CommitRow.tsx
    index 5db2162..179dfda 100644
    --- a/web/src/components/CommitRow.tsx
    +++ b/web/src/components/CommitRow.tsx
    @@ -11,6 +11,7 @@ interface AuditRun {
       started_at: string | null;
       completed_at: string | null;
       files_checked: number;
    +  check_summary: string | null;
     }
     
     interface CommitRowProps {
    @@ -71,7 +72,12 @@ function CommitRow({ audit, onClick }: CommitRowProps) {
               </div>
             </div>
           </div>
    -      <div style={{ flexShrink: 0, marginLeft: '12px' }}>
    +      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, marginLeft: '12px' }}>
    +        {audit.check_summary && (
    +          <span style={{ color: '#64748B', fontSize: '0.65rem', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
    +            {audit.check_summary}
    +          </span>
    +        )}
             <ResultBadge result={audit.overall_result} />
           </div>
         </div>
    diff --git a/web/src/components/EmptyState.tsx b/web/src/components/EmptyState.tsx
    new file mode 100644
    index 0000000..429e852
    --- /dev/null
    +++ b/web/src/components/EmptyState.tsx
    @@ -0,0 +1,38 @@
    +interface EmptyStateProps {
    +  message: string;
    +  actionLabel?: string;
    +  onAction?: () => void;
    +}
    +
    +function EmptyState({ message, actionLabel, onAction }: EmptyStateProps) {
    +  return (
    +    <div
    +      data-testid="empty-state"
    +      style={{
    +        textAlign: 'center',
    +        padding: '64px 24px',
    +        color: '#94A3B8',
    +      }}
    +    >
    +      <p style={{ marginBottom: actionLabel ? '16px' : '0' }}>{message}</p>
    +      {actionLabel && onAction && (
    +        <button
    +          onClick={onAction}
    +          style={{
    +            background: '#2563EB',
    +            color: '#fff',
    +            border: 'none',
    +            borderRadius: '6px',
    +            padding: '8px 16px',
    +            cursor: 'pointer',
    +            fontSize: '0.85rem',
    +          }}
    +        >
    +          {actionLabel}
    +        </button>
    +      )}
    +    </div>
    +  );
    +}
    +
    +export default EmptyState;
    diff --git a/web/src/components/Skeleton.tsx b/web/src/components/Skeleton.tsx
    new file mode 100644
    index 0000000..19bb14d
    --- /dev/null
    +++ b/web/src/components/Skeleton.tsx
    @@ -0,0 +1,68 @@
    +interface SkeletonProps {
    +  width?: string;
    +  height?: string;
    +  borderRadius?: string;
    +  style?: React.CSSProperties;
    +}
    +
    +function Skeleton({ width = '100%', height = '16px', borderRadius = '4px', style }: SkeletonProps) {
    +  return (
    +    <div
    +      data-testid="skeleton"
    +      style={{
    +        width,
    +        height,
    +        borderRadius,
    +        background: 'linear-gradient(90deg, #1E293B 25%, #2D3B4F 50%, #1E293B 75%)',
    +        backgroundSize: '200% 100%',
    +        animation: 'skeleton-shimmer 1.5s ease-in-out infinite',
    +        ...style,
    +      }}
    +    />
    +  );
    +}
    +
    +export function SkeletonCard() {
    +  return (
    +    <div
    +      style={{
    +        background: '#1E293B',
    +        borderRadius: '8px',
    +        padding: '16px 20px',
    +        display: 'flex',
    +        alignItems: 'center',
    +        gap: '12px',
    +      }}
    +    >
    +      <Skeleton width="12px" height="12px" borderRadius="50%" />
    +      <div style={{ flex: 1 }}>
    +        <Skeleton width="40%" height="14px" style={{ marginBottom: '8px' }} />
    +        <Skeleton width="60%" height="10px" />
    +      </div>
    +    </div>
    +  );
    +}
    +
    +export function SkeletonRow() {
    +  return (
    +    <div
    +      style={{
    +        background: '#1E293B',
    +        borderRadius: '6px',
    +        padding: '12px 16px',
    +        display: 'flex',
    +        alignItems: 'center',
    +        gap: '12px',
    +      }}
    +    >
    +      <Skeleton width="56px" height="14px" />
    +      <div style={{ flex: 1 }}>
    +        <Skeleton width="50%" height="12px" style={{ marginBottom: '6px' }} />
    +        <Skeleton width="30%" height="10px" />
    +      </div>
    +      <Skeleton width="48px" height="20px" borderRadius="4px" />
    +    </div>
    +  );
    +}
    +
    +export default Skeleton;
    diff --git a/web/src/context/ToastContext.tsx b/web/src/context/ToastContext.tsx
    new file mode 100644
    index 0000000..c1ae210
    --- /dev/null
    +++ b/web/src/context/ToastContext.tsx
    @@ -0,0 +1,99 @@
    +import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';
    +
    +interface Toast {
    +  id: number;
    +  message: string;
    +  type: 'error' | 'success' | 'info';
    +}
    +
    +interface ToastContextValue {
    +  addToast: (message: string, type?: Toast['type']) => void;
    +}
    +
    +const ToastContext = createContext<ToastContextValue | null>(null);
    +
    +let nextId = 1;
    +
    +export function ToastProvider({ children }: { children: ReactNode }) {
    +  const [toasts, setToasts] = useState<Toast[]>([]);
    +
    +  const addToast = useCallback((message: string, type: Toast['type'] = 'error') => {
    +    const id = nextId++;
    +    setToasts((prev) => [...prev, { id, message, type }]);
    +    setTimeout(() => {
    +      setToasts((prev) => prev.filter((t) => t.id !== id));
    +    }, 5000);
    +  }, []);
    +
    +  const removeToast = useCallback((id: number) => {
    +    setToasts((prev) => prev.filter((t) => t.id !== id));
    +  }, []);
    +
    +  const COLORS: Record<string, { bg: string; border: string }> = {
    +    error: { bg: '#7F1D1D', border: '#EF4444' },
    +    success: { bg: '#14532D', border: '#22C55E' },
    +    info: { bg: '#1E3A5F', border: '#2563EB' },
    +  };
    +
    +  return (
    +    <ToastContext.Provider value={{ addToast }}>
    +      {children}
    +      <div
    +        style={{
    +          position: 'fixed',
    +          bottom: '24px',
    +          right: '24px',
    +          zIndex: 1000,
    +          display: 'flex',
    +          flexDirection: 'column',
    +          gap: '8px',
    +          maxWidth: '360px',
    +        }}
    +      >
    +        {toasts.map((toast) => {
    +          const colors = COLORS[toast.type] ?? COLORS.info;
    +          return (
    +            <div
    +              key={toast.id}
    +              role="alert"
    +              style={{
    +                background: colors.bg,
    +                borderLeft: `3px solid ${colors.border}`,
    +                borderRadius: '6px',
    +                padding: '12px 16px',
    +                fontSize: '0.85rem',
    +                color: '#F8FAFC',
    +                display: 'flex',
    +                alignItems: 'center',
    +                justifyContent: 'space-between',
    +                gap: '8px',
    +              }}
    +            >
    +              <span>{toast.message}</span>
    +              <button
    +                onClick={() => removeToast(toast.id)}
    +                style={{
    +                  background: 'transparent',
    +                  color: '#94A3B8',
    +                  border: 'none',
    +                  cursor: 'pointer',
    +                  fontSize: '1rem',
    +                  padding: 0,
    +                  lineHeight: 1,
    +                }}
    +              >
    +                &times;
    +              </button>
    +            </div>
    +          );
    +        })}
    +      </div>
    +    </ToastContext.Provider>
    +  );
    +}
    +
    +export function useToast(): ToastContextValue {
    +  const ctx = useContext(ToastContext);
    +  if (!ctx) throw new Error('useToast must be used within ToastProvider');
    +  return ctx;
    +}
    diff --git a/web/src/hooks/useWebSocket.ts b/web/src/hooks/useWebSocket.ts
    new file mode 100644
    index 0000000..86131f1
    --- /dev/null
    +++ b/web/src/hooks/useWebSocket.ts
    @@ -0,0 +1,53 @@
    +import { useEffect, useRef, useCallback } from 'react';
    +import { useAuth } from '../context/AuthContext';
    +
    +type MessageHandler = (data: { type: string; payload: unknown }) => void;
    +
    +const WS_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/^http/, 'ws');
    +
    +export function useWebSocket(onMessage: MessageHandler) {
    +  const { token } = useAuth();
    +  const wsRef = useRef<WebSocket | null>(null);
    +  const handlerRef = useRef(onMessage);
    +  handlerRef.current = onMessage;
    +
    +  const connect = useCallback(() => {
    +    if (!token) return;
    +
    +    const url = `${WS_BASE}/ws?token=${encodeURIComponent(token)}`;
    +    const ws = new WebSocket(url);
    +
    +    ws.onmessage = (event) => {
    +      try {
    +        const data = JSON.parse(event.data);
    +        handlerRef.current(data);
    +      } catch {
    +        // ignore malformed messages
    +      }
    +    };
    +
    +    ws.onclose = () => {
    +      // Reconnect after 3 seconds
    +      setTimeout(() => {
    +        if (wsRef.current === ws) {
    +          connect();
    +        }
    +      }, 3000);
    +    };
    +
    +    ws.onerror = () => {
    +      ws.close();
    +    };
    +
    +    wsRef.current = ws;
    +  }, [token]);
    +
    +  useEffect(() => {
    +    connect();
    +    return () => {
    +      const ws = wsRef.current;
    +      wsRef.current = null;
    +      if (ws) ws.close();
    +    };
    +  }, [connect]);
    +}
    diff --git a/web/src/index.css b/web/src/index.css
    index bacf9f8..23889d9 100644
    --- a/web/src/index.css
    +++ b/web/src/index.css
    @@ -11,3 +11,8 @@ body {
       -webkit-font-smoothing: antialiased;
       -moz-osx-font-smoothing: grayscale;
     }
    +
    +@keyframes skeleton-shimmer {
    +  0% { background-position: 200% 0; }
    +  100% { background-position: -200% 0; }
    +}
    diff --git a/web/src/pages/AuditDetail.tsx b/web/src/pages/AuditDetail.tsx
    index 3e51080..9fa2e71 100644
    --- a/web/src/pages/AuditDetail.tsx
    +++ b/web/src/pages/AuditDetail.tsx
    @@ -1,9 +1,12 @@
     import { useState, useEffect } from 'react';
     import { useParams, useNavigate } from 'react-router-dom';
     import { useAuth } from '../context/AuthContext';
    +import { useToast } from '../context/ToastContext';
    +import AppShell from '../components/AppShell';
     import ResultBadge from '../components/ResultBadge';
     import CheckResultCard from '../components/CheckResultCard';
     import type { CheckResultData } from '../components/CheckResultCard';
    +import Skeleton from '../components/Skeleton';
     
     const API_BASE = import.meta.env.VITE_API_URL ?? '';
     
    @@ -24,6 +27,7 @@ interface AuditDetail {
     function AuditDetailPage() {
       const { repoId, auditId } = useParams<{ repoId: string; auditId: string }>();
       const { token } = useAuth();
    +  const { addToast } = useToast();
       const navigate = useNavigate();
     
       const [detail, setDetail] = useState<AuditDetail | null>(null);
    @@ -38,29 +42,47 @@ function AuditDetailPage() {
             );
             if (res.ok) {
               setDetail(await res.json());
    +        } else {
    +          addToast('Failed to load audit detail');
             }
           } catch {
    -        // network error
    +        addToast('Network error loading audit detail');
           } finally {
             setLoading(false);
           }
         };
         fetchDetail();
    -  }, [repoId, auditId, token]);
    +  }, [repoId, auditId, token, addToast]);
     
       if (loading) {
         return (
    -      <div style={{ padding: '24px', color: '#94A3B8' }}>
    -        Loading audit detail...
    -      </div>
    +      <AppShell>
    +        <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +          <Skeleton width="80px" height="28px" style={{ marginBottom: '20px' }} />
    +          <div style={{ background: '#1E293B', borderRadius: '8px', padding: '20px', marginBottom: '16px' }}>
    +            <Skeleton width="30%" height="14px" style={{ marginBottom: '12px' }} />
    +            <Skeleton width="60%" height="12px" style={{ marginBottom: '12px' }} />
    +            <Skeleton width="100px" height="24px" />
    +          </div>
    +          <Skeleton width="120px" height="16px" style={{ marginBottom: '12px' }} />
    +          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
    +            <Skeleton width="50%" height="14px" />
    +          </div>
    +          <div style={{ background: '#1E293B', borderRadius: '6px', padding: '14px 18px', marginBottom: '8px' }}>
    +            <Skeleton width="50%" height="14px" />
    +          </div>
    +        </div>
    +      </AppShell>
         );
       }
     
       if (!detail) {
         return (
    -      <div style={{ padding: '24px', color: '#94A3B8' }}>
    -        Audit not found.
    -      </div>
    +      <AppShell>
    +        <div style={{ padding: '24px', color: '#94A3B8' }}>
    +          Audit not found.
    +        </div>
    +      </AppShell>
         );
       }
     
    @@ -70,67 +92,69 @@ function AuditDetailPage() {
           : null;
     
       return (
    -    <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    -      <button
    -        onClick={() => navigate(`/repos/${repoId}`)}
    -        style={{
    -          background: 'transparent',
    -          color: '#94A3B8',
    -          border: '1px solid #334155',
    -          borderRadius: '6px',
    -          padding: '6px 12px',
    -          cursor: 'pointer',
    -          fontSize: '0.8rem',
    -          marginBottom: '20px',
    -        }}
    -      >
    -        Back to Timeline
    -      </button>
    +    <AppShell>
    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +        <button
    +          onClick={() => navigate(`/repos/${repoId}`)}
    +          style={{
    +            background: 'transparent',
    +            color: '#94A3B8',
    +            border: '1px solid #334155',
    +            borderRadius: '6px',
    +            padding: '6px 12px',
    +            cursor: 'pointer',
    +            fontSize: '0.8rem',
    +            marginBottom: '20px',
    +          }}
    +        >
    +          Back to Timeline
    +        </button>
     
    -      {/* Commit Info */}
    -      <div
    -        style={{
    -          background: '#1E293B',
    -          borderRadius: '8px',
    -          padding: '20px',
    -          marginBottom: '16px',
    -        }}
    -      >
    -        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
    -          <a
    -            href={`https://github.com/commit/${detail.commit_sha}`}
    -            target="_blank"
    -            rel="noopener noreferrer"
    -            style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem' }}
    -          >
    -            {detail.commit_sha.substring(0, 7)}
    -          </a>
    -          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    -            {detail.branch} &middot; {detail.commit_author}
    -          </span>
    +        {/* Commit Info */}
    +        <div
    +          style={{
    +            background: '#1E293B',
    +            borderRadius: '8px',
    +            padding: '20px',
    +            marginBottom: '16px',
    +          }}
    +        >
    +          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
    +            <a
    +              href={`https://github.com/commit/${detail.commit_sha}`}
    +              target="_blank"
    +              rel="noopener noreferrer"
    +              style={{ color: '#2563EB', fontFamily: 'monospace', fontSize: '0.85rem' }}
    +            >
    +              {detail.commit_sha.substring(0, 7)}
    +            </a>
    +            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +              {detail.branch} &middot; {detail.commit_author}
    +            </span>
    +          </div>
    +          <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
    +            {detail.commit_message}
    +          </p>
    +          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
    +            <ResultBadge result={detail.overall_result} size="large" />
    +            <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    +              {detail.files_checked} files checked
    +              {duration && <span> &middot; {duration}</span>}
    +            </span>
    +          </div>
             </div>
    -        <p style={{ margin: '0 0 12px 0', fontSize: '0.9rem' }}>
    -          {detail.commit_message}
    -        </p>
    -        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
    -          <ResultBadge result={detail.overall_result} size="large" />
    -          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>
    -            {detail.files_checked} files checked
    -            {duration && <span> &middot; {duration}</span>}
    -          </span>
    -        </div>
    -      </div>
     
    -      {/* Check Results */}
    -      <h3 style={{ fontSize: '1rem', marginBottom: '12px' }}>Check Results</h3>
    -      {detail.checks.length === 0 ? (
    -        <p style={{ color: '#94A3B8' }}>No check results.</p>
    -      ) : (
    -        detail.checks.map((check) => (
    -          <CheckResultCard key={check.id} check={check} />
    -        ))
    -      )}
    -    </div>
    +        {/* Check Results */}
    +        <h3 style={{ fontSize: '1rem', marginBottom: '12px' }}>Check Results</h3>
    +        {detail.checks.length === 0 ? (
    +          <p style={{ color: '#94A3B8' }}>No check results.</p>
    +        ) : (
    +          detail.checks.map((check) => (
    +            <CheckResultCard key={check.id} check={check} />
    +          ))
    +        )}
    +      </div>
    +    </AppShell>
       );
     }
     
    diff --git a/web/src/pages/CommitTimeline.tsx b/web/src/pages/CommitTimeline.tsx
    index 07d85b7..1fcfb5b 100644
    --- a/web/src/pages/CommitTimeline.tsx
    +++ b/web/src/pages/CommitTimeline.tsx
    @@ -1,15 +1,21 @@
     import { useState, useEffect, useCallback } from 'react';
     import { useParams, useNavigate } from 'react-router-dom';
     import { useAuth } from '../context/AuthContext';
    +import { useToast } from '../context/ToastContext';
    +import { useWebSocket } from '../hooks/useWebSocket';
    +import AppShell from '../components/AppShell';
     import CommitRow from '../components/CommitRow';
     import type { AuditRun } from '../components/CommitRow';
     import HealthBadge from '../components/HealthBadge';
    +import EmptyState from '../components/EmptyState';
    +import { SkeletonRow } from '../components/Skeleton';
     
     const API_BASE = import.meta.env.VITE_API_URL ?? '';
     
     function CommitTimeline() {
       const { repoId } = useParams<{ repoId: string }>();
       const { token } = useAuth();
    +  const { addToast } = useToast();
       const navigate = useNavigate();
     
       const [audits, setAudits] = useState<AuditRun[]>([]);
    @@ -28,78 +34,107 @@ function CommitTimeline() {
             const data = await res.json();
             setAudits(data.items);
             setTotal(data.total);
    +      } else {
    +        addToast('Failed to load audits');
           }
         } catch {
    -      // network error
    +      addToast('Network error loading audits');
         } finally {
           setLoading(false);
         }
    -  }, [repoId, token, offset]);
    +  }, [repoId, token, offset, addToast]);
     
       useEffect(() => {
         fetchAudits();
       }, [fetchAudits]);
     
    +  // Real-time: refresh when audit for this repo completes
    +  useWebSocket(useCallback((data) => {
    +    if (data.type === 'audit_update') {
    +      const payload = data.payload as { repo_id?: string };
    +      if (payload.repo_id === repoId) {
    +        fetchAudits();
    +      }
    +    }
    +  }, [fetchAudits, repoId]));
    +
       const handleAuditClick = (audit: AuditRun) => {
         navigate(`/repos/${repoId}/audits/${audit.id}`);
       };
     
    -  return (
    -    <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    -      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
    -        <button
    -          onClick={() => navigate('/')}
    -          style={{
    -            background: 'transparent',
    -            color: '#94A3B8',
    -            border: '1px solid #334155',
    -            borderRadius: '6px',
    -            padding: '6px 12px',
    -            cursor: 'pointer',
    -            fontSize: '0.8rem',
    -          }}
    -        >
    -          Back
    -        </button>
    -        <HealthBadge score="pending" />
    -        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
    -        <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
    -      </div>
    +  // Compute health from loaded audits
    +  const computedHealth = (() => {
    +    const completed = audits.filter((a) => a.status === 'completed');
    +    if (completed.length === 0) return 'pending';
    +    const allPass = completed.every((a) => a.overall_result === 'PASS');
    +    const anyFail = completed.some((a) => a.overall_result === 'FAIL' || a.overall_result === 'ERROR');
    +    if (allPass) return 'green';
    +    if (anyFail) return 'red';
    +    return 'yellow';
    +  })();
     
    -      {loading ? (
    -        <p style={{ color: '#94A3B8' }}>Loading audits...</p>
    -      ) : audits.length === 0 ? (
    -        <div style={{ textAlign: 'center', padding: '64px 24px', color: '#94A3B8' }}>
    -          <p>No audit results yet. Push a commit to trigger the first audit.</p>
    +  return (
    +    <AppShell>
    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
    +          <button
    +            onClick={() => navigate('/')}
    +            style={{
    +              background: 'transparent',
    +              color: '#94A3B8',
    +              border: '1px solid #334155',
    +              borderRadius: '6px',
    +              padding: '6px 12px',
    +              cursor: 'pointer',
    +              fontSize: '0.8rem',
    +            }}
    +          >
    +            Back
    +          </button>
    +          <HealthBadge score={computedHealth} />
    +          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Commit Timeline</h2>
    +          <span style={{ color: '#94A3B8', fontSize: '0.8rem' }}>({total} audits)</span>
             </div>
    -      ) : (
    -        <>
    +
    +        {loading ? (
               <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    -            {audits.map((audit) => (
    -              <CommitRow key={audit.id} audit={audit} onClick={handleAuditClick} />
    -            ))}
    +            <SkeletonRow />
    +            <SkeletonRow />
    +            <SkeletonRow />
    +            <SkeletonRow />
    +            <SkeletonRow />
               </div>
    -          {total > offset + limit && (
    -            <button
    -              onClick={() => setOffset((o) => o + limit)}
    -              style={{
    -                display: 'block',
    -                margin: '16px auto',
    -                background: '#1E293B',
    -                color: '#94A3B8',
    -                border: '1px solid #334155',
    -                borderRadius: '6px',
    -                padding: '8px 24px',
    -                cursor: 'pointer',
    -                fontSize: '0.85rem',
    -              }}
    -            >
    -              Load More
    -            </button>
    -          )}
    -        </>
    -      )}
    -    </div>
    +        ) : audits.length === 0 ? (
    +          <EmptyState message="No audit results yet. Push a commit to trigger the first audit." />
    +        ) : (
    +          <>
    +            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    +              {audits.map((audit) => (
    +                <CommitRow key={audit.id} audit={audit} onClick={handleAuditClick} />
    +              ))}
    +            </div>
    +            {total > offset + limit && (
    +              <button
    +                onClick={() => setOffset((o) => o + limit)}
    +                style={{
    +                  display: 'block',
    +                  margin: '16px auto',
    +                  background: '#1E293B',
    +                  color: '#94A3B8',
    +                  border: '1px solid #334155',
    +                  borderRadius: '6px',
    +                  padding: '8px 24px',
    +                  cursor: 'pointer',
    +                  fontSize: '0.85rem',
    +                }}
    +              >
    +                Load More
    +              </button>
    +            )}
    +          </>
    +        )}
    +      </div>
    +    </AppShell>
       );
     }
     
    diff --git a/web/src/pages/Dashboard.tsx b/web/src/pages/Dashboard.tsx
    index c6ebb70..aa05884 100644
    --- a/web/src/pages/Dashboard.tsx
    +++ b/web/src/pages/Dashboard.tsx
    @@ -1,15 +1,21 @@
     import { useState, useEffect, useCallback } from 'react';
     import { useNavigate } from 'react-router-dom';
     import { useAuth } from '../context/AuthContext';
    +import { useToast } from '../context/ToastContext';
    +import { useWebSocket } from '../hooks/useWebSocket';
    +import AppShell from '../components/AppShell';
     import RepoCard from '../components/RepoCard';
     import type { Repo } from '../components/RepoCard';
     import RepoPickerModal from '../components/RepoPickerModal';
     import ConfirmDialog from '../components/ConfirmDialog';
    +import EmptyState from '../components/EmptyState';
    +import { SkeletonCard } from '../components/Skeleton';
     
     const API_BASE = import.meta.env.VITE_API_URL ?? '';
     
     function Dashboard() {
    -  const { user, token, logout } = useAuth();
    +  const { token } = useAuth();
    +  const { addToast } = useToast();
       const navigate = useNavigate();
       const [repos, setRepos] = useState<Repo[]>([]);
       const [loading, setLoading] = useState(true);
    @@ -24,29 +30,39 @@ function Dashboard() {
           if (res.ok) {
             const data = await res.json();
             setRepos(data.items);
    +      } else {
    +        addToast('Failed to load repos');
           }
         } catch {
    -      // network error -- keep existing
    +      addToast('Network error loading repos');
         } finally {
           setLoading(false);
         }
    -  }, [token]);
    +  }, [token, addToast]);
     
       useEffect(() => {
         fetchRepos();
       }, [fetchRepos]);
     
    +  // Real-time: refresh repos when an audit completes
    +  useWebSocket(useCallback((data) => {
    +    if (data.type === 'audit_update') {
    +      fetchRepos();
    +    }
    +  }, [fetchRepos]));
    +
       const handleDisconnect = async () => {
         if (!disconnectTarget) return;
         try {
    -      await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
    +      const res = await fetch(`${API_BASE}/repos/${disconnectTarget.id}/disconnect`, {
             method: 'DELETE',
             headers: { Authorization: `Bearer ${token}` },
           });
    +      if (!res.ok) addToast('Failed to disconnect repo');
           setDisconnectTarget(null);
           fetchRepos();
         } catch {
    -      // best effort
    +      addToast('Network error disconnecting repo');
           setDisconnectTarget(null);
         }
       };
    @@ -56,44 +72,8 @@ function Dashboard() {
       };
     
       return (
    -    <div style={{ background: '#0F172A', color: '#F8FAFC', minHeight: '100vh' }}>
    -      <header
    -        style={{
    -          display: 'flex',
    -          alignItems: 'center',
    -          justifyContent: 'space-between',
    -          padding: '16px 24px',
    -          borderBottom: '1px solid #1E293B',
    -        }}
    -      >
    -        <h1 style={{ fontSize: '1.25rem', fontWeight: 700 }}>ForgeGuard</h1>
    -        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
    -          {user?.avatar_url && (
    -            <img
    -              src={user.avatar_url}
    -              alt={user.github_login}
    -              style={{ width: 32, height: 32, borderRadius: '50%' }}
    -            />
    -          )}
    -          <span style={{ color: '#94A3B8' }}>{user?.github_login}</span>
    -          <button
    -            onClick={logout}
    -            style={{
    -              background: 'transparent',
    -              color: '#94A3B8',
    -              border: '1px solid #334155',
    -              borderRadius: '6px',
    -              padding: '6px 16px',
    -              cursor: 'pointer',
    -              fontSize: '0.875rem',
    -            }}
    -          >
    -            Logout
    -          </button>
    -        </div>
    -      </header>
    -
    -      <main style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
    +    <AppShell sidebarRepos={repos} onReposChange={fetchRepos}>
    +      <div style={{ padding: '24px', maxWidth: '960px', margin: '0 auto' }}>
             <div
               style={{
                 display: 'flex',
    @@ -120,17 +100,17 @@ function Dashboard() {
             </div>
     
             {loading ? (
    -          <p style={{ color: '#94A3B8' }}>Loading repos...</p>
    -        ) : repos.length === 0 ? (
    -          <div
    -            style={{
    -              textAlign: 'center',
    -              padding: '64px 24px',
    -              color: '#94A3B8',
    -            }}
    -          >
    -            <p>No repos connected yet. Click &quot;Connect a Repo&quot; to get started.</p>
    +          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
    +            <SkeletonCard />
    +            <SkeletonCard />
    +            <SkeletonCard />
               </div>
    +        ) : repos.length === 0 ? (
    +          <EmptyState
    +            message='No repos connected yet. Click "Connect a Repo" to get started.'
    +            actionLabel="Connect a Repo"
    +            onAction={() => setShowPicker(true)}
    +          />
             ) : (
               <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                 {repos.map((repo) => (
    @@ -143,7 +123,7 @@ function Dashboard() {
                 ))}
               </div>
             )}
    -      </main>
    +      </div>
     
           {showPicker && (
             <RepoPickerModal
    @@ -161,7 +141,7 @@ function Dashboard() {
               onCancel={() => setDisconnectTarget(null)}
             />
           )}
    -    </div>
    +    </AppShell>
       );
     }
     

## Verification
- Static analysis: compileall pass on all Python modules
- Runtime: all FastAPI endpoints verified via test client including WS
- Behavior: pytest 56 passed, vitest 15 passed, all assertions green
- Contract compliance: boundaries.json respected, no unauthorized imports, no ORM usage

## Notes (optional)
- No blockers. All Phase 4 features implemented and tested.

## Next Steps
- Phase 5 Ship Gate: USER_INSTRUCTIONS.md, boot.ps1, rate limiting, input validation, error handling audit, env variable validation

