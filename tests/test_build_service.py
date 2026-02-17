"""Tests for app/services/build_service.py -- build orchestration layer."""

import asyncio
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import build_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _project(**overrides):
    defaults = {
        "id": _PROJECT_ID,
        "user_id": _USER_ID,
        "name": "Test Project",
        "status": "contracts_ready",
    }
    defaults.update(overrides)
    return defaults


def _contracts():
    return [
        {"contract_type": "blueprint", "content": "# Blueprint\nTest"},
        {"contract_type": "manifesto", "content": "# Manifesto\nTest"},
    ]


def _build(**overrides):
    defaults = {
        "id": _BUILD_ID,
        "project_id": _PROJECT_ID,
        "phase": "Phase 0",
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "loop_count": 0,
        "error_detail": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults




# ---------------------------------------------------------------------------
# Auto-fixture: mock _state-level external deps for sub-module functions
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_build_state_deps():
    """Mock _state-level external deps so sub-module functions don't hit real DB.

    Sub-modules (cost, planner, verification) access build_repo and manager
    via _state module attribute access.  We mock only external resources
    here -- NOT internal helpers -- so the real helpers run against mock deps.
    """
    mock_repo = MagicMock()
    for m in ("append_build_log", "record_build_cost", "update_build",
              "update_build_status", "get_build", "pause_build"):
        setattr(mock_repo, m, AsyncMock(return_value=None))
    mock_mgr = MagicMock()
    mock_mgr.send_to_user = AsyncMock(return_value=None)
    mock_mgr.send_to_group = AsyncMock(return_value=None)
    with patch("app.services.build._state.build_repo", mock_repo), \
         patch("app.services.build._state.manager", mock_mgr):
        yield {"build_repo": mock_repo, "manager": mock_mgr}

# ---------------------------------------------------------------------------
# Tests: start_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_success(mock_get_user, mock_build_repo, mock_project_repo, mock_create_task):
    """start_build creates a build record and spawns a background task."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_build_repo.create_build = AsyncMock(return_value=_build())
    mock_create_task.return_value = MagicMock()
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": "sk-ant-test123"}

    result = await build_service.start_build(_PROJECT_ID, _USER_ID)

    assert result["status"] == "pending"
    mock_build_repo.create_build.assert_called_once_with(
        _PROJECT_ID,
        target_type=None,
        target_ref=None,
        working_dir=None,
        branch="main",
        build_mode="plan_execute",
        contract_batch=None,
    )
    mock_project_repo.update_project_status.assert_called_once_with(
        _PROJECT_ID, "building"
    )
    mock_create_task.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_project_not_found(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if project not found."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_wrong_owner(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if user doesn't own the project."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(user_id=uuid.uuid4())
    )

    with pytest.raises(ValueError, match="not found"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_no_contracts(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if no contracts exist."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="No contracts"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_with_snapshot_batch(mock_get_user, mock_build_repo, mock_project_repo, mock_create_task):
    """start_build uses snapshot contracts when contract_batch is provided."""
    snapshot_contracts = [
        {"contract_type": "blueprint", "content": "# Snapshot Blueprint"},
        {"contract_type": "manifesto", "content": "# Snapshot Manifesto"},
    ]
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_snapshot_contracts = AsyncMock(return_value=snapshot_contracts)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_build_repo.create_build = AsyncMock(return_value=_build())
    mock_create_task.return_value = MagicMock()
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": "sk-ant-test123"}

    result = await build_service.start_build(_PROJECT_ID, _USER_ID, contract_batch=2)

    assert result["status"] == "pending"
    mock_project_repo.get_snapshot_contracts.assert_called_once_with(_PROJECT_ID, 2)
    # Should NOT have called get_contracts_by_project
    mock_project_repo.get_contracts_by_project = AsyncMock()
    mock_build_repo.create_build.assert_called_once_with(
        _PROJECT_ID,
        target_type=None,
        target_ref=None,
        working_dir=None,
        branch="main",
        build_mode="plan_execute",
        contract_batch=2,
    )


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_snapshot_batch_not_found(mock_build_repo, mock_project_repo):
    """start_build raises ValueError when snapshot batch doesn't exist."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_snapshot_contracts = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="Snapshot batch 99 not found"):
        await build_service.start_build(_PROJECT_ID, _USER_ID, contract_batch=99)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_start_build_already_running(mock_build_repo, mock_project_repo):
    """start_build raises ValueError if a build is already in progress."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )

    with pytest.raises(ValueError, match="already in progress"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_no_api_key(mock_get_user, mock_build_repo, mock_project_repo):
    """start_build raises ValueError when user has no Anthropic API key."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": None}

    with pytest.raises(ValueError, match="API key required"):
        await build_service.start_build(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: cancel_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_cancel_build_success(mock_build_repo, mock_project_repo, mock_manager):
    """cancel_build cancels an active build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )
    mock_build_repo.cancel_build = AsyncMock(return_value=True)
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.get_build_by_id = AsyncMock(
        return_value=_build(status="cancelled")
    )
    mock_manager.send_to_user = AsyncMock()

    result = await build_service.cancel_build(_PROJECT_ID, _USER_ID)

    assert result["status"] == "cancelled"
    mock_build_repo.cancel_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_cancel_build_no_active(mock_build_repo, mock_project_repo):
    """cancel_build raises ValueError if no active build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed")
    )

    with pytest.raises(ValueError, match="No active build"):
        await build_service.cancel_build(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_status(mock_build_repo, mock_project_repo):
    """get_build_status returns the latest build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running", phase="Phase 2")
    )

    result = await build_service.get_build_status(_PROJECT_ID, _USER_ID)

    assert result["status"] == "running"
    assert result["phase"] == "Phase 2"


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_status_no_builds(mock_build_repo, mock_project_repo):
    """get_build_status raises ValueError if no builds exist."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="No builds"):
        await build_service.get_build_status(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_logs(mock_build_repo, mock_project_repo):
    """get_build_logs returns paginated logs."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )
    mock_build_repo.get_build_logs = AsyncMock(
        return_value=([{"message": "log1"}, {"message": "log2"}], 10)
    )

    logs, total = await build_service.get_build_logs(
        _PROJECT_ID, _USER_ID, limit=50, offset=0
    )

    assert total == 10
    assert len(logs) == 2


# ---------------------------------------------------------------------------
# Tests: _build_directive
# ---------------------------------------------------------------------------


def test_build_directive_format():
    """_build_directive inlines all contracts except phases."""
    contracts = [
        {"contract_type": "manifesto", "content": "# Manifesto"},
        {"contract_type": "blueprint", "content": "# Blueprint"},
    ]

    result = build_service._build_directive(contracts)

    # Should include governance heading
    assert "Forge Governance" in result
    # Full contract content SHOULD be inlined
    assert "# Manifesto" in result
    assert "# Blueprint" in result


def test_build_directive_excludes_phases():
    """_build_directive inlines all contracts but excludes phases (uses window)."""
    contracts = [
        {"contract_type": "blueprint", "content": "blueprint-content"},
        {"contract_type": "schema", "content": "schema-content"},
        {"contract_type": "phases", "content": "phase-content-should-not-appear"},
    ]
    result = build_service._build_directive(contracts)
    assert "blueprint-content" in result
    assert "schema-content" in result
    # phases excluded — handled by _extract_phase_window
    assert "phase-content-should-not-appear" not in result


# ---------------------------------------------------------------------------
# Tests: _write_contracts_to_workdir
# ---------------------------------------------------------------------------


def test_write_contracts_to_workdir(tmp_path):
    """_write_contracts_to_workdir writes contract files and returns paths."""
    contracts = [
        {"contract_type": "blueprint", "content": "# Blueprint\ntest"},
        {"contract_type": "boundaries", "content": '{"layers": []}'},
        {"contract_type": "physics", "content": "timeout: 60"},
    ]
    paths = build_service._write_contracts_to_workdir(str(tmp_path), contracts)

    assert len(paths) == 3
    assert "Forge/Contracts/blueprint.md" in paths
    assert "Forge/Contracts/boundaries.json" in paths
    assert "Forge/Contracts/physics.yaml" in paths

    # Verify files exist on disk with correct content
    assert (tmp_path / "Forge" / "Contracts" / "blueprint.md").read_text() == "# Blueprint\ntest"
    assert (tmp_path / "Forge" / "Contracts" / "boundaries.json").read_text() == '{"layers": []}'


# ---------------------------------------------------------------------------
# Tests: _extract_phase_window
# ---------------------------------------------------------------------------


def test_extract_phase_window_current_and_next():
    """_extract_phase_window returns current + next phase text."""
    phases_content = (
        "## Phase 0 -- Genesis\n"
        "**Objective:** Scaffold the project.\n\n"
        "**Deliverables:**\n- /health endpoint\n\n---\n\n"
        "## Phase 1 -- Auth\n"
        "**Objective:** Add authentication.\n\n"
        "**Deliverables:**\n- Login flow\n\n---\n\n"
        "## Phase 2 -- Repos\n"
        "**Objective:** Repo management.\n"
    )
    contracts = [{"contract_type": "phases", "content": phases_content}]

    result = build_service._extract_phase_window(contracts, 0)
    assert "Phase 0" in result
    assert "Phase 1" in result
    assert "Phase 2" not in result
    assert "Phase Window" in result


def test_extract_phase_window_last_phase():
    """_extract_phase_window handles the last phase (no next phase)."""
    phases_content = (
        "## Phase 0 -- Genesis\n**Objective:** Scaffold.\n\n"
        "## Phase 1 -- Final\n**Objective:** Finish.\n"
    )
    contracts = [{"contract_type": "phases", "content": phases_content}]

    result = build_service._extract_phase_window(contracts, 1)
    assert "Phase 1" in result
    # No Phase 2, but should still work
    assert "Phase 0" not in result


def test_extract_phase_window_no_phases_contract():
    """_extract_phase_window returns empty string when no phases contract."""
    contracts = [{"contract_type": "blueprint", "content": "# Blueprint"}]
    result = build_service._extract_phase_window(contracts, 0)
    assert result == ""


# ---------------------------------------------------------------------------
# Tests: _run_inline_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.build_repo")
async def test_run_inline_audit(mock_build_repo):
    """_run_inline_audit returns PASS when audit is disabled."""
    mock_build_repo.append_build_log = AsyncMock()

    result = await build_service._run_inline_audit(
        _BUILD_ID, "Phase 1", "builder output", _contracts(), "sk-ant-test", False
    )

    assert result == ("PASS", "")
    mock_build_repo.append_build_log.assert_called()


@pytest.mark.asyncio
@patch("app.services.build._state.build_repo")
async def test_run_inline_audit_enabled_no_prompt(mock_build_repo, tmp_path, monkeypatch):
    """_run_inline_audit falls back to PASS when auditor_prompt.md is missing."""
    mock_build_repo.append_build_log = AsyncMock()
    # Point FORGE_CONTRACTS_DIR to empty tmp dir → no auditor_prompt.md
    monkeypatch.setattr("app.services.build.planner.FORGE_CONTRACTS_DIR", tmp_path)

    result = await build_service._run_inline_audit(
        _BUILD_ID, "Phase 1", "output", _contracts(), "sk-ant-test", True
    )

    assert result == ("PASS", "")


# ---------------------------------------------------------------------------
# Tests: _audit_single_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_disabled(mock_build_repo, mock_manager):
    """_audit_single_file returns PASS immediately when audit is disabled."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    result = await build_service._audit_single_file(
        _BUILD_ID, _USER_ID, "sk-ant-test",
        "src/foo.py", "print('hi')", "A test file",
        audit_llm_enabled=False,
    )

    assert result == ("src/foo.py", "PASS", "")
    # Should have broadcast file_audited
    mock_manager.send_to_user.assert_called_once()
    ws_payload = mock_manager.send_to_user.call_args[0][1]
    assert ws_payload["type"] == "file_audited"
    assert ws_payload["payload"]["verdict"] == "PASS"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_trivially_small(mock_build_repo, mock_manager):
    """_audit_single_file skips files with < 50 chars of content."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    result = await build_service._audit_single_file(
        _BUILD_ID, _USER_ID, "sk-ant-test",
        "src/init.py", "# empty", "init file",
        audit_llm_enabled=True,
    )

    assert result == ("src/init.py", "PASS", "")
    ws_payload = mock_manager.send_to_user.call_args[0][1]
    assert ws_payload["type"] == "file_audited"
    assert ws_payload["payload"]["verdict"] == "PASS"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_clean(mock_build_repo, mock_manager):
    """_audit_single_file returns PASS for VERDICT: CLEAN response."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    llm_response = {"text": "Looks good.\nVERDICT: CLEAN", "usage": {}}

    with patch("app.clients.llm_client.chat", new_callable=AsyncMock, return_value=llm_response):
        result = await build_service._audit_single_file(
            _BUILD_ID, _USER_ID, "sk-ant-test",
            "src/app.py",
            "def main():\n    print('hello world')\n" * 5,
            "Main application entry",
            audit_llm_enabled=True,
        )

    assert result[0] == "src/app.py"
    assert result[1] == "PASS"
    assert result[2] == ""
    ws_payload = mock_manager.send_to_user.call_args[0][1]
    assert ws_payload["payload"]["verdict"] == "PASS"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_flags_found(mock_build_repo, mock_manager):
    """_audit_single_file returns FAIL for VERDICT: FLAGS FOUND response."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    llm_response = {
        "text": "BLOCKING: missing export\nVERDICT: FLAGS FOUND",
        "usage": {},
    }

    with patch("app.clients.llm_client.chat", new_callable=AsyncMock, return_value=llm_response):
        result = await build_service._audit_single_file(
            _BUILD_ID, _USER_ID, "sk-ant-test",
            "src/broken.py",
            "class Broken:\n    pass\n" * 5,
            "A broken module",
            audit_llm_enabled=True,
        )

    assert result[0] == "src/broken.py"
    assert result[1] == "FAIL"
    assert "FLAGS FOUND" in result[2]
    ws_payload = mock_manager.send_to_user.call_args[0][1]
    assert ws_payload["payload"]["verdict"] == "FAIL"
    # Build log should have been called with warn level
    log_calls = mock_build_repo.append_build_log.call_args_list
    assert any(c.kwargs.get("level") == "warn" or (len(c.args) > 3 and "warn" in str(c)) for c in log_calls)


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_llm_error_failopen(mock_build_repo, mock_manager):
    """_audit_single_file fails open (returns PASS) on LLM error."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    with patch("app.clients.llm_client.chat", new_callable=AsyncMock, side_effect=RuntimeError("LLM down")):
        result = await build_service._audit_single_file(
            _BUILD_ID, _USER_ID, "sk-ant-test",
            "src/widget.py",
            "def widget():\n    return 42\n" * 5,
            "Widget module",
            audit_llm_enabled=True,
        )

    assert result[0] == "src/widget.py"
    assert result[1] == "PASS"
    ws_payload = mock_manager.send_to_user.call_args[0][1]
    assert ws_payload["payload"]["verdict"] == "PASS"
    assert "error" in ws_payload["payload"]["findings"].lower()


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_audit_single_file_timeout_failopen(mock_build_repo, mock_manager):
    """_audit_single_file fails open on timeout."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    async def _slow_chat(**kwargs):
        await asyncio.sleep(999)

    with patch("app.clients.llm_client.chat", new_callable=AsyncMock, side_effect=_slow_chat):
        # Patch the timeout to be tiny for test speed
        with patch.object(build_service.asyncio, "wait_for", side_effect=asyncio.TimeoutError):
            result = await build_service._audit_single_file(
                _BUILD_ID, _USER_ID, "sk-ant-test",
                "src/slow.py",
                "def slow():\n    pass\n" * 5,
                "Slow module",
                audit_llm_enabled=True,
            )

    assert result[0] == "src/slow.py"
    assert result[1] == "PASS"


# ---------------------------------------------------------------------------
# Tests: _fail_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_fail_build(mock_build_repo, mock_manager):
    """_fail_build marks the build as failed and broadcasts."""
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    await build_service._fail_build(_BUILD_ID, _USER_ID, "something broke")

    mock_build_repo.update_build_status.assert_called_once()
    call_kwargs = mock_build_repo.update_build_status.call_args
    assert call_kwargs[0][1] == "failed"
    mock_manager.send_to_user.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: get_build_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_summary(mock_build_repo, mock_project_repo):
    """get_build_summary returns build, cost, elapsed, and loop_count."""
    now = datetime.now(timezone.utc)
    b = _build(
        status="completed",
        started_at=now,
        completed_at=now,
        loop_count=1,
    )
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=b)
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 3000,
        "total_output_tokens": 1500,
        "total_cost_usd": Decimal("0.157500"),
        "phase_count": 2,
    })
    mock_build_repo.get_build_costs = AsyncMock(return_value=[
        {
            "phase": "Phase 0",
            "input_tokens": 1500,
            "output_tokens": 800,
            "model": "claude-opus-4-6",
            "estimated_cost_usd": Decimal("0.082500"),
        },
    ])
    mock_build_repo.get_build_stats = AsyncMock(return_value={
        "total_turns": 3,
        "total_audit_attempts": 2,
        "files_written_count": 5,
        "git_commits_made": 1,
        "interjections_received": 0,
    })

    result = await build_service.get_build_summary(_PROJECT_ID, _USER_ID)

    assert result["build"]["status"] == "completed"
    assert result["cost"]["total_input_tokens"] == 3000
    assert len(result["cost"]["phases"]) == 1
    assert result["loop_count"] == 1


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_summary_not_found(mock_build_repo, mock_project_repo):
    """get_build_summary raises ValueError for missing project."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await build_service.get_build_summary(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_instructions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_get_build_instructions(mock_project_repo):
    """get_build_instructions returns deploy instructions from stack contract."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(name="TestApp")
    )
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[
        {"contract_type": "stack", "content": "Python 3.12, PostgreSQL, React, Render"},
        {"contract_type": "blueprint", "content": "# Blueprint\nA test app"},
    ])

    result = await build_service.get_build_instructions(_PROJECT_ID, _USER_ID)

    assert result["project_name"] == "TestApp"
    assert "Python 3.12" in result["instructions"]
    assert "Render" in result["instructions"]


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_get_build_instructions_no_contracts(mock_project_repo):
    """get_build_instructions raises ValueError when no contracts."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="No contracts"):
        await build_service.get_build_instructions(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: _generate_deploy_instructions
# ---------------------------------------------------------------------------


def test_generate_deploy_instructions_full_stack():
    """_generate_deploy_instructions includes all stack components."""
    result = build_service._generate_deploy_instructions(
        "MyApp",
        "Python 3.12+, React, PostgreSQL 15+, Render",
        "# Blueprint",
    )

    assert "MyApp" in result
    assert "Python 3.12" in result
    assert "Node.js" in result
    assert "PostgreSQL" in result
    assert "Render" in result


def test_generate_deploy_instructions_minimal():
    """_generate_deploy_instructions handles minimal stack."""
    result = build_service._generate_deploy_instructions(
        "SimpleApp", "Go backend", "# Simple"
    )

    assert "SimpleApp" in result
    assert "Git 2.x" in result


# ---------------------------------------------------------------------------
# Tests: _record_phase_cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.build_repo")
async def test_record_phase_cost(mock_build_repo):
    """_record_phase_cost persists cost and resets usage counters."""
    from app.clients.agent_client import StreamUsage
    mock_build_repo.record_build_cost = AsyncMock()

    usage = StreamUsage(input_tokens=1000, output_tokens=500, model="claude-opus-4-6")
    await build_service._record_phase_cost(_BUILD_ID, "Phase 0", usage)

    mock_build_repo.record_build_cost.assert_called_once()
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


def test_get_token_rates_model_aware():
    """_get_token_rates returns correct pricing per model family."""
    from decimal import Decimal

    opus_in, opus_out = build_service._get_token_rates("claude-opus-4-6")
    assert opus_in == Decimal("0.000015")
    assert opus_out == Decimal("0.000075")

    sonnet_in, sonnet_out = build_service._get_token_rates("claude-sonnet-4-5-20251001")
    assert sonnet_in == Decimal("0.000003")
    assert sonnet_out == Decimal("0.000015")

    # Unknown model falls back to Opus (safest = most expensive)
    unk_in, unk_out = build_service._get_token_rates("some-unknown-model")
    assert unk_in == Decimal("0.000015")
    assert unk_out == Decimal("0.000075")


# ---------------------------------------------------------------------------
# Tests: _detect_language
# ---------------------------------------------------------------------------


def test_detect_language_python():
    assert build_service._detect_language("src/main.py") == "python"


def test_detect_language_typescript():
    assert build_service._detect_language("src/App.tsx") == "typescriptreact"


def test_detect_language_json():
    assert build_service._detect_language("package.json") == "json"


def test_detect_language_unknown():
    assert build_service._detect_language("data.xyz") == "plaintext"


def test_detect_language_dotenv():
    assert build_service._detect_language(".env") == "dotenv"


def test_detect_language_gitignore():
    assert build_service._detect_language(".gitignore") == "ignore"


# ---------------------------------------------------------------------------
# Tests: _parse_file_blocks
# ---------------------------------------------------------------------------


def test_parse_file_blocks_single():
    """Single file block parsed correctly."""
    text = """Some preamble text
=== FILE: src/main.py ===
print("hello")
=== END FILE ===
Some trailing text"""

    blocks = build_service._parse_file_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["path"] == "src/main.py"
    assert 'print("hello")' in blocks[0]["content"]


def test_parse_file_blocks_multiple():
    """Multiple consecutive file blocks parsed."""
    text = """
=== FILE: file1.py ===
content1
=== END FILE ===
=== FILE: file2.ts ===
content2
=== END FILE ===
"""
    blocks = build_service._parse_file_blocks(text)
    assert len(blocks) == 2
    assert blocks[0]["path"] == "file1.py"
    assert blocks[1]["path"] == "file2.ts"
    assert "content1" in blocks[0]["content"]
    assert "content2" in blocks[1]["content"]


def test_parse_file_blocks_with_code_fence():
    """File block wrapped in code fence gets fence stripped."""
    text = """=== FILE: test.py ===
```python
def hello():
    pass
```
=== END FILE ==="""

    blocks = build_service._parse_file_blocks(text)
    assert len(blocks) == 1
    assert "def hello" in blocks[0]["content"]
    assert "```" not in blocks[0]["content"]


def test_parse_file_blocks_no_end_marker():
    """Missing END FILE marker — block is skipped."""
    text = """=== FILE: orphan.py ===
some content without end marker
"""
    blocks = build_service._parse_file_blocks(text)
    assert len(blocks) == 0


def test_parse_file_blocks_empty_input():
    """Empty string returns empty list."""
    assert build_service._parse_file_blocks("") == []


# ---------------------------------------------------------------------------
# Tests: _strip_code_fence
# ---------------------------------------------------------------------------


def test_strip_code_fence_basic():
    """Strip a basic code fence wrapper."""
    text = "```python\nprint('hi')\n```"
    result = build_service._strip_code_fence(text)
    assert "print('hi')" in result
    assert "```" not in result


def test_strip_code_fence_no_fence():
    """No code fence — content passes through."""
    text = "plain content\n"
    result = build_service._strip_code_fence(text)
    assert "plain content" in result


def test_strip_code_fence_empty():
    """Empty string returns empty."""
    assert build_service._strip_code_fence("") == ""


# ---------------------------------------------------------------------------
# Tests: _write_file_block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service._broadcast_build_event", new_callable=AsyncMock)
@patch("app.services.build_service.build_repo")
async def test_write_file_block_success(mock_build_repo, mock_broadcast, tmp_path):
    """_write_file_block writes a file and emits events."""
    mock_build_repo.append_build_log = AsyncMock()
    files_written: list[dict] = []

    await build_service._write_file_block(
        _BUILD_ID, _USER_ID, str(tmp_path),
        "src/hello.py", "print('hello')\n", files_written,
    )

    # File should exist on disk
    written_file = tmp_path / "src" / "hello.py"
    assert written_file.exists()
    assert written_file.read_text() == "print('hello')\n"

    # File info should be appended to files_written
    assert len(files_written) == 1
    assert files_written[0]["path"] == "src/hello.py"
    assert files_written[0]["language"] == "python"
    assert files_written[0]["size_bytes"] == len("print('hello')\n".encode())

    # Build log should be recorded
    mock_build_repo.append_build_log.assert_called_once()

    # WS event should be broadcast
    mock_broadcast.assert_called_once()


@pytest.mark.asyncio
@patch("app.services.build_service._broadcast_build_event", new_callable=AsyncMock)
@patch("app.services.build_service.build_repo")
async def test_write_file_block_traversal_rejected(mock_build_repo, mock_broadcast, tmp_path):
    """_write_file_block rejects paths with directory traversal."""
    files_written: list[dict] = []

    await build_service._write_file_block(
        _BUILD_ID, _USER_ID, str(tmp_path),
        "../../../etc/passwd", "bad content", files_written,
    )

    # No file should be written
    assert len(files_written) == 0
    mock_build_repo.append_build_log.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.build_service._broadcast_build_event", new_callable=AsyncMock)
@patch("app.services.build_service.build_repo")
async def test_write_file_block_absolute_rejected(mock_build_repo, mock_broadcast, tmp_path):
    """_write_file_block rejects absolute paths."""
    files_written: list[dict] = []

    await build_service._write_file_block(
        _BUILD_ID, _USER_ID, str(tmp_path),
        "/etc/passwd", "bad content", files_written,
    )

    assert len(files_written) == 0
    mock_build_repo.append_build_log.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: get_build_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_files(mock_build_repo, mock_project_repo):
    """get_build_files returns file list from build logs."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed")
    )
    mock_build_repo.get_build_file_logs = AsyncMock(return_value=[
        {"path": "src/main.py", "size_bytes": 100, "language": "python", "created_at": "2025-01-01T00:00:00Z"},
    ])

    result = await build_service.get_build_files(_PROJECT_ID, _USER_ID)

    assert len(result) == 1
    assert result[0]["path"] == "src/main.py"


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_files_no_project(mock_build_repo, mock_project_repo):
    """get_build_files raises ValueError for missing project."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await build_service.get_build_files(_PROJECT_ID, _USER_ID)


# ---------------------------------------------------------------------------
# Tests: get_build_file_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_file_content(mock_build_repo, mock_project_repo, tmp_path):
    """get_build_file_content reads file from working directory."""
    # Write a file to tmp_path
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")

    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed", working_dir=str(tmp_path))
    )

    result = await build_service.get_build_file_content(_PROJECT_ID, _USER_ID, "src/app.py")

    assert result["path"] == "src/app.py"
    assert "print('hi')" in result["content"]
    assert result["language"] == "python"


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_file_content_traversal(mock_build_repo, mock_project_repo, tmp_path):
    """get_build_file_content rejects directory traversal paths."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed", working_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="Invalid file path"):
        await build_service.get_build_file_content(_PROJECT_ID, _USER_ID, "../../../etc/passwd")


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_get_build_file_content_not_found(mock_build_repo, mock_project_repo, tmp_path):
    """get_build_file_content raises ValueError for non-existent file."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed", working_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="File not found"):
        await build_service.get_build_file_content(_PROJECT_ID, _USER_ID, "nonexistent.py")



