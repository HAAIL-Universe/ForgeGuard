"""Tests for app.services.build.plan_artifacts — phase plan artifact storage."""

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4

import pytest

from app.services.build.plan_artifacts import (
    store_phase_plan,
    store_phase_outcome,
    get_prior_phase_context,
    get_current_phase_plan_context,
    clear_build_artifacts,
)
from forge_ide.mcp import artifact_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_artifact_store():
    """Clear the in-memory artifact store before and after each test."""
    artifact_store._store.clear()
    yield
    artifact_store._store.clear()


def _make_phase(num: int = 0, name: str = "Genesis", objective: str = "Bootstrap", deliverables: list | None = None):
    return {
        "number": num,
        "name": name,
        "objective": objective,
        "deliverables": deliverables or ["Create skeleton", "Add health check"],
    }


def _make_manifest(n: int = 3):
    return [
        {
            "path": f"app/file_{i}.py",
            "action": "create",
            "purpose": f"File {i} purpose",
            "depends_on": [f"app/file_{i - 1}.py"] if i > 0 else [],
            "context_files": [],
            "language": "python",
            "estimated_lines": 50 + i * 10,
            "status": "pending",
        }
        for i in range(n)
    ]


# ===================================================================
# store_phase_plan
# ===================================================================

class TestStorePhasePlan:
    """Tests for store_phase_plan."""

    def test_stores_plan_and_returns_metadata(self):
        bid = uuid4()
        phase = _make_phase(0)
        manifest = _make_manifest(3)

        result = store_phase_plan(bid, phase, manifest)

        assert result["stored"] is True
        assert "plan_phase_0" in result["store_key"]
        assert result["artifact_type"] == "phase"
        assert result["key"] == "plan_phase_0"
        assert result["size_chars"] > 0

    def test_plan_is_retrievable(self):
        bid = uuid4()
        phase = _make_phase(1, "Auth", "Implement auth")
        manifest = _make_manifest(2)

        store_phase_plan(bid, phase, manifest)

        retrieved = artifact_store.get_artifact(str(bid), "phase", "plan_phase_1")
        assert "error" not in retrieved
        content = retrieved["content"]
        assert content["phase_number"] == 1
        assert content["phase_name"] == "Auth"
        assert content["objective"] == "Implement auth"
        assert content["file_count"] == 2
        assert len(content["manifest"]) == 2

    def test_plan_manifest_preserves_paths_and_purposes(self):
        bid = uuid4()
        phase = _make_phase()
        manifest = _make_manifest(3)

        store_phase_plan(bid, phase, manifest)
        content = artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")["content"]

        paths = [f["path"] for f in content["manifest"]]
        assert paths == ["app/file_0.py", "app/file_1.py", "app/file_2.py"]
        assert content["manifest"][0]["purpose"] == "File 0 purpose"
        assert content["manifest"][1]["depends_on"] == ["app/file_0.py"]

    def test_plan_has_timestamp(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(), _make_manifest(1))
        content = artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")["content"]
        # Should be a valid ISO timestamp
        dt = datetime.fromisoformat(content["generated_at"])
        assert dt.year >= 2024

    def test_plan_preserves_deliverables(self):
        bid = uuid4()
        phase = _make_phase(0, deliverables=["D1", "D2", "D3"])
        store_phase_plan(bid, phase, _make_manifest(1))
        content = artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")["content"]
        assert content["deliverables"] == ["D1", "D2", "D3"]

    @patch.object(artifact_store, "_persist_to_disk")
    def test_plan_persists_to_disk(self, mock_persist):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(), _make_manifest(1))
        mock_persist.assert_called_once()

    def test_later_phase_plan_does_not_overwrite_earlier(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(2))
        store_phase_plan(bid, _make_phase(1, "Auth"), _make_manifest(3))

        p0 = artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")["content"]
        p1 = artifact_store.get_artifact(str(bid), "phase", "plan_phase_1")["content"]
        assert p0["file_count"] == 2
        assert p1["file_count"] == 3

    def test_empty_manifest_stores_zero_files(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(), [])
        content = artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")["content"]
        assert content["file_count"] == 0
        assert content["manifest"] == []


