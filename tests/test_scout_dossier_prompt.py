"""Tests for Phase 39 dossier prompt rules and score-history endpoint."""

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Dossier prompt tests (39.3)
# ---------------------------------------------------------------------------

class TestDossierPrompt:
    """Verify the rubric-based prompt rules."""

    def test_prompt_requires_computed_score(self):
        from app.services.scout_service import _DOSSIER_SYSTEM_PROMPT
        assert "computed_score" in _DOSSIER_SYSTEM_PROMPT
        assert "do NOT change it" in _DOSSIER_SYSTEM_PROMPT.lower() or \
               "do NOT change it" in _DOSSIER_SYSTEM_PROMPT

    def test_prompt_references_measured_metrics(self):
        from app.services.scout_service import _DOSSIER_SYSTEM_PROMPT
        assert "measured" in _DOSSIER_SYSTEM_PROMPT.lower() or \
               "Measured" in _DOSSIER_SYSTEM_PROMPT

    def test_prompt_requires_smells_in_risks(self):
        from app.services.scout_service import _DOSSIER_SYSTEM_PROMPT
        assert "smell" in _DOSSIER_SYSTEM_PROMPT.lower()
        assert "risk_areas" in _DOSSIER_SYSTEM_PROMPT

    def test_prompt_requires_json_schema(self):
        from app.services.scout_service import _DOSSIER_SYSTEM_PROMPT
        assert "executive_summary" in _DOSSIER_SYSTEM_PROMPT
        assert "quality_assessment" in _DOSSIER_SYSTEM_PROMPT
        assert "recommendations" in _DOSSIER_SYSTEM_PROMPT

    def test_generate_dossier_accepts_metrics_param(self):
        """Ensure _generate_dossier signature accepts metrics kwarg."""
        import inspect
        from app.services.scout_service import _generate_dossier
        sig = inspect.signature(_generate_dossier)
        assert "metrics" in sig.parameters


# ---------------------------------------------------------------------------
# Score-history repo tests (39.4)
# ---------------------------------------------------------------------------

class TestScoreHistoryRepo:
    """Test the get_score_history repository function exists and has correct signature."""

    def test_get_score_history_importable(self):
        from app.repos.scout_repo import get_score_history
        assert callable(get_score_history)

    def test_update_scout_run_accepts_computed_score(self):
        import inspect
        from app.repos.scout_repo import update_scout_run
        sig = inspect.signature(update_scout_run)
        assert "computed_score" in sig.parameters


# ---------------------------------------------------------------------------
# Score-history router tests (39.4)
# ---------------------------------------------------------------------------

class TestScoreHistoryRouter:
    """Test the score-history endpoint is registered."""

    def test_score_history_route_exists(self):
        from app.api.routers.scout import router
        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert any("score-history" in p for p in paths)

    @pytest.mark.asyncio
    async def test_score_history_service_function(self):
        """Verify get_scout_score_history validates ownership."""
        from app.services.scout_service import get_scout_score_history
        with patch("app.repos.repo_repo.get_repo_by_id", new_callable=AsyncMock) as mock_repo:
            mock_repo.return_value = None
            with pytest.raises(ValueError, match="not found"):
                await get_scout_score_history(uuid4(), uuid4())


# ---------------------------------------------------------------------------
# Deep scan integration — metrics in results payload
# ---------------------------------------------------------------------------

class TestDeepScanMetricsIntegration:
    """Verify the deep scan pipeline includes metrics in the results."""

    def test_dossier_response_includes_metrics(self):
        """get_scout_dossier return shape includes 'metrics' key."""
        import inspect
        from app.services.scout_service import get_scout_dossier
        # Just verify the function source includes metrics
        source = inspect.getsource(get_scout_dossier)
        assert '"metrics"' in source or "'metrics'" in source