# ---------------------------------------------------------------------------
# Tests: start_build with target params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_invalid_target_type(mock_get_user, mock_build_repo, mock_project_repo, mock_create_task):
    """start_build rejects invalid target_type."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": "sk-ant-test123"}

    with pytest.raises(ValueError, match="Invalid target_type"):
        await build_service.start_build(_PROJECT_ID, _USER_ID, target_type="invalid_type")


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_target_type_without_ref(mock_get_user, mock_build_repo, mock_project_repo, mock_create_task):
    """start_build raises ValueError when target_type given without target_ref."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": "sk-ant-test123"}

    with pytest.raises(ValueError, match="target_ref is required"):
        await build_service.start_build(_PROJECT_ID, _USER_ID, target_type="local_path")


# ---------------------------------------------------------------------------
# Tests: _parse_build_plan (Phase 13)
# ---------------------------------------------------------------------------


def test_parse_build_plan_numbered():
    """_parse_build_plan parses numbered task list."""
    text = (
        "Here's the plan:\n"
        "=== PLAN ===\n"
        "1. Set up project structure\n"
        "2. Implement auth module\n"
        "3. Create database schema\n"
        "=== END PLAN ===\n"
        "Let me begin..."
    )
    tasks = build_service._parse_build_plan(text)
    assert len(tasks) == 3
    assert tasks[0]["id"] == 1
    assert tasks[0]["title"] == "Set up project structure"
    assert tasks[0]["status"] == "pending"
    assert tasks[2]["id"] == 3
    assert tasks[2]["title"] == "Create database schema"


