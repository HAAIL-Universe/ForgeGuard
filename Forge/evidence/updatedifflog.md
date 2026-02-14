# Phase 3 -- Audit Engine: Diff Log

## Verification Evidence
- Static: PASS (compileall clean, import app.main OK)
- Runtime: PASS (uvicorn boots on port 8004, /health 200, /webhooks/github 401 on bad signature)
- Behavior: PASS (pytest 42/42, vitest 9/9)
- Contract: PASS (physics webhook + audit endpoints matched, boundaries respected, schema audit tables match migration)

## Files Changed (13 new, 6 modified)

### New Files
- app/webhooks.py
- app/audit/engine.py
- app/repos/audit_repo.py
- app/services/audit_service.py
- app/api/routers/webhooks.py
- web/src/components/ResultBadge.tsx
- web/src/components/CommitRow.tsx
- web/src/components/CheckResultCard.tsx
- web/src/pages/CommitTimeline.tsx
- web/src/pages/AuditDetail.tsx
- tests/test_audit_engine.py
- tests/test_webhooks.py
- tests/test_webhook_router.py

### Modified Files
- app/api/routers/repos.py
- app/clients/github_client.py
- app/main.py
- web/src/App.tsx
- web/src/pages/Dashboard.tsx
- web/src/__tests__/App.test.tsx
- app/audit/__init__.py
- Forge/Contracts/builder_contract.md
- Forge/evidence/audit_ledger.md
- Forge/evidence/test_runs.md
- Forge/evidence/test_runs_latest.md
- Forge/evidence/updatedifflog.md
- Forge/scripts/watch_audit.ps1

## Summary
- Webhook receiver with hashlib-only HMAC-SHA256 signature verification (RFC 2104)
- Pure audit engine with A4 boundary compliance, A9 dependency gate, W1 secrets scan
- Audit DB layer for audit_runs and audit_checks tables
- Audit service orchestrating webhook push events through to stored results
- Frontend commit timeline (paginated) and audit detail views with result badges
- Added audit listing and detail endpoints to repos router

