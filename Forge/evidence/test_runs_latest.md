Status: PASS
Phase: 39 — Scout Metrics Engine & Grounded Quality Assessment
Branch: master
Backend: 849 passed, 1 pre-existing failure (test_cors_allows_valid_origin)
Frontend: 68 passed
Total: 917 tests
New tests: 45 backend (scout_metrics=35, dossier_prompt=10)
Files created:
  - app/services/scout_metrics.py (380 lines) — deterministic metrics engine + 11 smell detectors
  - db/migrations/019_scout_computed_score.sql — computed_score column
  - tests/test_scout_metrics.py (35 tests)
  - tests/test_scout_dossier_prompt.py (10 tests)
Files modified:
  - app/services/scout_service.py — rubric-based dossier prompt, metrics injection, score history
  - app/repos/scout_repo.py — computed_score param, get_score_history()
  - app/api/routers/scout.py — GET /{repo_id}/score-history endpoint
  - web/src/pages/Scout.tsx — metrics bars, smells list, sparkline chart, Measured/AI badges
  - Forge/Contracts/phases.md (Phase 39 status -> DONE)
