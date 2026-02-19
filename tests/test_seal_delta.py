"""Phase 58.3 tests — Seal delta computation when dossier baseline is present."""

import pytest
from app.services.certificate_scorer import compute_certificate_scores


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_data(**overrides):
    """Minimal data dict for ``compute_certificate_scores``."""
    data = {
        "project": {"id": "p1", "name": "Test", "description": None},
        "builds_total": 1,
        "contracts": {"count": 3},
        "build": {
            "id": "b1",
            "phase": "1",
            "status": "completed",
            "loop_count": 1,
            "stats": {"files_written_count": 10, "git_commits_made": 3},
            "cost": {"total_cost_usd": 0.5, "total_input_tokens": 1000, "total_output_tokens": 500},
        },
        "audits": [],
        "scout": None,
        "repo_id": None,
        "consistency_data": None,
    }
    data.update(overrides)
    return data


def _baseline_dimensions():
    """Sample baseline dimensions from a dossier."""
    return {
        "build_integrity": {"score": 50},
        "test_coverage": {"score": 40},
        "audit_compliance": {"score": 60},
        "governance": {"score": 55},
        "security": {"score": 90},
        "cost_efficiency": {"score": 50},
        "reliability": {"score": 50},
        "consistency": {"score": 45},
        "architecture": {"score": 70},
    }


# ---------------------------------------------------------------------------
# Delta computation tests
# ---------------------------------------------------------------------------


class TestSealDelta:
    """Test that compute_certificate_scores produces baseline delta."""

    def test_no_baseline_returns_none_delta(self):
        """Without dossier_baseline, delta should be None."""
        data = _base_data()
        result = compute_certificate_scores(data)
        assert result["baseline_score"] is None
        assert result["delta"] is None

    def test_with_baseline_computes_delta(self):
        """With dossier_baseline, delta should have per-dimension entries."""
        data = _base_data(
            dossier_baseline={
                "computed_score": 55,
                "dimensions": _baseline_dimensions(),
            }
        )
        result = compute_certificate_scores(data)
        assert result["baseline_score"] == 55
        assert result["delta"] is not None
        assert isinstance(result["delta"], dict)
        # Should have entries for all dimensions in the certificate
        assert len(result["delta"]) > 0

    def test_delta_has_correct_structure(self):
        """Each delta entry should have baseline, final, delta keys."""
        data = _base_data(
            dossier_baseline={
                "computed_score": 55,
                "dimensions": _baseline_dimensions(),
            }
        )
        result = compute_certificate_scores(data)
        for dim_key, entry in result["delta"].items():
            assert "baseline" in entry, f"{dim_key} missing baseline"
            assert "final" in entry, f"{dim_key} missing final"
            assert "delta" in entry, f"{dim_key} missing delta"

    def test_delta_math_is_correct(self):
        """delta = final - baseline for each dimension."""
        data = _base_data(
            dossier_baseline={
                "computed_score": 55,
                "dimensions": _baseline_dimensions(),
            }
        )
        result = compute_certificate_scores(data)
        for dim_key, entry in result["delta"].items():
            if entry["baseline"] is not None and entry["delta"] is not None:
                expected = round(entry["final"] - entry["baseline"], 1)
                assert entry["delta"] == expected, (
                    f"{dim_key}: expected delta {expected}, got {entry['delta']}"
                )

    def test_missing_baseline_dim_gets_null(self):
        """If a dimension has no baseline, it should get null values."""
        sparse_baseline = {
            "test_coverage": {"score": 40},
            # Only one baseline dimension provided
        }
        data = _base_data(
            dossier_baseline={
                "computed_score": 40,
                "dimensions": sparse_baseline,
            }
        )
        result = compute_certificate_scores(data)
        # test_coverage should have a real delta
        if "test_coverage" in result["delta"]:
            assert result["delta"]["test_coverage"]["baseline"] == 40
        # Dimensions without baseline should have null baseline
        for dim_key, entry in result["delta"].items():
            if dim_key != "test_coverage":
                assert entry["baseline"] is None
                assert entry["delta"] is None


class TestSealVerdictUnchanged:
    """Ensure the verdict logic wasn't broken by Phase 58 changes."""

    def test_verdict_exists(self):
        data = _base_data()
        result = compute_certificate_scores(data)
        assert result["verdict"] in ("CERTIFIED", "CONDITIONAL", "FLAGGED")

    def test_overall_score_present(self):
        data = _base_data()
        result = compute_certificate_scores(data)
        assert "overall_score" in result
        assert 0 <= result["overall_score"] <= 100

    def test_dimensions_present(self):
        data = _base_data()
        result = compute_certificate_scores(data)
        assert "dimensions" in result
        assert len(result["dimensions"]) > 0