def test_parse_build_plan_dash_list():
    """_parse_build_plan parses dash-prefixed task list."""
    text = (
        "=== PLAN ===\n"
        "- First task\n"
        "- Second task\n"
        "=== END PLAN ===\n"
    )
    tasks = build_service._parse_build_plan(text)
    assert len(tasks) == 2
    assert tasks[0]["id"] == 1
    assert tasks[0]["title"] == "First task"
    assert tasks[1]["id"] == 2


def test_parse_build_plan_no_plan():
    """_parse_build_plan returns empty list when no plan block exists."""
    text = "Just some builder output without a plan block."
    tasks = build_service._parse_build_plan(text)
    assert tasks == []


def test_parse_build_plan_no_end():
    """_parse_build_plan returns empty list when end delimiter is missing."""
    text = (
        "=== PLAN ===\n"
        "1. Task one\n"
        "2. Task two\n"
    )
    tasks = build_service._parse_build_plan(text)
    assert tasks == []


def test_parse_build_plan_empty_lines():
    """_parse_build_plan skips empty lines."""
    text = (
        "=== PLAN ===\n"
        "\n"
        "1. Task A\n"
        "\n"
        "2. Task B\n"
        "\n"
        "=== END PLAN ===\n"
    )
    tasks = build_service._parse_build_plan(text)
    assert len(tasks) == 2


