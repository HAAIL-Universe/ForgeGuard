"""Unit tests for the forge_get_phase_window tool handler.

Covers phase extraction logic, edge cases (first phase, last phase,
invalid phase number, malformed phases.md, missing file).
"""

from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PHASES_CONTENT = """\
# Appendix header — should not count as a phase

## Phase 0 — Genesis

**Objective:** Skeleton project that boots.

**Deliverables:**
- `/health` returns 200 (FastAPI)
- `boot.ps1` installs deps and starts server
- `pytest` passes one health test

**Exit criteria:**
- `GET /health` → 200
- `pytest` passes

---

## Phase 1 — Authentication

**Objective:** GitHub OAuth login flow.

**Deliverables:**
- `POST /auth/github` — accepts OAuth code
- `GET /auth/me` — returns current user

**Exit criteria:**
- OAuth round-trip works
- JWT returned on login

---

## Phase 2 — Core Features

**Objective:** Build the main domain model.

**Deliverables:**
- Task CRUD endpoints
- Database tables

**Exit criteria:**
- All tests pass
- `/tasks` returns data
"""


def _make_phases_workdir(tmp_path: Path, content: str = PHASES_CONTENT) -> str:
    """Write phases.md to a Forge/Contracts/ dir under tmp_path."""
    contracts = tmp_path / "Forge" / "Contracts"
    contracts.mkdir(parents=True)
    (contracts / "phases.md").write_text(content, encoding="utf-8")
    return str(tmp_path)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_phase_0_returns_phase_0_and_1(tmp_path):
    """Phase 0 request returns Phase 0 + Phase 1 (look-ahead)."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    assert "Phase 0" in result
    assert "Genesis" in result
    assert "Phase 1" in result
    assert "Authentication" in result
    # Phase 2 should NOT appear
    assert "Core Features" not in result


def test_phase_1_returns_phase_1_and_2(tmp_path):
    """Phase 1 request returns Phase 1 + Phase 2."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 1}, wd)

    assert "Phase 1" in result
    assert "Authentication" in result
    assert "Phase 2" in result
    assert "Core Features" in result
    # Phase 0 should NOT appear
    assert "Genesis" not in result


def test_last_phase_marks_final(tmp_path):
    """Requesting the last phase returns it with 'final phase' annotation."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 2}, wd)

    assert "Phase 2" in result
    assert "Core Features" in result
    assert "final phase" in result.lower()
    # Should not include a Phase 3 that doesn't exist
    assert "Phase 3" not in result


def test_deliverables_content_preserved(tmp_path):
    """Deliverables content from phases.md is included in the window output."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    assert "GET /health" in result
    assert "boot.ps1" in result
    assert "Exit criteria" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_invalid_phase_number_returns_error(tmp_path):
    """Requesting a non-existent phase number returns Error with range info."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 99}, wd)

    assert "Error" in result
    assert "99" in result
    assert "2" in result  # max phase is 2


def test_missing_phases_file_returns_error(tmp_path):
    """Returns Error when phases.md does not exist in the working directory."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    # Create Forge/Contracts/ dir but no phases.md
    contracts = tmp_path / "Forge" / "Contracts"
    contracts.mkdir(parents=True)

    result = _exec_forge_get_phase_window({"phase_number": 0}, str(tmp_path))

    assert "Error" in result
    assert "phases.md" in result


def test_empty_phases_file_returns_error(tmp_path):
    """Returns Error when phases.md has no phase blocks."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path, content="# No phases here\n\nJust a header.\n")
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    assert "Error" in result
    assert "No phases" in result or "not found" in result.lower()


def test_default_phase_number_is_zero(tmp_path):
    """When phase_number is absent from input, defaults to 0."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({}, wd)  # no phase_number key

    assert "Phase 0" in result
    assert "Genesis" in result


def test_single_phase_file(tmp_path):
    """Works correctly when phases.md has only one phase."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    content = (
        "## Phase 0 — Only Phase\n\n"
        "**Objective:** Build everything in one shot.\n\n"
        "**Deliverables:**\n- The whole app\n\n"
        "**Exit criteria:**\n- All tests pass\n"
    )
    wd = _make_phases_workdir(tmp_path, content=content)
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    assert "Phase 0" in result
    assert "Only Phase" in result
    assert "final phase" in result.lower()


def test_phase_window_header_format(tmp_path):
    """The output starts with a ## Phase Window header line."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path)
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    assert result.startswith("## Phase Window")


# ---------------------------------------------------------------------------
# Appendix header doesn't count as a phase
# ---------------------------------------------------------------------------


def test_appendix_header_not_treated_as_phase(tmp_path):
    """The '# Appendix ...' heading at the top of phases.md is not a phase."""
    from app.services.tool_executor import _exec_forge_get_phase_window

    wd = _make_phases_workdir(tmp_path, content=PHASES_CONTENT)
    result = _exec_forge_get_phase_window({"phase_number": 0}, wd)

    # Should not contain the appendix header as a phase
    assert "Appendix" not in result
    # Phase 0 and 1 should still be there
    assert "Genesis" in result
    assert "Authentication" in result
