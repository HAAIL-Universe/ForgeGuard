# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T04:42:00+00:00
- Branch: master
- HEAD: pending
- BASE_HEAD: 63d92ee
- Diff basis: post-phase enhancement (commit backfill + create project)

## Cycle Status
- Status: COMPLETE

## Summary
- Added offline commit backfill: `list_commits()` GitHub client, `get_existing_commit_shas()` repo layer, `backfill_repo_commits()` service, `POST /repos/{id}/sync` endpoint
- Added "Create Project" button + modal on Dashboard with name/description form
- Added "Sync Commits" button on CommitTimeline page to trigger backfill
- 15 new tests: 5 audit service, 3 GitHub client, 3 repos router sync, 4 frontend CreateProjectModal

## Files Changed
- app/clients/github_client.py (added list_commits)
- app/repos/audit_repo.py (added get_existing_commit_shas)
- app/services/audit_service.py (added backfill_repo_commits)
- app/api/routers/repos.py (added POST sync endpoint)
- web/src/components/CreateProjectModal.tsx (new)
- web/src/pages/Dashboard.tsx (added Create Project button + modal)
- web/src/pages/CommitTimeline.tsx (added Sync Commits button)
- tests/test_audit_service.py (new, 5 tests)
- tests/test_github_client.py (new, 3 tests)
- tests/test_repos_router.py (added 3 sync tests)
- web/src/__tests__/App.test.tsx (added 4 CreateProjectModal tests)
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md

## Verification
- Static: all files compile (compileall + tsc pass)
- Runtime: 219 pytest + 39 vitest = 258 tests pass (was 243)
- Behavior: backfill syncs missing commits, Create Project modal submits to backend
- Contract: no phase contract changes needed (post-phase enhancement)

## Notes (optional)
- Backfill fetches up to 300 recent commits (3 pages × 100) per sync
- Backfill continues processing if individual commits error (graceful degradation)

## Next Steps
- Features ready for manual QA with live GitHub repos