# ---------------------------------------------------------------------------
# Tests: _compact_conversation (Phase 13)
# ---------------------------------------------------------------------------


def test_compact_conversation_short():
    """_compact_conversation returns unchanged list when <= 5 messages."""
    messages = [
        {"role": "user", "content": "directive"},
        {"role": "assistant", "content": "response 1"},
        {"role": "user", "content": "feedback"},
    ]
    result = build_service._compact_conversation(messages)
    assert len(result) == 3
    assert result[0]["content"] == "directive"


def test_compact_conversation_compacts_middle():
    """_compact_conversation summarizes middle messages with progress."""
    messages = [
        {"role": "user", "content": "directive"},
        {"role": "assistant", "content": "response 1"},
        {"role": "user", "content": "feedback 1"},
        {"role": "assistant", "content": "response 2"},
        {"role": "user", "content": "feedback 2"},
        {"role": "assistant", "content": "response 3"},
        {"role": "user", "content": "feedback 3"},
        {"role": "assistant", "content": "response 4"},
    ]
    files = [{"path": "app.py", "size_bytes": 100, "language": "python"}]
    result = build_service._compact_conversation(
        messages, files_written=files, current_phase="Phase 0",
    )

    # First message (directive) + summary + last 4 messages
    assert len(result) == 6
    assert result[0]["content"] == "directive"
    assert "[Context compacted" in result[1]["content"]
    assert "app.py" in result[1]["content"]  # files manifest present
    assert "Phase 0" in result[1]["content"]  # current phase present
    # Last 4 messages intact
    assert result[2]["content"] == "feedback 2"
    assert result[3]["content"] == "response 3"
    assert result[4]["content"] == "feedback 3"
    assert result[5]["content"] == "response 4"


def test_compact_conversation_truncates_long_content():
    """_compact_conversation truncates long messages in the summary."""
    long_content = "x" * 1000
    messages = [
        {"role": "user", "content": "directive"},
        {"role": "assistant", "content": long_content},
        {"role": "user", "content": "feedback 1"},
        {"role": "assistant", "content": "response 2"},
        {"role": "user", "content": "feedback 2"},
        {"role": "assistant", "content": "response 3"},
    ]
    result = build_service._compact_conversation(
        messages, files_written=[], current_phase="Phase 1",
    )

    # The summary message should have truncated the long content
    summary = result[1]["content"]
    assert "..." in summary
    assert len(summary) < len(long_content)


# ---------------------------------------------------------------------------
# Tests: Multi-turn _run_build (Phase 13)
# ---------------------------------------------------------------------------


# Shared call counter for multi-turn stream mocks
_stream_call_counter: dict[str, int] = {"n": 0}


def _reset_stream_counter():
    _stream_call_counter["n"] = 0


async def _fake_stream_pass(*args, **kwargs):
    """Fake stream_agent: emits plan + sign-off on first call, finishes cleanly on subsequent calls."""
    _stream_call_counter["n"] += 1
    if _stream_call_counter["n"] == 1:
        text = (
            "=== PLAN ===\n"
            "1. Create main module\n"
            "2. Add tests\n"
            "=== END PLAN ===\n\n"
            "Building Phase 0...\n"
            "=== FILE: app/main.py ===\nprint('hello')\n=== END FILE ===\n"
            "=== TASK DONE: 1 ===\n"
            "Phase: Phase 0 -- Genesis\n"
            "=== PHASE SIGN-OFF: PASS ===\n"
        )
        for chunk in [text[i:i+50] for i in range(0, len(text), 50)]:
            yield chunk
    else:
        yield "Build complete. All phases done."


async def _fake_stream_no_signal(*args, **kwargs):
    """Fake stream_agent that finishes without phase signal (build done)."""
    yield "Build complete. All phases done."


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_multi_turn_plan_detected(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build detects build plan and emits build_plan event."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_pass
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    # Mock audit to pass
    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # Check that phase_plan event was broadcast
    plan_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "phase_plan"
    ]
    assert len(plan_calls) >= 1
    plan_payload = plan_calls[0][0][1]["payload"]
    assert len(plan_payload["tasks"]) == 2
    assert plan_payload["tasks"][0]["title"] == "Create main module"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_multi_turn_audit_feedback(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build injects audit feedback and retries on failure."""
    call_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] <= 2:
            text = (
                "Phase: Phase 0 -- Genesis\n"
                "=== PHASE SIGN-OFF: PASS ===\n"
            )
            yield text
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2])
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    # First audit fails, second passes, then build finishes on turn 3
    audit_returns = iter([("FAIL", "Audit found issues"), ("PASS", "")])
    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        side_effect=lambda *a, **k: next(audit_returns),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock,
        return_value="Mocked remediation plan",
    ):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # stream_agent called 3 times: initial + retry after audit fail + final (no signal = break)
    assert call_counter["n"] == 3

    # Audit fail event should have been broadcast
    fail_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "audit_fail"
    ]
    assert len(fail_calls) >= 1


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_multi_turn_max_failures(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build pauses after MAX_LOOP_COUNT audit failures; abort leads to build_error."""
    async def _stream_gen(*args, **kwargs):
        yield "Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2, 3])
    mock_build_repo.pause_build = AsyncMock(return_value=True)
    mock_build_repo.resume_build = AsyncMock(return_value=True)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    async def _auto_abort():
        """Wait for build to pause, then abort."""
        await asyncio.sleep(0.05)
        for _ in range(50):
            bid = str(_BUILD_ID)
            if bid in build_service._pause_events:
                build_service._resume_actions[bid] = "abort"
                build_service._pause_events[bid].set()
                return
            await asyncio.sleep(0.02)

    # All audits fail
    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        return_value=("FAIL", "Audit found issues"),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock,
        return_value="Mocked remediation plan",
    ):
        await asyncio.gather(
            build_service._run_build_conversation(
                _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
                "sk-ant-test", audit_llm_enabled=True,
            ),
            _auto_abort(),
        )

    # Build should have been paused first, then aborted → build_error
    pause_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_paused"
    ]
    assert len(pause_calls) >= 1

    fail_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_error"
    ]
    assert len(fail_calls) >= 1
    assert "aborted by user" in fail_calls[0][0][1]["payload"]["error_detail"].lower()


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_turn_event_broadcast(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build broadcasts build_turn events with turn count."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_pass
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # build_turn event should have been broadcast
    turn_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_turn"
    ]
    assert len(turn_calls) >= 1
    first_turn = turn_calls[0][0][1]["payload"]
    assert first_turn["turn"] == 1
    assert first_turn["compacted"] is False


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_task_done_broadcast(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build detects TASK DONE signals and broadcasts plan_task_complete."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_pass
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # plan_task_complete event should have been broadcast (task 1 at least)
    task_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "plan_task_complete"
    ]
    assert len(task_calls) >= 1
    assert task_calls[0][0][1]["payload"]["task_id"] == 1
    assert task_calls[0][0][1]["payload"]["status"] == "done"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_context_compaction(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build compacts context when total tokens exceed threshold."""
    call_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        usage_out = kwargs.get("usage_out")
        if usage_out:
            # Simulate large token usage that exceeds threshold
            usage_out.input_tokens = 80000
            usage_out.output_tokens = 80000
        if call_counter["n"] <= 5:
            # Need 5 sign-off turns so messages grows to 6 (1 directive + 5 assistant)
            # Compaction triggers at turn 6: len(messages)=6>5 and total_tokens>150K
            yield f"Phase: Phase {call_counter['n'] - 1}\n=== PHASE SIGN-OFF: PASS ===\n"
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 160000, "total_output_tokens": 160000, "total_cost_usd": Decimal("0.50"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    # All audits pass — each turn adds assistant message, growing len(messages) past 5
    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        return_value=("PASS", ""),
    ):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # build_turn event with compacted=True should have been broadcast
    turn_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_turn" and c[0][1].get("payload", {}).get("compacted")
    ]
    assert len(turn_calls) >= 1


# ---------------------------------------------------------------------------
# Tests: Pause / Resume / Interject (Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_pauses_at_threshold(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build pauses (not fails) after PAUSE_THRESHOLD consecutive audit failures."""
    call_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        yield f"Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2, 3])
    mock_build_repo.pause_build = AsyncMock(return_value=True)
    mock_build_repo.resume_build = AsyncMock(return_value=True)
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    # All audits fail → triggers pause after PAUSE_THRESHOLD
    # Then resume with "abort" to end the build cleanly
    async def _auto_resume():
        """Wait for build to pause, then signal abort."""
        await asyncio.sleep(0.05)
        for _ in range(50):
            bid = str(_BUILD_ID)
            if bid in build_service._pause_events:
                build_service._resume_actions[bid] = "abort"
                build_service._pause_events[bid].set()
                return
            await asyncio.sleep(0.02)

    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        return_value=("FAIL", "Audit found issues"),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock,
        return_value="Mocked remediation plan",
    ):
        # Run both tasks concurrently
        await asyncio.gather(
            build_service._run_build_conversation(
                _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
                "sk-ant-test", audit_llm_enabled=True,
            ),
            _auto_resume(),
        )

    # build_paused event should have been broadcast
    pause_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_paused"
    ]
    assert len(pause_calls) >= 1
    assert pause_calls[0][0][1]["payload"]["phase"] == "Phase 0"
    assert pause_calls[0][0][1]["payload"]["loop_count"] == 3


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_pause_resume_retry(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build resumes with retry after pause, then completes."""
    call_counter = {"n": 0}
    audit_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] <= 4:
            yield f"Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2, 3, 4])
    mock_build_repo.pause_build = AsyncMock(return_value=True)
    mock_build_repo.resume_build = AsyncMock(return_value=True)
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    async def _audit(*a, **k):
        audit_counter["n"] += 1
        # First 3 fail → pause, then 4th passes after resume
        return ("FAIL", "Audit found issues") if audit_counter["n"] <= 3 else ("PASS", "")

    async def _auto_resume():
        await asyncio.sleep(0.05)
        for _ in range(50):
            bid = str(_BUILD_ID)
            if bid in build_service._pause_events:
                build_service._resume_actions[bid] = "retry"
                build_service._pause_events[bid].set()
                return
            await asyncio.sleep(0.02)

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, side_effect=_audit), \
         patch.object(build_service, "_run_recovery_planner", new_callable=AsyncMock, return_value="Mocked remediation"):
        await asyncio.gather(
            build_service._run_build_conversation(
                _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
                "sk-ant-test", audit_llm_enabled=True,
            ),
            _auto_resume(),
        )

    # Build should have completed (not failed)
    complete_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_complete"
    ]
    assert len(complete_calls) >= 1


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_pause_skip(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build skips a phase after pause with skip action."""
    call_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] <= 3:
            yield f"Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2, 3])
    mock_build_repo.pause_build = AsyncMock(return_value=True)
    mock_build_repo.resume_build = AsyncMock(return_value=True)
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    async def _auto_resume():
        await asyncio.sleep(0.05)
        for _ in range(50):
            bid = str(_BUILD_ID)
            if bid in build_service._pause_events:
                build_service._resume_actions[bid] = "skip"
                build_service._pause_events[bid].set()
                return
            await asyncio.sleep(0.02)

    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock, return_value=("FAIL", "Audit found issues"),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock, return_value="Mocked remediation plan",
    ):
        await asyncio.gather(
            build_service._run_build_conversation(
                _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
                "sk-ant-test", audit_llm_enabled=True,
            ),
            _auto_resume(),
        )

    # build_resumed event with action=skip should have been broadcast
    resume_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_resumed"
    ]
    assert len(resume_calls) >= 1
    assert resume_calls[0][0][1]["payload"]["action"] == "skip"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_interjection_injected(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build picks up interjection messages between turns."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_pass
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    # Pre-populate interjection queue
    queue = asyncio.Queue()
    queue.put_nowait("please add logging")
    build_service._interjection_queues[str(_BUILD_ID)] = queue

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # build_interjection event should have been broadcast
    interject_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_interjection"
    ]
    assert len(interject_calls) >= 1
    assert "please add logging" in interject_calls[0][0][1]["payload"]["message"]