# ===================================================================
# store_phase_outcome
# ===================================================================

class TestStorePhaseOutcome:
    """Tests for store_phase_outcome."""

    def test_stores_pass_outcome(self):
        bid = uuid4()
        phase = _make_phase(0)
        files = {"app/main.py": "print('hi')", "app/health.py": "ok"}

        result = store_phase_outcome(
            bid, phase,
            status="pass",
            files_written=files,
        )
        assert result["stored"] is True
        assert "outcome_phase_0" in result["store_key"]

    def test_outcome_is_retrievable(self):
        bid = uuid4()
        phase = _make_phase(0)
        files = {"app/main.py": "print('hi')"}
        store_phase_outcome(bid, phase, status="pass", files_written=files)

        retrieved = artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")
        content = retrieved["content"]
        assert content["phase_number"] == 0
        assert content["status"] == "pass"
        assert content["file_count"] == 1
        assert content["audit_verdict"] == "PASS"

    def test_outcome_records_audit_stats(self):
        bid = uuid4()
        phase = _make_phase(0)
        store_phase_outcome(
            bid, phase,
            status="partial",
            files_written={"a.py": "x"},
            audit_verdict="PASS",
            audit_attempts=3,
            fixes_applied=2,
            verification={"syntax_errors": 1, "tests_failed": 0},
            governance={"passed": False},
        )
        content = artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")["content"]
        assert content["audit_attempts"] == 3
        assert content["fixes_applied"] == 2
        assert content["verification"]["syntax_errors"] == 1
        assert content["governance"]["passed"] is False

    def test_outcome_file_sizes_computed(self):
        bid = uuid4()
        store_phase_outcome(
            bid, _make_phase(),
            status="pass",
            files_written={"a.py": "abc", "b.py": "z" * 100},
        )
        content = artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")["content"]
        files = {f["path"]: f["size_bytes"] for f in content["files_written"]}
        assert files["a.py"] == 3
        assert files["b.py"] == 100

    def test_outcome_handles_none_content(self):
        bid = uuid4()
        store_phase_outcome(
            bid, _make_phase(),
            status="pass",
            files_written={"a.py": None},
        )
        content = artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")["content"]
        assert content["files_written"][0]["size_bytes"] == 0

    def test_outcome_defaults(self):
        """Default values when optional params are omitted."""
        bid = uuid4()
        store_phase_outcome(
            bid, _make_phase(),
            status="pass",
            files_written={},
        )
        content = artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")["content"]
        assert content["audit_verdict"] == "PASS"
        assert content["audit_attempts"] == 1
        assert content["fixes_applied"] == 0
        assert content["verification"] == {"syntax_errors": 0, "tests_failed": 0}
        assert content["governance"] == {"passed": True}


# ===================================================================
# get_prior_phase_context
# ===================================================================

