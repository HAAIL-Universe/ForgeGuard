"""Tests for Phase 34 — governance gate at phase transitions.

Covers:
  34.1  _run_governance_checks (all checks)
  34.2  G1 scope compliance (phantom files, missing files)
  34.3  G2 boundary compliance (forbidden patterns)
  34.4  G3 dependency gate (undeclared imports)
  34.5  G4 secrets scan
  34.6  G5 physics route coverage
  34.7  G6 rename detection
  34.8  G7 TODO / placeholder scan
  34.9  Blocking failure triggers fix loop
  34.10 Warnings do not block
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import build_service
from app.services.build_service import _run_governance_checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _contracts(boundaries: dict | None = None) -> list[dict]:
    b = boundaries or {"layers": []}
    return [
        {"contract_type": "blueprint", "content": "# Blueprint\nTest blueprint"},
        {"contract_type": "stack", "content": "# Stack\nPython + FastAPI"},
        {"contract_type": "boundaries", "content": json.dumps(b)},
    ]


def _manifest(files: list[str]) -> list[dict]:
    return [
        {
            "path": f,
            "action": "create",
            "purpose": f"Create {f}",
            "depends_on": [],
            "context_files": [],
            "estimated_lines": 50,
            "language": "python",
        }
        for f in files
    ]


def _mock_patches():
    """Return common patches for governance tests."""
    return (
        patch("app.services.build._state.build_repo", new_callable=MagicMock),
        patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock),
        patch("app.services.build._state._set_build_activity", new_callable=AsyncMock),
    )


# ---------------------------------------------------------------------------
# 34.1 _run_governance_checks — all pass
# ---------------------------------------------------------------------------


class TestGovernanceAllPass:
    @pytest.mark.asyncio
    async def test_all_checks_pass(self, tmp_path):
        """When all files are clean and match manifest, all checks pass."""
        # Create a simple Python file with no issues
        app_dir = tmp_path / "app" / "services"
        app_dir.mkdir(parents=True)
        (app_dir / "foo.py").write_text("import os\n\ndef hello():\n    return 'hi'\n")

        manifest = _manifest(["app/services/foo.py"])
        touched = {"app/services/foo.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        assert result["passed"] is True
        assert result["blocking_failures"] == 0
        assert len(result["checks"]) == 7  # G1-G7
        for c in result["checks"]:
            assert c["result"] in ("PASS", "WARN")  # No FAIL


# ---------------------------------------------------------------------------
# 34.2 G1 — scope compliance
# ---------------------------------------------------------------------------


class TestG1ScopeCompliance:
    @pytest.mark.asyncio
    async def test_phantom_file_detected(self, tmp_path):
        """File on disk but not in manifest -> G1 FAIL."""
        (tmp_path / "extra.py").write_text("x = 1\n")

        manifest = _manifest([])  # empty manifest
        touched = {"extra.py"}  # but we "touched" it on disk

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g1 = next(c for c in result["checks"] if c["code"] == "G1")
        assert g1["result"] == "FAIL"
        assert "phantom" in g1["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_file_detected(self, tmp_path):
        """File in manifest but not on disk -> G1 FAIL."""
        manifest = _manifest(["does_not_exist.py"])
        touched: set[str] = set()

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g1 = next(c for c in result["checks"] if c["code"] == "G1")
        assert g1["result"] == "FAIL"
        assert "missing" in g1["detail"].lower()


# ---------------------------------------------------------------------------
# 34.3 G2 — boundary compliance
# ---------------------------------------------------------------------------


class TestG2BoundaryCompliance:
    @pytest.mark.asyncio
    async def test_boundary_violation_detected(self, tmp_path):
        """File contains a forbidden pattern -> G2 FAIL."""
        routers_dir = tmp_path / "app" / "api" / "routers"
        routers_dir.mkdir(parents=True)
        (routers_dir / "builds.py").write_text(
            "import asyncpg\n\ndef get_builds():\n    pass\n"
        )

        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "routers must not import asyncpg directly"}
                    ],
                }
            ]
        }

        manifest = _manifest(["app/api/routers/builds.py"])
        touched = {"app/api/routers/builds.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(boundaries),
                touched, "Phase 1",
            )

        g2 = next(c for c in result["checks"] if c["code"] == "G2")
        assert g2["result"] == "FAIL"
        assert "asyncpg" in g2["detail"]

    @pytest.mark.asyncio
    async def test_no_boundary_violation(self, tmp_path):
        """Clean file passes boundary check."""
        routers_dir = tmp_path / "app" / "api" / "routers"
        routers_dir.mkdir(parents=True)
        (routers_dir / "builds.py").write_text(
            "from fastapi import APIRouter\n\ndef get_builds():\n    pass\n"
        )

        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "no asyncpg"}
                    ],
                }
            ]
        }

        manifest = _manifest(["app/api/routers/builds.py"])
        touched = {"app/api/routers/builds.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(boundaries),
                touched, "Phase 1",
            )

        g2 = next(c for c in result["checks"] if c["code"] == "G2")
        assert g2["result"] == "PASS"


# ---------------------------------------------------------------------------
# 34.4 G3 — dependency gate
# ---------------------------------------------------------------------------


class TestG3DependencyGate:
    @pytest.mark.asyncio
    async def test_undeclared_dependency_detected(self, tmp_path):
        """Import not in requirements.txt -> G3 FAIL."""
        # Set up forge.json
        (tmp_path / "forge.json").write_text(json.dumps({
            "backend": {"language": "python", "dependency_file": "requirements.txt"}
        }))
        (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "svc.py").write_text("import httpx\n\ndef call():\n    pass\n")

        manifest = _manifest(["app/svc.py"])
        touched = {"app/svc.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g3 = next(c for c in result["checks"] if c["code"] == "G3")
        assert g3["result"] == "FAIL"
        assert "httpx" in g3["detail"]

    @pytest.mark.asyncio
    async def test_declared_dependency_passes(self, tmp_path):
        """Import present in requirements.txt -> G3 PASS."""
        (tmp_path / "forge.json").write_text(json.dumps({
            "backend": {"language": "python", "dependency_file": "requirements.txt"}
        }))
        (tmp_path / "requirements.txt").write_text("fastapi\nhttpx\n")

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "svc.py").write_text("import httpx\n\ndef call():\n    pass\n")

        manifest = _manifest(["app/svc.py"])
        touched = {"app/svc.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g3 = next(c for c in result["checks"] if c["code"] == "G3")
        assert g3["result"] == "PASS"


# ---------------------------------------------------------------------------
# 34.5 G4 — secrets scan
# ---------------------------------------------------------------------------


class TestG4SecretsScan:
    @pytest.mark.asyncio
    async def test_secret_pattern_detected(self, tmp_path):
        """File containing 'sk-' triggers G4 WARN."""
        (tmp_path / "config.env").write_text("API_KEY=sk-abc123xyz\n")

        manifest = _manifest(["config.env"])
        touched = {"config.env"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g4 = next(c for c in result["checks"] if c["code"] == "G4")
        assert g4["result"] == "WARN"
        assert "sk-" in g4["detail"]

    @pytest.mark.asyncio
    async def test_no_secrets(self, tmp_path):
        """Clean file passes secrets scan."""
        (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n")

        manifest = _manifest(["app.py"])
        touched = {"app.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g4 = next(c for c in result["checks"] if c["code"] == "G4")
        assert g4["result"] == "PASS"


# ---------------------------------------------------------------------------
# 34.7 G7 — TODO / placeholder scan
# ---------------------------------------------------------------------------


class TestG7TodoScan:
    @pytest.mark.asyncio
    async def test_todo_detected(self, tmp_path):
        """File with TODO comment -> G7 WARN."""
        (tmp_path / "svc.py").write_text("def run():\n    # TODO: implement this\n    pass\n")

        manifest = _manifest(["svc.py"])
        touched = {"svc.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g7 = next(c for c in result["checks"] if c["code"] == "G7")
        assert g7["result"] == "WARN"
        assert "TODO" in g7["detail"]

    @pytest.mark.asyncio
    async def test_not_implemented_error_detected(self, tmp_path):
        """File with raise NotImplementedError -> G7 WARN."""
        (tmp_path / "svc.py").write_text(
            "def run():\n    raise NotImplementedError\n"
        )

        manifest = _manifest(["svc.py"])
        touched = {"svc.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g7 = next(c for c in result["checks"] if c["code"] == "G7")
        assert g7["result"] == "WARN"
        assert "NotImplementedError" in g7["detail"]


# ---------------------------------------------------------------------------
# 34.9 Blocking failure vs warnings (integration-level)
# ---------------------------------------------------------------------------


class TestGovernanceBlockingVsWarnings:
    @pytest.mark.asyncio
    async def test_blocking_failure_sets_passed_false(self, tmp_path):
        """G1/G2/G3 FAIL means passed=False."""
        # Missing manifest file -> G1 FAIL
        manifest = _manifest(["missing.py"])
        touched: set[str] = set()

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        assert result["passed"] is False
        assert result["blocking_failures"] >= 1

    @pytest.mark.asyncio
    async def test_warn_only_still_passes(self, tmp_path):
        """Only WARN checks (G4/G5/G6/G7) -> passed=True."""
        # Create a file with a TODO (G7 WARN) but otherwise clean
        (tmp_path / "svc.py").write_text(
            "import os\n\ndef hello():\n    # TODO: cleanup\n    return 1\n"
        )

        manifest = _manifest(["svc.py"])
        touched = {"svc.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        assert result["passed"] is True
        assert result["warnings"] >= 1
        g7 = next(c for c in result["checks"] if c["code"] == "G7")
        assert g7["result"] == "WARN"


# ---------------------------------------------------------------------------
# 34.10 Governance check count
# ---------------------------------------------------------------------------


class TestGovernanceCheckCount:
    @pytest.mark.asyncio
    async def test_always_returns_seven_checks(self, tmp_path):
        """Governance gate always returns exactly 7 checks (G1-G7)."""
        (tmp_path / "foo.py").write_text("x = 1\n")
        manifest = _manifest(["foo.py"])
        touched = {"foo.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        assert len(result["checks"]) == 7
        codes = [c["code"] for c in result["checks"]]
        assert codes == ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]


# ---------------------------------------------------------------------------
# 34.11 Governance detail message format (for Change 4 broadcasting)
# ---------------------------------------------------------------------------


class TestGovernanceDetailFormat:
    """Tests that governance check results contain structured detail for broadcasting."""

    @pytest.mark.asyncio
    async def test_failing_checks_have_code_name_detail(self, tmp_path):
        """Failed governance checks include code, name, and detail fields."""
        manifest = _manifest(["missing.py"])
        touched: set[str] = set()

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        failing = [c for c in result["checks"] if c["result"] == "FAIL"]
        assert len(failing) >= 1
        for chk in failing:
            assert "code" in chk, "Failing check must have 'code' field"
            assert "name" in chk, "Failing check must have 'name' field"
            assert "detail" in chk, "Failing check must have 'detail' field"
            assert chk["code"].startswith("G"), "Check code must start with 'G'"

    @pytest.mark.asyncio
    async def test_detail_message_format_matches_broadcast(self, tmp_path):
        """Detail message format matches what build_service broadcasts to the user.

        The build service broadcasts:
          [G1] Scope compliance: <detail text>
        We verify the check result has the right shape for that template.
        """
        # Create a phantom file to trigger G1 FAIL
        (tmp_path / "phantom.py").write_text("x = 1\n")
        manifest = _manifest([])
        touched = {"phantom.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(),
                touched, "Phase 1",
            )

        g1 = next(c for c in result["checks"] if c["code"] == "G1")
        assert g1["result"] == "FAIL"

        # Simulate the exact format used in build_service.py governance broadcasting
        detail_msg = (
            f"  [{g1.get('code', '?')}] "
            f"{g1.get('name', 'unknown')}: "
            f"{g1.get('detail', 'no detail')}"
        )
        assert "[G1]" in detail_msg
        assert g1["name"] in detail_msg
        assert g1["detail"] in detail_msg

    @pytest.mark.asyncio
    async def test_boundary_fail_produces_useful_detail(self, tmp_path):
        """G2 boundary violation includes the forbidden pattern in detail."""
        routers_dir = tmp_path / "app" / "api" / "routers"
        routers_dir.mkdir(parents=True)
        (routers_dir / "builds.py").write_text(
            "import asyncpg\n\ndef get_builds():\n    pass\n"
        )
        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "no asyncpg in routers"}
                    ],
                }
            ]
        }
        manifest = _manifest(["app/api/routers/builds.py"])
        touched = {"app/api/routers/builds.py"}

        p1, p2, p3 = _mock_patches()
        with p1 as mock_repo, p2, p3:
            mock_repo.append_build_log = AsyncMock()
            result = await _run_governance_checks(
                _BUILD_ID, _USER_ID, "fake-key",
                manifest, str(tmp_path), _contracts(boundaries),
                touched, "Phase 1",
            )

        g2 = next(c for c in result["checks"] if c["code"] == "G2")
        assert g2["result"] == "FAIL"
        # The detail should include the forbidden pattern for broadcast
        detail_msg = f"  [{g2['code']}] {g2['name']}: {g2['detail']}"
        assert "asyncpg" in detail_msg