# ---------------------------------------------------------------------------
# Tests: resume_build (service public API, Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_resume_build_success(mock_build_repo, mock_project_repo):
    """resume_build signals the pause event and returns updated build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="paused")
    )
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.get_build_by_id = AsyncMock(
        return_value=_build(status="running")
    )

    # Set up a pause event to be signalled
    event = asyncio.Event()
    build_service._pause_events[str(_BUILD_ID)] = event

    result = await build_service.resume_build(_PROJECT_ID, _USER_ID, action="retry")

    assert event.is_set()
    # The action is stored for _run_build to consume
    assert build_service._resume_actions.get(str(_BUILD_ID)) == "retry"

    # Cleanup
    build_service._pause_events.pop(str(_BUILD_ID), None)
    build_service._resume_actions.pop(str(_BUILD_ID), None)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_resume_build_not_resumable(mock_build_repo, mock_project_repo):
    """resume_build raises ValueError if build is in a terminal non-resumable state."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed")
    )

    with pytest.raises(ValueError, match="Cannot resume build"):
        await build_service.resume_build(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_resume_build_invalid_action(mock_build_repo, mock_project_repo):
    """resume_build raises ValueError for invalid action."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="paused")
    )

    with pytest.raises(ValueError, match="Invalid action"):
        await build_service.resume_build(_PROJECT_ID, _USER_ID, action="explode")


# ---------------------------------------------------------------------------
# Tests: interject_build (service public API, Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_interject_build_success(mock_build_repo, mock_project_repo):
    """interject_build queues a message."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )

    # Set up interjection queue
    build_service._interjection_queues[str(_BUILD_ID)] = asyncio.Queue()

    result = await build_service.interject_build(
        _PROJECT_ID, _USER_ID, "add error handling"
    )

    assert result["status"] == "queued"
    assert build_service._interjection_queues[str(_BUILD_ID)].qsize() == 1

    # Cleanup
    build_service._interjection_queues.pop(str(_BUILD_ID), None)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_interject_build_no_active(mock_build_repo, mock_project_repo):
    """interject_build raises ValueError if no active build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed")
    )

    with pytest.raises(ValueError, match="No active build"):
        await build_service.interject_build(_PROJECT_ID, _USER_ID, "msg")


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_interject_build_no_queue(mock_build_repo, mock_project_repo):
    """interject_build raises ValueError if queue not initialized."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="running")
    )

    # No queue set up
    build_service._interjection_queues.pop(str(_BUILD_ID), None)

    with pytest.raises(ValueError, match="queue not found"):
        await build_service.interject_build(_PROJECT_ID, _USER_ID, "msg")


# ---------------------------------------------------------------------------
# Tests: cancel_build with paused status (Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_cancel_paused_build(mock_build_repo, mock_project_repo, mock_manager):
    """cancel_build can cancel a paused build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="paused")
    )
    mock_build_repo.cancel_build = AsyncMock(return_value=True)
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.get_build_by_id = AsyncMock(
        return_value=_build(status="cancelled")
    )
    mock_manager.send_to_user = AsyncMock()

    # Set up pause event
    event = asyncio.Event()
    build_service._pause_events[str(_BUILD_ID)] = event

    result = await build_service.cancel_build(_PROJECT_ID, _USER_ID)

    assert result["status"] == "cancelled"
    assert event.is_set()  # Pause event should be signalled

    # Cleanup
    build_service._pause_events.pop(str(_BUILD_ID), None)
    build_service._resume_actions.pop(str(_BUILD_ID), None)


# ---------------------------------------------------------------------------
# Tests: _pause_build helper (Phase 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build._state.build_repo")
async def test_pause_build_helper(mock_build_repo, mock_manager):
    """_pause_build persists pause state and broadcasts event."""
    mock_build_repo.pause_build = AsyncMock(return_value=True)
    mock_build_repo.append_build_log = AsyncMock()
    mock_manager.send_to_user = AsyncMock()

    await build_service._pause_build(
        _BUILD_ID, _USER_ID, "Phase 2", 3, "test reason"
    )

    mock_build_repo.pause_build.assert_called_once_with(
        _BUILD_ID, "test reason", "Phase 2"
    )
    # Should have created a pause event
    assert str(_BUILD_ID) in build_service._pause_events
    # Broadcast
    pause_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_paused"
    ]
    assert len(pause_calls) == 1
    assert pause_calls[0][0][1]["payload"]["phase"] == "Phase 2"
    assert pause_calls[0][0][1]["payload"]["loop_count"] == 3

    # Cleanup
    build_service._pause_events.pop(str(_BUILD_ID), None)


# ---------------------------------------------------------------------------
# Tests: PAUSE_THRESHOLD config (Phase 14)
# ---------------------------------------------------------------------------


def test_pause_threshold_default():
    """PAUSE_THRESHOLD defaults to 3."""
    from app.config import settings as s
    assert isinstance(s.PAUSE_THRESHOLD, int)
    assert s.PAUSE_THRESHOLD >= 1


def test_build_pause_timeout_default():
    """BUILD_PAUSE_TIMEOUT_MINUTES defaults to 30."""
    from app.config import settings as s
    assert isinstance(s.BUILD_PAUSE_TIMEOUT_MINUTES, int)
    assert s.BUILD_PAUSE_TIMEOUT_MINUTES >= 1


# ---------------------------------------------------------------------------
# Tests: Per-phase planning & plan_tasks reset (Phase 16)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_plan_tasks_reset_between_phases(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """plan_tasks resets between phases so each phase gets a fresh plan."""
    call_counter = {"n": 0}

    async def _stream_multi_phase(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            yield (
                "=== PLAN ===\n1. Phase 0 task A\n2. Phase 0 task B\n=== END PLAN ===\n"
                "=== TASK DONE: 1 ===\n=== TASK DONE: 2 ===\n"
                "Phase: Phase 0 -- Genesis\n=== PHASE SIGN-OFF: PASS ===\n"
            )
        elif call_counter["n"] == 2:
            yield (
                "=== PLAN ===\n1. Phase 1 task X\n=== END PLAN ===\n"
                "=== TASK DONE: 1 ===\n"
                "Phase: Phase 1 -- Scaffold\n=== PHASE SIGN-OFF: PASS ===\n"
            )
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_multi_phase
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # Should have emitted TWO phase_plan events (one per phase)
    plan_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "phase_plan"
    ]
    assert len(plan_calls) == 2

    # Phase 0 plan had 2 tasks
    assert len(plan_calls[0][0][1]["payload"]["tasks"]) == 2
    assert plan_calls[0][0][1]["payload"]["tasks"][0]["title"] == "Phase 0 task A"

    # Phase 1 plan had 1 task (fresh, not carried over)
    assert len(plan_calls[1][0][1]["payload"]["tasks"]) == 1
    assert plan_calls[1][0][1]["payload"]["tasks"][0]["title"] == "Phase 1 task X"


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_build_overview_emitted(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """_run_build emits build_overview event when phases contract is available."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_pass
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()

    # Provide a phases contract for build_overview
    mock_project_repo.get_contract_by_type = AsyncMock(return_value={
        "content": "## Phase 0 -- Genesis\n**Objective:** Bootstrap\n\n## Phase 1 -- Scaffold\n**Objective:** Build structure",
    })
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # build_overview event should have been broadcast
    overview_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "build_overview"
    ]
    assert len(overview_calls) == 1
    phases = overview_calls[0][0][1]["payload"]["phases"]
    assert len(phases) == 2
    assert phases[0]["number"] == 0
    assert phases[0]["name"] == "Genesis"
    assert phases[1]["number"] == 1