class TestGetPriorPhaseContext:
    """Tests for get_prior_phase_context."""

    def test_returns_empty_when_no_prior_phases(self):
        bid = uuid4()
        result = get_prior_phase_context(bid, 0)
        assert result == ""

    def test_returns_empty_when_phase_1_with_no_stored_phase_0(self):
        bid = uuid4()
        result = get_prior_phase_context(bid, 1)
        assert result == ""

    def test_returns_plan_summary_for_prior_phase(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0, "Genesis", "Bootstrap"), _make_manifest(2))
        store_phase_outcome(
            bid, _make_phase(0),
            status="pass",
            files_written={"a.py": "x", "b.py": "y"},
        )

        result = get_prior_phase_context(bid, 1)
        assert "## Prior Phase Summary" in result
        assert "Phase 0" in result
        assert "Genesis" in result
        assert "Bootstrap" in result
        assert "app/file_0.py" in result
        assert "Outcome" in result
        assert "pass" in result

    def test_includes_audit_stats_when_nontrivial(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(
            bid, _make_phase(0),
            status="partial",
            files_written={"a.py": "x"},
            audit_attempts=3,
            fixes_applied=5,
        )

        result = get_prior_phase_context(bid, 1)
        assert "Audit attempts" in result
        assert "3" in result
        assert "Auto-fixes" in result
        assert "5" in result

    def test_includes_multiple_prior_phases(self):
        bid = uuid4()
        for i in range(3):
            store_phase_plan(bid, _make_phase(i, f"Phase{i}"), _make_manifest(1))
            store_phase_outcome(
                bid, _make_phase(i),
                status="pass",
                files_written={f"f{i}.py": "x"},
            )

        result = get_prior_phase_context(bid, 3)
        assert "Phase 0" in result
        assert "Phase 1" in result
        assert "Phase 2" in result

    def test_handles_plan_only_no_outcome(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0, "Genesis"), _make_manifest(2))

        result = get_prior_phase_context(bid, 1)
        assert "Phase 0" in result
        assert "Genesis" in result
        # No outcome section
        assert "Outcome" not in result

    def test_handles_outcome_only_no_plan(self):
        bid = uuid4()
        store_phase_outcome(
            bid, _make_phase(0),
            status="pass",
            files_written={"a.py": "x"},
        )

        result = get_prior_phase_context(bid, 1)
        assert "Phase 0" in result
        assert "pass" in result

    def test_skips_phases_with_no_data(self):
        bid = uuid4()
        # Phase 0 has data, phase 1 does not, phase 2 has data
        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(bid, _make_phase(0), status="pass", files_written={"a.py": "x"})
        store_phase_plan(bid, _make_phase(2, "Dashboard"), _make_manifest(1))
        store_phase_outcome(bid, _make_phase(2), status="pass", files_written={"b.py": "y"})

        result = get_prior_phase_context(bid, 3)
        assert "Phase 0" in result
        assert "Phase 1" not in result
        assert "Phase 2" in result

    def test_includes_remaining_errors(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(
            bid, _make_phase(0),
            status="partial",
            files_written={"a.py": "x"},
            verification={"syntax_errors": 2, "tests_failed": 3},
        )
        result = get_prior_phase_context(bid, 1)
        assert "syntax errors" in result
        assert "test failures" in result


# ===================================================================
# get_current_phase_plan_context
# ===================================================================

class TestGetCurrentPhasePlanContext:
    """Tests for get_current_phase_plan_context."""

    def test_returns_empty_when_no_plan_stored(self):
        bid = uuid4()
        assert get_current_phase_plan_context(bid, 0) == ""

    def test_returns_phase_plan_summary(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0, "Genesis", "Bootstrap"), _make_manifest(3))

        result = get_current_phase_plan_context(bid, 0)
        assert "Current Phase Plan" in result
        assert "Phase 0" in result
        assert "Bootstrap" in result
        assert "app/file_0.py" in result
        assert "app/file_1.py" in result
        assert "app/file_2.py" in result

    def test_includes_dependency_info(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(3))

        result = get_current_phase_plan_context(bid, 0)
        assert "depends: none" in result  # file_0 has no deps
        assert "depends: app/file_0.py" in result  # file_1 depends on file_0

    def test_different_phases_independent(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(2))
        store_phase_plan(bid, _make_phase(1, "Auth"), _make_manifest(4))

        ctx0 = get_current_phase_plan_context(bid, 0)
        ctx1 = get_current_phase_plan_context(bid, 1)
        assert "2" not in ctx0 or "file_2" not in ctx0  # Only 2 files in phase 0
        assert "file_3" in ctx1  # 4 files in phase 1


# ===================================================================
# clear_build_artifacts
# ===================================================================

