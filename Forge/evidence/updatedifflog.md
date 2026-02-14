# Phase 2 -- Repo Management: Diff Log

## Files Changed (8 new, 6 modified)

### New Files
- app/repos/repo_repo.py - Repo DB CRUD (create, get, delete, update webhook)
- app/services/repo_service.py - Repo service (connect, disconnect, list, available)
- app/api/routers/repos.py - REST endpoints (GET /repos, GET /repos/available, POST /repos/connect, DELETE /repos/{id}/disconnect)
- web/src/components/HealthBadge.tsx - Colored health indicator circle
- web/src/components/RepoCard.tsx - Repo list card with health badge and disconnect
- web/src/components/RepoPickerModal.tsx - Searchable modal for connecting GitHub repos
- web/src/components/ConfirmDialog.tsx - Generic confirmation dialog for destructive actions
- tests/test_repos_router.py - 10 endpoint tests for repos router

### Modified Files
- app/clients/github_client.py - Added list_user_repos, create_webhook, delete_webhook
- app/config.py - Added APP_URL setting
- app/repos/user_repo.py - Added access_token to get_user_by_id SELECT
- app/main.py - Wired repos_router
- web/src/pages/Dashboard.tsx - Full repo list with connect/disconnect flow
- web/src/__tests__/App.test.tsx - Added HealthBadge + ConfirmDialog tests

## Verification Evidence
- Static: PASS (compileall clean, import app.main OK)
- Runtime: PASS (uvicorn boots, /health 200, /repos 401, /repos/available 401)
- Behavior: PASS (pytest 22/22, vitest 5/5)
- Contract: PASS (physics endpoints matched, boundaries clean, schema repos table matches migration)