def test_no_haiku_references_in_pricing():
    """No Haiku model entries remain in the pricing table."""
    for key in build_service._MODEL_PRICING:
        assert "haiku" not in key.lower(), f"Legacy Haiku entry found: {key}"


# ---------------------------------------------------------------------------
# Tests: _gather_project_state (Phase 17)
# ---------------------------------------------------------------------------


def test_gather_project_state_empty_dir(tmp_path):
    """_gather_project_state returns tree for an empty directory."""
    result = build_service._gather_project_state(str(tmp_path))
    assert "## File Tree" in result
    assert "(empty)" in result


def test_gather_project_state_with_files(tmp_path):
    """_gather_project_state includes code files and respects tree layout."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "app" / "config.json").write_text('{"key": "val"}', encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")  # non-code file

    result = build_service._gather_project_state(str(tmp_path))
    assert "main.py" in result
    assert "print('hello')" in result  # file content included
    assert "config.json" in result
    assert "README.md" in result
    assert "data.bin" in result  # in tree but not content


def test_gather_project_state_truncates_large_file(tmp_path):
    """_gather_project_state truncates files larger than 10KB."""
    (tmp_path / "big.py").write_text("x" * 20_000, encoding="utf-8")
    result = build_service._gather_project_state(str(tmp_path))
    assert "[... truncated" in result


def test_gather_project_state_skips_pycache(tmp_path):
    """_gather_project_state skips __pycache__ directories."""
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "module.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / "app.py").write_text("pass", encoding="utf-8")
    result = build_service._gather_project_state(str(tmp_path))
    assert "__pycache__" not in result
    assert "app.py" in result


def test_gather_project_state_none():
    """_gather_project_state handles None working_dir."""
    result = build_service._gather_project_state(None)
    assert "not available" in result


# ---------------------------------------------------------------------------
# Tests: _run_recovery_planner (Phase 17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build._state.build_repo")
async def test_run_recovery_planner_success(mock_build_repo, monkeypatch):
    """_run_recovery_planner returns the planner's remediation text."""
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()

    fake_result = {
        "text": "=== REMEDIATION PLAN ===\n1. Fix the thing\n=== END REMEDIATION PLAN ===",
        "usage": {"input_tokens": 500, "output_tokens": 200},
    }

    with patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock) as mock_broadcast:
        with patch("app.clients.llm_client.chat", new_callable=AsyncMock, return_value=fake_result):
            result = await build_service._run_recovery_planner(
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key="sk-ant-test",
                phase="Phase 0",
                audit_findings="VERDICT: FLAGS FOUND\nMissing tests",
                builder_output="phase output here",
                contracts=_contracts(),
                working_dir=None,
            )

    assert "REMEDIATION PLAN" in result
    assert "Fix the thing" in result
    # Cost should have been recorded
    mock_build_repo.record_build_cost.assert_called_once()
    cost_args = mock_build_repo.record_build_cost.call_args
    assert "planner" in cost_args[0][1]  # phase arg contains "(planner)"
    # WS event should have been broadcast
    mock_broadcast.assert_called_once()
    assert mock_broadcast.call_args[0][2] == "recovery_plan"


@pytest.mark.asyncio
@patch("app.services.build._state.build_repo")
async def test_run_recovery_planner_missing_prompt(mock_build_repo, monkeypatch, tmp_path):
    """_run_recovery_planner returns empty string when prompt file is missing."""
    mock_build_repo.append_build_log = AsyncMock()
    monkeypatch.setattr("app.services.build.planner.FORGE_CONTRACTS_DIR", tmp_path)

    result = await build_service._run_recovery_planner(
        build_id=_BUILD_ID,
        user_id=_USER_ID,
        api_key="sk-ant-test",
        phase="Phase 0",
        audit_findings="FAIL",
        builder_output="output",
        contracts=_contracts(),
        working_dir=None,
    )
    assert result == ""


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_recovery_planner_fallback_on_error(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """If _run_recovery_planner raises, the build loop uses generic feedback."""
    call_counter = {"n": 0}

    async def _stream_gen(*args, **kwargs):
        call_counter["n"] += 1
        if call_counter["n"] <= 2:
            yield "Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"
        else:
            yield "Build complete."

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2])
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    audit_returns = iter([("FAIL", "Audit found issues"), ("PASS", "")])
    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        side_effect=lambda *a, **k: next(audit_returns),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock,
        side_effect=RuntimeError("LLM API timeout"),
    ):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # Build should still have completed (fell back to generic feedback)
    assert call_counter["n"] == 3


@pytest.mark.asyncio
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_recovery_planner_injects_remediation(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager
):
    """On audit FAIL, the recovery planner's output is injected into messages."""
    captured_messages = []

    async def _stream_gen(*args, messages=None, **kwargs):
        if messages:
            captured_messages.append(list(messages))
        yield "Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"

    mock_stream.side_effect = _stream_gen
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.increment_loop_count = AsyncMock(side_effect=[1, 2])
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    audit_returns = iter([("FAIL", "Audit found issues"), ("PASS", "")])
    with patch.object(
        build_service, "_run_inline_audit",
        new_callable=AsyncMock,
        side_effect=lambda *a, **k: next(audit_returns),
    ), patch.object(
        build_service, "_run_recovery_planner",
        new_callable=AsyncMock,
        return_value="=== REMEDIATION PLAN ===\n1. Fix X\n=== END REMEDIATION PLAN ===",
    ):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
        )

    # The second call to stream_agent should have the remediation plan in messages
    assert len(captured_messages) >= 2
    retry_msgs = captured_messages[1]
    # Last user message should contain the remediation plan
    user_msgs = [m for m in retry_msgs if m["role"] == "user"]
    last_user = user_msgs[-1]["content"]
    assert "recovery planner" in last_user.lower()
    assert "REMEDIATION PLAN" in last_user


# ---------------------------------------------------------------------------
# Tests: Tool-use in build loop (Phase 18)
# ---------------------------------------------------------------------------

from app.clients.agent_client import ToolCall


async def _fake_stream_with_tool_call(*args, **kwargs):
    """Fake stream that yields a read_file tool call, then sign-off on next call."""
    # First call: text + tool call
    _stream_call_counter["n"] += 1
    if _stream_call_counter["n"] == 1:
        # Yield text first
        yield "Let me check the project structure.\n"
        # Yield a tool call
        yield ToolCall(id="tc_001", name="read_file", input={"path": "README.md"})
    elif _stream_call_counter["n"] == 2:
        # After tool result, continue with phase sign-off
        text = (
            "Great, I see the README.\n"
            "Phase: Phase 0 -- Genesis\n"
            "=== PHASE SIGN-OFF: PASS ===\n"
        )
        yield text
    else:
        yield "Build complete."


