# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T04:17:23+00:00
- Branch: master
- HEAD: e40d2ed
- BASE_HEAD: ef1b753
- Diff basis: committed (Phase 11 complete)

## Cycle Status
- Status: COMPLETE

## Summary
- Added build_costs table (004_build_costs.sql) for per-phase token usage and cost tracking
- Implemented StreamUsage in agent_client.py to capture input/output tokens from SSE events
- Added GET /build/summary and GET /build/instructions endpoints with rate limiting (5/hr)
- Created BuildComplete.tsx frontend page with cost breakdown, status banner, deploy instructions
- Updated USER_INSTRUCTIONS.md with full build workflow docs and API reference

## Files Changed
- db/migrations/004_build_costs.sql
- Forge/Contracts/physics.yaml
- Forge/Contracts/schema.md
- Forge/evidence/audit_ledger.md
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md
- USER_INSTRUCTIONS.md
- app/api/rate_limit.py
- app/api/routers/builds.py
- app/clients/agent_client.py
- app/repos/build_repo.py
- app/services/build_service.py
- tests/test_build_repo.py
- tests/test_build_service.py
- tests/test_builds_router.py
- web/src/App.tsx
- web/src/__tests__/Build.test.tsx
- web/src/pages/BuildComplete.tsx
- web/src/pages/BuildProgress.tsx

## Verification
- Static: all files compile (compileall + tsc pass)
- Runtime: 208 pytest + 35 vitest = 243 tests pass
- Behavior: cost tracking, summary/instructions endpoints validated
- Contract: physics.yaml and schema.md updated with new paths and schemas

## Notes (optional)
- W1 secrets warning is expected (test fixtures contain token patterns)
- Removed from __future__ import annotations in agent_client.py to fix A9 dependency gate

## Next Steps
- Phase 11 complete — all phases delivered
- AUTO-AUTHORIZED at commit e40d2ed

