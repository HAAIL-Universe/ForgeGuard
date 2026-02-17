# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-17T19:00:00+00:00
- Branch: master
- HEAD: 294992c (pre-commit — Phase 35 changes staged)
- BASE_HEAD: 294992c
- Diff basis: working tree vs HEAD

## Cycle Status
- Status: COMPLETE

## Summary
- Phase 35: Build Spend Cap / Circuit Breaker / Cost Gate.
  Adds user-configurable per-build spend cap with automatic cost enforcement,
  live cost ticker via WebSocket, circuit breaker (one-click API kill switch),
  and cost warning/exceeded banners to prevent runaway API costs.
- DB: migration 017 adds `build_spend_cap NUMERIC(10,2)` column to users table.
- Backend: In-memory cost accumulation across 5 LLM cost recording sites.
  `_check_cost_gate()` enforces warning at 80% and hard stop at 100% of cap.
  `CostCapExceeded` exception triggers `_fail_build()` + cancel flag.
  `cost_ticker` WS event broadcasts every 5s with live cost/tokens/api_calls.
  `cost_warning` and `cost_exceeded` WS events for frontend banners.
  Server-level default: $50 (BUILD_MAX_COST_USD env var).
- API: PUT/DELETE `/auth/spend-cap` endpoints. `/auth/me` includes `build_spend_cap`.
  GET `/projects/{id}/build/live-cost` for REST cost polling.
  POST `/projects/{id}/build/circuit-break` for immediate hard stop.
- Frontend BuildProgress.tsx: spend cap progress bar, live API call counter,
  cost ticker display, cost warning/exceeded banners, circuit breaker button (red).
- Frontend Settings.tsx: Spend cap input field with save/clear.
- AuthContext: `build_spend_cap` added to User interface.
- 19 new tests in test_cost_gate.py. 624 backend + 61 frontend = 685 total tests passing.

## Files Changed
- db/migrations/017_user_spend_cap.sql (new migration)
- app/repos/user_repo.py (build_spend_cap in SELECT + set_build_spend_cap)
- app/config.py (BUILD_MAX_COST_USD, BUILD_COST_WARN_PCT, BUILD_COST_TICKER_INTERVAL)
- app/services/build_service.py (cost gate, accumulator, ticker, init/cleanup)
- app/api/routers/auth.py (spend-cap PUT/DELETE, /me includes build_spend_cap)
- app/api/routers/builds.py (circuit-break + live-cost endpoints)
- web/src/pages/BuildProgress.tsx (cost ticker UI, circuit breaker, banners)
- web/src/pages/Settings.tsx (spend cap input section)
- web/src/context/AuthContext.tsx (build_spend_cap in User interface)
- tests/test_cost_gate.py (19 new tests — NEW FILE)
- tests/test_plan_execute.py (mock fix for get_user_by_id in _run_build)
- Forge/evidence/updatedifflog.md (this file)

## Verification
- Static: all modules import cleanly, no syntax errors. TypeScript clean (tsc --noEmit).
- Runtime: FastAPI app boots without error.
- Behavior: 624 backend tests pass (pytest), 61 frontend tests pass (vitest). 685 total.
- Contract: boundary compliance verified via test suite. No forbidden patterns.

## Next Steps
- Run watcher audit to validate this diff log.
- Commit and push Phase 35.