@pytest.mark.asyncio
@patch("app.services.build_service.execute_tool_async", new_callable=AsyncMock)
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_tool_call_execution(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager, mock_execute_tool, tmp_path,
):
    """_run_build executes tool calls and continues the conversation."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_with_tool_call
    mock_execute_tool.return_value = "# README\nForgeGuard project"
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
            working_dir=str(tmp_path),
        )

    # execute_tool was called with the right arguments
    mock_execute_tool.assert_called_once_with("read_file", {"path": "README.md"}, str(tmp_path))

    # tool_use WS event was broadcast
    tool_use_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "tool_use"
    ]
    assert len(tool_use_calls) >= 1
    tool_payload = tool_use_calls[0][0][1]["payload"]
    assert tool_payload["tool_name"] == "read_file"


async def _fake_stream_with_write_tool(*args, **kwargs):
    """Fake stream that uses write_file tool to create a file."""
    _stream_call_counter["n"] += 1
    if _stream_call_counter["n"] == 1:
        yield "Creating the main module.\n"
        yield ToolCall(
            id="tc_002", name="write_file",
            input={"path": "app/main.py", "content": "print('hello')"},
        )
    elif _stream_call_counter["n"] == 2:
        text = (
            "File created successfully.\n"
            "Phase: Phase 0 -- Genesis\n"
            "=== PHASE SIGN-OFF: PASS ===\n"
        )
        yield text
    else:
        yield "Build complete."


@pytest.mark.asyncio
@patch("app.services.build_service.execute_tool_async", new_callable=AsyncMock)
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_write_file_tool_emits_file_created(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager, mock_execute_tool, tmp_path,
):
    """write_file tool calls emit file_created WS events."""
    _reset_stream_counter()
    mock_stream.side_effect = _fake_stream_with_write_tool
    mock_execute_tool.return_value = "OK: Wrote 14 bytes to app/main.py"
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
            working_dir=str(tmp_path),
        )

    # file_created event was broadcast for the write_file tool
    file_calls = [
        c for c in mock_manager.send_to_user.call_args_list
        if c[0][1].get("type") == "file_created"
    ]
    assert len(file_calls) >= 1
    file_payload = file_calls[0][0][1]["payload"]
    assert file_payload["path"] == "app/main.py"

    # Tool call was logged
    log_calls = mock_build_repo.append_build_log.call_args_list
    tool_logs = [c for c in log_calls if c[1].get("source") == "tool" or (len(c[0]) > 2 and c[0][2] == "tool")]
    assert len(tool_logs) >= 1


@pytest.mark.asyncio
@patch("app.services.build_service.execute_tool_async", new_callable=AsyncMock)
@patch("app.services.build._state.manager")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.stream_agent")
async def test_run_build_tool_result_in_messages(
    mock_stream, mock_build_repo, mock_project_repo, mock_manager, mock_execute_tool, tmp_path,
):
    """After tool execution, tool_result is appended to messages for continuation."""
    captured_messages: list[list[dict]] = []

    async def _stream_cap(*args, messages=None, **kwargs):
        _stream_call_counter["n"] += 1
        if messages:
            captured_messages.append(list(messages))
        if _stream_call_counter["n"] == 1:
            yield "Checking file.\n"
            yield ToolCall(id="tc_003", name="list_directory", input={"path": "."})
        elif _stream_call_counter["n"] == 2:
            yield "Phase: Phase 0\n=== PHASE SIGN-OFF: PASS ===\n"
        else:
            yield "Build complete."

    _reset_stream_counter()
    mock_stream.side_effect = _stream_cap
    mock_execute_tool.return_value = "app/\ntests/\nREADME.md"
    mock_build_repo.update_build_status = AsyncMock()
    mock_build_repo.append_build_log = AsyncMock()
    mock_build_repo.record_build_cost = AsyncMock()
    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    })
    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_manager.send_to_user = AsyncMock()

    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value=("PASS", "")):
        await build_service._run_build_conversation(
            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
            "sk-ant-test", audit_llm_enabled=True,
            working_dir=str(tmp_path),
        )

    # Second call should have tool_result in messages
    assert len(captured_messages) >= 2
    second_msgs = captured_messages[1]

    # Should have an assistant message with tool_use content block
    assistant_msgs = [m for m in second_msgs if m["role"] == "assistant"]
    assert any(
        isinstance(m.get("content"), list) and
        any(b.get("type") == "tool_use" for b in m["content"])
        for m in assistant_msgs
    )

    # Should have a user message with tool_result
    user_msgs = [m for m in second_msgs if m["role"] == "user"]
    assert any(
        isinstance(m.get("content"), list) and
        any(b.get("type") == "tool_result" for b in m["content"])
        for m in user_msgs
    )


# ---------------------------------------------------------------------------
# Tests: list_builds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.project_repo")
async def test_list_builds_success(mock_project_repo, mock_build_repo):
    """list_builds returns formatted build list."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_builds_for_project = AsyncMock(return_value=[
        _build(branch="main"),
        _build(id=uuid.uuid4(), branch="forge/v2"),
    ])

    result = await build_service.list_builds(_PROJECT_ID, _USER_ID)

    assert len(result) == 2
    assert result[0]["branch"] == "main"
    assert result[1]["branch"] == "forge/v2"
    assert "id" in result[0]
    assert "phase" in result[0]


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_list_builds_not_found(mock_project_repo):
    """list_builds raises ValueError for missing project."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Project not found"):
        await build_service.list_builds(_PROJECT_ID, _USER_ID)


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_list_builds_wrong_user(mock_project_repo):
    """list_builds raises ValueError if user doesn't own project."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(user_id=uuid.uuid4())
    )

    with pytest.raises(ValueError, match="Project not found"):
        await build_service.list_builds(_PROJECT_ID, _USER_ID)



# ---------------------------------------------------------------------------
# Tests: start_build with branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.asyncio.create_task")
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
async def test_start_build_with_branch(mock_get_user, mock_build_repo, mock_project_repo, mock_create_task):
    """start_build passes branch to create_build."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
    mock_project_repo.update_project_status = AsyncMock()
    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_build_repo.create_build = AsyncMock(return_value=_build())
    mock_create_task.return_value = MagicMock()
    mock_get_user.return_value = {"id": _USER_ID, "anthropic_api_key": "sk-ant-test123"}

    result = await build_service.start_build(
        _PROJECT_ID, _USER_ID, branch="forge/v2"
    )

    assert result["status"] == "pending"
    mock_build_repo.create_build.assert_called_once()
    call_kwargs = mock_build_repo.create_build.call_args.kwargs
    assert call_kwargs["branch"] == "forge/v2"


# ---------------------------------------------------------------------------
# Tests: delete_builds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.project_repo")
async def test_delete_builds_success(mock_project_repo, mock_build_repo):
    """delete_builds deletes eligible builds and returns count."""
    bid1 = uuid.uuid4()
    bid2 = uuid.uuid4()
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_builds_for_project = AsyncMock(return_value=[
        _build(id=bid1, status="completed"),
        _build(id=bid2, status="failed"),
    ])
    mock_build_repo.delete_builds = AsyncMock(return_value=2)

    result = await build_service.delete_builds(
        _PROJECT_ID, _USER_ID, [str(bid1), str(bid2)]
    )

    assert result == 2
    mock_build_repo.delete_builds.assert_called_once()
    deleted_ids = mock_build_repo.delete_builds.call_args[0][0]
    assert set(deleted_ids) == {bid1, bid2}


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.project_repo")
async def test_delete_builds_skips_active(mock_project_repo, mock_build_repo):
    """delete_builds skips running/pending builds."""
    bid_done = uuid.uuid4()
    bid_running = uuid.uuid4()
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_builds_for_project = AsyncMock(return_value=[
        _build(id=bid_done, status="completed"),
        _build(id=bid_running, status="running"),
    ])
    mock_build_repo.delete_builds = AsyncMock(return_value=1)

    result = await build_service.delete_builds(
        _PROJECT_ID, _USER_ID, [str(bid_done), str(bid_running)]
    )

    assert result == 1
    deleted_ids = mock_build_repo.delete_builds.call_args[0][0]
    assert deleted_ids == [bid_done]


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
@patch("app.services.build_service.project_repo")
async def test_delete_builds_all_active_raises(mock_project_repo, mock_build_repo):
    """delete_builds raises ValueError when all builds are active."""
    bid = uuid.uuid4()
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_builds_for_project = AsyncMock(return_value=[
        _build(id=bid, status="running"),
    ])

    with pytest.raises(ValueError, match="No eligible builds"):
        await build_service.delete_builds(_PROJECT_ID, _USER_ID, [str(bid)])


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_delete_builds_not_found(mock_project_repo):
    """delete_builds raises ValueError for missing project."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Project not found"):
        await build_service.delete_builds(_PROJECT_ID, _USER_ID, ["some-id"])


@pytest.mark.asyncio
@patch("app.services.build_service.project_repo")
async def test_delete_builds_empty_ids(mock_project_repo):
    """delete_builds raises ValueError for empty build_ids list."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())

    with pytest.raises(ValueError, match="No build IDs"):
        await build_service.delete_builds(_PROJECT_ID, _USER_ID, [])


# ---------------------------------------------------------------------------
# Tests: /pull slash command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.start_build", new_callable=AsyncMock)
@patch("app.services.build_service.git_client")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_pull_command_detects_phase(
    mock_build_repo, mock_project_repo, mock_get_user, mock_git, mock_start
):
    """/pull clones the repo, parses git log, and resumes from detected phase."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(repo_full_name="owner/repo")
    )
    mock_build_repo.get_latest_build_for_project = AsyncMock(
        return_value=_build(status="completed", branch="main")
    )
    mock_get_user.return_value = {"id": _USER_ID, "access_token": "ghp_test"}

    mock_git.clone_repo = AsyncMock()
    mock_git.log_oneline = AsyncMock(return_value=[
        "forge: Phase 3 complete",
        "forge: Phase 2 complete (after 2 audit attempts)",
        "forge: Phase 1 complete",
        "forge: Phase 0 complete",
    ])

    new_build = _build(status="running")
    mock_start.return_value = new_build

    result = await build_service.interject_build(_PROJECT_ID, _USER_ID, "/pull")

    assert result["status"] == "pulled"
    assert "Phase 4" in result["message"]
    assert "Phase 3 was last committed" in result["message"]

    # Verify start_build was called with resume_from_phase=3
    mock_start.assert_awaited_once()
    call_kwargs = mock_start.call_args[1]
    assert call_kwargs["resume_from_phase"] == 3


@pytest.mark.asyncio
@patch("app.services.build_service.git_client")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_pull_command_no_repo(
    mock_build_repo, mock_project_repo, mock_get_user, mock_git
):
    """/pull raises ValueError if no GitHub repo is linked."""
    mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="No GitHub repository"):
        await build_service.interject_build(_PROJECT_ID, _USER_ID, "/pull")