class TestClearBuildArtifacts:
    """Tests for clear_build_artifacts."""

    def test_clears_all_phase_artifacts(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(bid, _make_phase(0), status="pass", files_written={"a.py": "x"})
        store_phase_plan(bid, _make_phase(1), _make_manifest(2))

        result = clear_build_artifacts(bid)
        assert result["cleared_memory"] == 3  # 2 plans + 1 outcome

        # Verify they're gone
        assert "error" in artifact_store.get_artifact(str(bid), "phase", "plan_phase_0")
        assert "error" in artifact_store.get_artifact(str(bid), "phase", "outcome_phase_0")
        assert "error" in artifact_store.get_artifact(str(bid), "phase", "plan_phase_1")

    def test_does_not_affect_other_builds(self):
        bid1 = uuid4()
        bid2 = uuid4()
        store_phase_plan(bid1, _make_phase(0), _make_manifest(1))
        store_phase_plan(bid2, _make_phase(0), _make_manifest(2))

        clear_build_artifacts(bid1)

        assert "error" in artifact_store.get_artifact(str(bid1), "phase", "plan_phase_0")
        assert "error" not in artifact_store.get_artifact(str(bid2), "phase", "plan_phase_0")

    def test_idempotent_on_empty(self):
        bid = uuid4()
        result = clear_build_artifacts(bid)
        assert result["cleared_memory"] == 0


# ===================================================================
# Integration: store → retrieve round-trip
# ===================================================================

class TestRoundTrip:
    """End-to-end store-then-retrieve tests."""

    def test_full_two_phase_pipeline(self):
        """Simulate a 2-phase mini build: store plans + outcomes, then
        retrieve prior context for phase 2."""
        bid = uuid4()

        # Phase 0
        phase0 = _make_phase(0, "Genesis", "Project skeleton")
        manifest0 = _make_manifest(2)
        store_phase_plan(bid, phase0, manifest0)

        store_phase_outcome(
            bid, phase0,
            status="pass",
            files_written={"app/file_0.py": "content0", "app/file_1.py": "content1"},
            audit_attempts=1,
        )

        # Phase 1 planner gets prior context
        prior = get_prior_phase_context(bid, 1)
        assert "Genesis" in prior
        assert "Project skeleton" in prior
        assert "file_0.py" in prior
        assert "pass" in prior

        # Phase 1
        phase1 = _make_phase(1, "Auth", "Add authentication")
        manifest1 = _make_manifest(3)
        store_phase_plan(bid, phase1, manifest1)

        # Per-file generator gets current plan context
        plan_ctx = get_current_phase_plan_context(bid, 1)
        assert "Auth" in plan_ctx
        assert "file_0.py" in plan_ctx
        assert "file_2.py" in plan_ctx

        store_phase_outcome(
            bid, phase1,
            status="pass",
            files_written={"app/file_0.py": "x", "app/file_1.py": "y", "app/file_2.py": "z"},
        )

        # After both phases, phase 2 would see both
        prior2 = get_prior_phase_context(bid, 2)
        assert "Phase 0" in prior2
        assert "Phase 1" in prior2

    def test_clear_then_new_build(self):
        """Simulate a fresh build clearing stale artifacts."""
        bid = uuid4()

        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(bid, _make_phase(0), status="pass", files_written={"a.py": "x"})

        # New build starts — clear
        clear_build_artifacts(bid)

        # No prior context
        assert get_prior_phase_context(bid, 1) == ""
        assert get_current_phase_plan_context(bid, 0) == ""

    def test_resume_preserves_artifacts(self):
        """Simulate resume: artifacts from prior phases survive."""
        bid = uuid4()

        store_phase_plan(bid, _make_phase(0), _make_manifest(2))
        store_phase_outcome(bid, _make_phase(0), status="pass", files_written={"a.py": "x"})

        # "Resume" — don't clear, just retrieve
        prior = get_prior_phase_context(bid, 1)
        assert "Phase 0" in prior

    def test_partial_outcome_reported(self):
        bid = uuid4()
        store_phase_plan(bid, _make_phase(0), _make_manifest(1))
        store_phase_outcome(
            bid, _make_phase(0),
            status="partial",
            files_written={"a.py": "x"},
            verification={"syntax_errors": 1, "tests_failed": 2},
        )

        prior = get_prior_phase_context(bid, 1)
        assert "partial" in prior
        assert "syntax errors" in prior.lower()
        assert "test failures" in prior.lower()