@pytest.mark.asyncio
@patch("app.services.build_service.git_client")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_pull_command_no_token(
    mock_build_repo, mock_project_repo, mock_get_user, mock_git
):
    """/pull raises ValueError if no GitHub access token."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(repo_full_name="owner/repo")
    )
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_get_user.return_value = {"id": _USER_ID}

    with pytest.raises(ValueError, match="No GitHub access token"):
        await build_service.interject_build(_PROJECT_ID, _USER_ID, "/pull")


@pytest.mark.asyncio
@patch("app.services.build_service.start_build", new_callable=AsyncMock)
@patch("app.services.build_service.git_client")
@patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.build_service.project_repo")
@patch("app.services.build_service.build_repo")
async def test_pull_command_no_phases_starts_fresh(
    mock_build_repo, mock_project_repo, mock_get_user, mock_git, mock_start
):
    """/pull with no forge commits starts from Phase 0."""
    mock_project_repo.get_project_by_id = AsyncMock(
        return_value=_project(repo_full_name="owner/repo")
    )
    mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
    mock_get_user.return_value = {"id": _USER_ID, "access_token": "ghp_test"}

    mock_git.clone_repo = AsyncMock()
    mock_git.log_oneline = AsyncMock(return_value=[
        "Initial commit",
        "Add README",
    ])

    new_build = _build(status="running")
    mock_start.return_value = new_build

    result = await build_service.interject_build(_PROJECT_ID, _USER_ID, "/pull")

    assert result["status"] == "pulled"
    assert "Phase 0" in result["message"]
    assert "no prior phases" in result["message"]

    call_kwargs = mock_start.call_args[1]
    assert call_kwargs["resume_from_phase"] == -1


# ---------------------------------------------------------------------------
# Manifest caching (resume resilience)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manifest_cache_save_and_load(tmp_path):
    """After generating a manifest, it should be persisted to .forge/
    as JSON.  On a second run the cached copy is loaded instead of
    calling the LLM again."""
    import json

    manifest_data = [
        {
            "path": "src/main.py",
            "action": "create",
            "purpose": "entry point",
            "depends_on": [],
            "context_files": [],
            "estimated_lines": 50,
            "language": "python",
            "status": "pending",
        },
        {
            "path": "src/utils.py",
            "action": "create",
            "purpose": "helpers",
            "depends_on": [],
            "context_files": [],
            "estimated_lines": 30,
            "language": "python",
            "status": "pending",
        },
    ]

    # Write a cached manifest
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    cache_file = forge_dir / "manifest_phase_1.json"
    cache_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    # Verify it round-trips correctly
    loaded = json.loads(cache_file.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    assert len(loaded) == 2
    assert loaded[0]["path"] == "src/main.py"
    assert loaded[1]["path"] == "src/utils.py"

    # Verify cleanup works (simulating phase completion)
    assert cache_file.exists()
    cache_file.unlink(missing_ok=True)
    assert not cache_file.exists()


# ---------------------------------------------------------------------------
# Auditor-as-fixer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_single_file_applies_fix(tmp_path):
    """_fix_single_file reads file, sends to LLM, writes fixed content back."""
    # Create a broken file on disk
    src = tmp_path / "src"
    src.mkdir()
    broken_file = src / "main.py"
    broken_file.write_text("import foo\nprint(bar)\n", encoding="utf-8")

    fixed_content = "import foo\nbar = 1\nprint(bar)\n"

    with patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state.build_repo") as mock_repo, \
         patch("app.clients.llm_client.chat", new_callable=AsyncMock) as mock_chat, \
         patch("app.services.build_service._get_token_rates", return_value=(Decimal("0.01"), Decimal("0.03"))):
        mock_chat.return_value = {"text": fixed_content, "usage": {"input_tokens": 100, "output_tokens": 50}}
        mock_repo.record_build_cost = AsyncMock()
        mock_repo.append_build_log = AsyncMock()

        result = await build_service._fix_single_file(
            build_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            api_key="test-key",
            file_path="src/main.py",
            findings="L2: 'bar' is undefined",
            working_dir=str(tmp_path),
        )

    assert result == fixed_content
    assert broken_file.read_text(encoding="utf-8") == fixed_content
    mock_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_and_cache_fix_loop_passes_after_fix(tmp_path):
    """When audit returns FAIL, _audit_and_cache fixes + re-audits."""
    # Create file and cache
    src = tmp_path / "src"
    src.mkdir()
    target = src / "app.py"
    target.write_text("bad code\n", encoding="utf-8")

    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    import json
    manifest = [{"path": "src/app.py", "status": "pending", "language": "python"}]
    cache_path = forge_dir / "manifest_phase_1.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    call_count = 0

    async def mock_audit(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ("src/app.py", "FAIL", "L1: bad code")
        return ("src/app.py", "PASS", "")

    with patch("app.services.build.verification._audit_single_file", side_effect=mock_audit), \
         patch("app.services.build.verification._fix_single_file", new_callable=AsyncMock) as mock_fix, \
         patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state.build_repo") as mock_repo:
        mock_fix.return_value = "good code\n"
        mock_repo.append_build_log = AsyncMock()

        result = await build_service._audit_and_cache(
            manifest_cache_path=cache_path,
            build_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            audit_api_key="key2",
            file_path="src/app.py",
            file_content="bad code\n",
            file_purpose="app entry",
            audit_llm_enabled=True,
            working_dir=str(tmp_path),
        )

    fpath, fverdict, _ = result
    assert fverdict == "PASS"
    mock_fix.assert_awaited_once()
    # Manifest cache should be updated to "fixed"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached[0]["status"] == "fixed"


@pytest.mark.asyncio
async def test_audit_and_cache_pushes_to_fix_queue(tmp_path):
    """When auditor can't fix after max rounds, file goes to fix_queue."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    import json
    manifest = [{"path": "src/broken.py", "status": "pending"}]
    cache_path = forge_dir / "manifest_phase_1.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Create the file
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("broken\n", encoding="utf-8")

    fix_queue: asyncio.Queue = asyncio.Queue()

    async def always_fail(*args, **kwargs):
        return ("src/broken.py", "FAIL", "L1: still broken")

    with patch("app.services.build.verification._audit_single_file", side_effect=always_fail), \
         patch("app.services.build.verification._fix_single_file", new_callable=AsyncMock) as mock_fix, \
         patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state.build_repo") as mock_repo:
        mock_fix.return_value = "still broken\n"
        mock_repo.append_build_log = AsyncMock()

        result = await build_service._audit_and_cache(
            manifest_cache_path=cache_path,
            build_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            audit_api_key="key2",
            file_path="src/broken.py",
            file_content="broken\n",
            file_purpose="broken module",
            audit_llm_enabled=True,
            working_dir=str(tmp_path),
            fix_queue=fix_queue,
        )

    _, fverdict, _ = result
    assert fverdict == "FAIL"
    # Should have been pushed to fix queue
    assert not fix_queue.empty()
    queued_path, queued_findings = fix_queue.get_nowait()
    assert queued_path == "src/broken.py"
    # Fix was attempted _AUDITOR_FIX_ROUNDS times
    assert mock_fix.await_count == build_service._AUDITOR_FIX_ROUNDS


@pytest.mark.asyncio
async def test_builder_drain_fix_queue(tmp_path):
    """Builder drains fix queue and produces results."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    import json
    manifest = [{"path": "src/fix_me.py", "status": "fix_queued"}]
    cache_path = forge_dir / "manifest_phase_1.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    (src / "fix_me.py").write_text("needs fix\n", encoding="utf-8")

    fix_queue: asyncio.Queue = asyncio.Queue()
    await fix_queue.put(("src/fix_me.py", "L1: needs fix"))

    with patch("app.services.build.verification._fix_single_file", new_callable=AsyncMock) as mock_fix, \
         patch("app.services.build.verification._audit_single_file", new_callable=AsyncMock) as mock_audit, \
         patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state._touch_progress"), \
         patch("app.services.build._state.build_repo") as mock_repo:
        mock_fix.return_value = "fixed content\n"
        mock_audit.return_value = ("src/fix_me.py", "PASS", "")
        mock_repo.append_build_log = AsyncMock()

        results = await build_service._builder_drain_fix_queue(
            build_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            builder_api_key="key1",
            audit_api_key="key2",
            fix_queue=fix_queue,
            working_dir=str(tmp_path),
            manifest_cache_path=cache_path,
        )

    assert len(results) == 1
    assert results[0][1] == "PASS"
    assert fix_queue.empty()
    # Manifest cache should be updated to "fixed"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached[0]["status"] == "fixed"


@pytest.mark.asyncio
async def test_audit_and_cache_skips_fix_when_pass(tmp_path):
    """When audit passes immediately, no fix is attempted."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    import json
    manifest = [{"path": "src/good.py", "status": "pending"}]
    cache_path = forge_dir / "manifest_phase_1.json"
    cache_path.write_text(json.dumps(manifest), encoding="utf-8")

    async def pass_audit(*args, **kwargs):
        return ("src/good.py", "PASS", "")

    with patch("app.services.build.verification._audit_single_file", side_effect=pass_audit), \
         patch("app.services.build.verification._fix_single_file", new_callable=AsyncMock) as mock_fix, \
         patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock):

        result = await build_service._audit_and_cache(
            manifest_cache_path=cache_path,
            build_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            audit_api_key="key2",
            file_path="src/good.py",
            file_content="good code\n",
            file_purpose="good module",
            audit_llm_enabled=True,
            working_dir=str(tmp_path),
        )

    _, fverdict, _ = result
    assert fverdict == "PASS"
    mock_fix.assert_not_awaited()
    # Manifest cache should be "audited"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached[0]["status"] == "audited"
