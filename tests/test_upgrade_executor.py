"""Tests for upgrade_executor service."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.upgrade_executor import (
    _active_upgrades,
    _apply_file_change,
    _audit_file_change,
    _build_deterministic_fix,
    _build_task_with_llm,
    _gather_file_contents,
    _generate_remediation_plan,
    _log,
    _plan_task_with_llm,
    _PlanPoolItem,
    _RemediationItem,
    _run_pre_push_audit,
    _run_retry,
    _strip_codeblock,
    _TokenAccumulator,
    _WorkerSlot,
    _write_audit_trail,
    _write_diff_log,
    _fmt_tokens,
    execute_upgrade,
    get_available_commands,
    get_upgrade_status,
    prepare_upgrade_workspace,
    send_command,
    set_narrator_watching,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(autouse=True)
def _cleanup_upgrades():
    """Ensure no state leaks between tests."""
    yield
    _active_upgrades.clear()

FAKE_PLAN = {
    "executive_brief": {"headline": "Upgrade needed", "health_grade": "C"},
    "migration_recommendations": [
        {
            "id": "MIG-1",
            "from_state": "React 17",
            "to_state": "React 18",
            "priority": "high",
            "effort": "medium",
            "risk": "low",
            "category": "framework",
            "rationale": "EOL",
            "steps": ["Update package.json", "Fix breaking changes"],
            "forge_automatable": True,
        },
    ],
    "summary_stats": {},
}

FAKE_RUN = {
    "id": RUN_ID,
    "user_id": USER_ID,
    "repo_name": "test/repo",
    "scan_type": "deep",
    "status": "completed",
    "checks_passed": 1,
    "checks_failed": 0,
    "checks_warned": 0,
    "results": {
        "renovation_plan": FAKE_PLAN,
        "stack_profile": {"primary_language": "TypeScript"},
    },
}

# -----------------------------------------------------------------------
# get_upgrade_status
# -----------------------------------------------------------------------


def test_get_upgrade_status_returns_none_for_unknown():
    assert get_upgrade_status("nonexistent") is None


def test_get_upgrade_status_returns_state():
    _active_upgrades["test-run"] = {"status": "running", "logs": []}
    try:
        state = get_upgrade_status("test-run")
        assert state is not None
        assert state["status"] == "running"
    finally:
        _active_upgrades.pop("test-run", None)


# -----------------------------------------------------------------------
# _log helper
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_appends_to_state_and_emits():
    rid = "log-test-run"
    _active_upgrades[rid] = {"status": "running", "logs": []}
    try:
        with patch("app.services.upgrade_executor._emit", new_callable=AsyncMock) as mock_emit:
            await _log("user1", rid, "Hello world", "info", "test")
            assert len(_active_upgrades[rid]["logs"]) == 1
            entry = _active_upgrades[rid]["logs"][0]
            assert entry["message"] == "Hello world"
            assert entry["level"] == "info"
            assert entry["source"] == "test"
            assert "timestamp" in entry
            mock_emit.assert_awaited_once()
            call_args = mock_emit.call_args
            assert call_args[0][1] == "upgrade_log"
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_log_handles_missing_state():
    """_log should still emit even if run_id is not in state."""
    with patch("app.services.upgrade_executor._emit", new_callable=AsyncMock) as mock_emit:
        await _log("user1", "missing-run", "msg", "warn")
        mock_emit.assert_awaited_once()


# -----------------------------------------------------------------------
# execute_upgrade — validation
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_upgrade_run_not_found():
    with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await execute_upgrade(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_execute_upgrade_wrong_user():
    other_user = UUID("99999999-9999-9999-9999-999999999999")
    run = {**FAKE_RUN, "user_id": other_user}
    with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = run
        with pytest.raises(ValueError, match="not found"):
            await execute_upgrade(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_execute_upgrade_requires_deep_scan():
    run = {**FAKE_RUN, "scan_type": "quick"}
    with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = run
        with pytest.raises(ValueError, match="deep scan"):
            await execute_upgrade(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_execute_upgrade_no_results():
    run = {**FAKE_RUN, "results": None}
    with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = run
        with pytest.raises(ValueError, match="No results"):
            await execute_upgrade(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_execute_upgrade_no_plan():
    run = {**FAKE_RUN, "results": {"stack_profile": {}}}
    with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = run
        with pytest.raises(ValueError, match="No renovation plan"):
            await execute_upgrade(USER_ID, RUN_ID)


@pytest.mark.asyncio
async def test_execute_upgrade_duplicate_blocked():
    rid = str(RUN_ID)
    _active_upgrades[rid] = {"status": "running"}
    try:
        with patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = FAKE_RUN
            with pytest.raises(ValueError, match="already in progress"):
                await execute_upgrade(USER_ID, RUN_ID)
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# execute_upgrade — success path
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_upgrade_starts_background_task():
    """Successful call returns immediately with session info and spawns a task."""
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get,
        patch("app.services.upgrade_executor.asyncio") as mock_asyncio,
    ):
        mock_get.return_value = FAKE_RUN
        mock_asyncio.create_task = MagicMock()

        result = await execute_upgrade(USER_ID, RUN_ID, api_key="sk-test-1")

        assert result["status"] == "running"
        assert result["total_tasks"] == 1
        assert result["repo_name"] == "test/repo"
        assert result["run_id"] == str(RUN_ID)
        assert result["narrator_enabled"] is False
        assert result["workers"] == ["sonnet", "opus"]

        # Background task should have been spawned
        mock_asyncio.create_task.assert_called_once()

        # State should exist
        state = get_upgrade_status(str(RUN_ID))
        assert state is not None
        assert state["status"] == "running"
        assert state["total_tasks"] == 1
        assert "tokens" in state
        assert state["narrator_enabled"] is False

    # Cleanup
    _active_upgrades.pop(str(RUN_ID), None)


@pytest.mark.asyncio
async def test_execute_upgrade_parses_json_results():
    """When results is a JSON string, it should be parsed."""
    import json

    run = {**FAKE_RUN, "results": json.dumps(FAKE_RUN["results"])}
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get,
        patch("app.services.upgrade_executor.asyncio") as mock_asyncio,
    ):
        mock_get.return_value = run
        mock_asyncio.create_task = MagicMock()

        result = await execute_upgrade(USER_ID, RUN_ID)
        assert result["total_tasks"] == 1

    _active_upgrades.pop(str(RUN_ID), None)


# -----------------------------------------------------------------------
# _strip_codeblock
# -----------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    # Plain JSON — unchanged
    ('{"key": "val"}', '{"key": "val"}'),
    # Wrapped in ```json ... ```
    ('```json\n{"key": "val"}\n```', '{"key": "val"}'),
    # Wrapped in ``` ... ```  (no language tag)
    ('```\n{"key": "val"}\n```', '{"key": "val"}'),
    # Leading whitespace + triple-fence
    ('  ```json\n{"a":1}\n```  ', '{"a":1}'),
    # Only opening fence
    ('```json\n{"a":1}', '{"a":1}'),
    # Only closing fence
    ('{"a":1}\n```', '{"a":1}'),
    # Extra blank lines inside fences
    ('```json\n\n{"a":1}\n\n```', '{"a":1}'),
])
def test_strip_codeblock(raw: str, expected: str):
    assert _strip_codeblock(raw) == expected


# -----------------------------------------------------------------------
# Narrator-enabled execution
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_upgrade_narrator_enabled():
    """When two API keys are supplied, narrator_enabled should be True."""
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get,
        patch("app.services.upgrade_executor.asyncio") as mock_asyncio,
    ):
        mock_get.return_value = FAKE_RUN
        mock_asyncio.create_task = MagicMock()

        result = await execute_upgrade(
            USER_ID, RUN_ID, api_key="sk-key-1", api_key_2="sk-key-2",
        )

        assert result["narrator_enabled"] is True
        assert result["workers"] == ["sonnet", "opus", "opus-2"]
        assert result["status"] == "running"

        state = get_upgrade_status(str(RUN_ID))
        assert state is not None
        assert state["narrator_enabled"] is True

    _active_upgrades.pop(str(RUN_ID), None)


@pytest.mark.asyncio
async def test_execute_upgrade_single_key_no_narrator():
    """With only api_key_2 empty, narrator is disabled."""
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get,
        patch("app.services.upgrade_executor.asyncio") as mock_asyncio,
    ):
        mock_get.return_value = FAKE_RUN
        mock_asyncio.create_task = MagicMock()

        result = await execute_upgrade(USER_ID, RUN_ID, api_key="sk-primary")

        assert result["narrator_enabled"] is False
        assert result["workers"] == ["sonnet", "opus"]

    _active_upgrades.pop(str(RUN_ID), None)


@pytest.mark.asyncio
async def test_execute_upgrade_no_key_raises():
    """When no BYOK key and no server env key, should raise ValueError."""
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_get,
        patch("app.services.upgrade_executor.settings") as mock_settings,
    ):
        mock_get.return_value = FAKE_RUN
        mock_settings.ANTHROPIC_API_KEY = ""
        mock_settings.LLM_BUILDER_MODEL = "claude-opus-4-6"
        mock_settings.LLM_PLANNER_MODEL = "claude-sonnet-4-5"
        mock_settings.LLM_NARRATOR_MODEL = "claude-haiku-4-5"

        with pytest.raises(ValueError, match="No Anthropic API key"):
            await execute_upgrade(USER_ID, RUN_ID, api_key="", api_key_2="")

    _active_upgrades.pop(str(RUN_ID), None)


# -----------------------------------------------------------------------
# _TokenAccumulator
# -----------------------------------------------------------------------


def test_token_accumulator_add_and_snapshot():
    t = _TokenAccumulator()
    assert t.total == 0

    t.add("opus", 100, 50)
    assert t.opus_in == 100
    assert t.opus_out == 50
    assert t.sonnet_in == 0
    assert t.haiku_in == 0

    t.add("sonnet", 300, 120)
    assert t.sonnet_in == 300
    assert t.sonnet_out == 120

    t.add("haiku", 200, 80)
    assert t.haiku_in == 200
    assert t.haiku_out == 80

    snap = t.snapshot()
    assert snap["opus"]["input"] == 100
    assert snap["opus"]["output"] == 50
    assert snap["opus"]["total"] == 150
    assert snap["sonnet"]["total"] == 420
    assert snap["haiku"]["total"] == 280
    assert snap["total"] == 850


def test_token_accumulator_multiple_adds():
    t = _TokenAccumulator()
    t.add("opus", 500, 200)
    t.add("opus", 300, 100)
    t.add("sonnet", 150, 50)
    assert t.opus_in == 800
    assert t.opus_out == 300
    assert t.sonnet_in == 150
    assert t.snapshot()["opus"]["total"] == 1100
    assert t.snapshot()["sonnet"]["total"] == 200


# -----------------------------------------------------------------------
# _fmt_tokens
# -----------------------------------------------------------------------


@pytest.mark.parametrize("n,expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0k"),
    (1500, "1.5k"),
    (142300, "142.3k"),
    (1000000, "1.0M"),
    (2500000, "2.5M"),
])
def test_fmt_tokens(n: int, expected: str):
    assert _fmt_tokens(n) == expected


# -----------------------------------------------------------------------
# get_available_commands
# -----------------------------------------------------------------------


def test_get_available_commands():
    cmds = get_available_commands()
    assert "/pause" in cmds
    assert "/resume" in cmds
    assert "/stop" in cmds
    assert "/help" in cmds
    assert "/status" in cmds
    assert "/clear" in cmds
    assert "/push" in cmds
    assert "/start" in cmds
    # Each value is a description string
    assert isinstance(cmds["/pause"], str)


# -----------------------------------------------------------------------
# send_command
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_command_help():
    """'/help' should work even without an active session."""
    with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
        result = await send_command("u1", "r1", "/help")
        assert result["ok"] is True
        assert "Available commands" in result["message"]


@pytest.mark.asyncio
async def test_send_command_clear():
    rid = "cmd-clear-test"
    _active_upgrades[rid] = {"status": "running", "logs": [{"msg": "old"}]}
    try:
        with patch("app.services.upgrade_executor._emit", new_callable=AsyncMock) as mock_emit:
            result = await send_command("u1", rid, "/clear")
            assert result["ok"] is True
            assert _active_upgrades[rid]["logs"] == []
            # Should emit upgrade_clear_logs event
            mock_emit.assert_awaited_once()
            assert mock_emit.call_args[0][1] == "upgrade_clear_logs"
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_no_session():
    """Commands that need a session should fail gracefully."""
    result = await send_command("u1", "nonexistent", "/pause")
    assert result["ok"] is False
    assert "No active" in result["message"]


@pytest.mark.asyncio
async def test_send_command_pause_resume():
    import asyncio

    rid = "cmd-pr-test"
    pause_ev = asyncio.Event()
    pause_ev.set()
    stop_flag = asyncio.Event()
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "completed_tasks": 2,
        "total_tasks": 5,
        "tokens": {"total": 100},
        "_pause_event": pause_ev,
        "_stop_flag": stop_flag,
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        ):
            # Pause
            r = await send_command("u1", rid, "/pause")
            assert r["ok"] is True
            assert _active_upgrades[rid]["status"] == "paused"
            assert not pause_ev.is_set()

            # Double-pause should fail
            r2 = await send_command("u1", rid, "/pause")
            assert r2["ok"] is False
            assert "Cannot pause" in r2["message"]

            # Resume
            r3 = await send_command("u1", rid, "/resume")
            assert r3["ok"] is True
            assert _active_upgrades[rid]["status"] == "running"
            assert pause_ev.is_set()

            # Resume when running should fail
            r4 = await send_command("u1", rid, "/resume")
            assert r4["ok"] is False
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_stop():
    import asyncio

    rid = "cmd-stop-test"
    pause_ev = asyncio.Event()
    pause_ev.set()
    stop_flag = asyncio.Event()
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "_pause_event": pause_ev,
        "_stop_flag": stop_flag,
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        ):
            r = await send_command("u1", rid, "/stop")
            assert r["ok"] is True
            assert stop_flag.is_set()
            assert _active_upgrades[rid]["status"] == "stopping"
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_status():
    import asyncio

    rid = "cmd-status-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "completed_tasks": 3,
        "total_tasks": 7,
        "tokens": {"total": 42000},
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
            r = await send_command("u1", rid, "/status")
            assert r["ok"] is True
            assert "3/7" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_unknown():
    import asyncio

    rid = "cmd-unk-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
            r = await send_command("u1", rid, "/foobar")
            assert r["ok"] is False
            assert "Unknown command" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_push_no_working_dir():
    """Push with changes but no working_dir should explain the issue."""
    import asyncio

    rid = "cmd-push-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "repo_name": "test/repo",
        "task_results": [
            {"llm_result": {"changes": [{"file": "a.py", "action": "modify"}]}},
        ],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        ):
            # /push now shows confirmation prompt
            r = await send_command("u1", rid, "/push")
            assert r["ok"] is True
            assert "Y/N" in r["message"]
            assert _active_upgrades[rid].get("pending_prompt") == "push_test_confirm"
            # Answer N (skip tests, push directly) → triggers _push_changes
            r = await send_command("u1", rid, "n")
            assert r["ok"] is False
            assert "applied" in r["message"].lower() or "No" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_push_with_working_dir(tmp_path):
    """Push with a real working_dir: /push → N (skip tests) → applies and pushes."""
    import asyncio

    rid = "cmd-push-wd-test"
    # Create a real file in the temp dir
    (tmp_path / "a.py").write_text("x = 1\n")

    _active_upgrades[rid] = {
        "status": "completed",
        "logs": [],
        "repo_name": "test/repo",
        "working_dir": str(tmp_path),
        "access_token": "ghp_test",
        "branch": "main",
        "task_results": [
            {"status": "proposed", "task_name": "Upgrade A", "changes_count": 1,
             "llm_result": {"changes": [
                {"file": "a.py", "action": "modify",
                 "before_snippet": "x = 1\n",
                 "after_snippet": "x = 2\n"}
             ]}},
        ],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
            patch("app.services.upgrade_executor.git_client") as mock_git,
        ):
            mock_git.add_all = AsyncMock()
            mock_git.commit = AsyncMock(return_value="abc12345")
            mock_git._run_git = AsyncMock(return_value="main")
            mock_git.set_remote = AsyncMock()
            mock_git.pull_rebase = AsyncMock()
            mock_git.push = AsyncMock()

            # Step 1: /push shows confirmation prompt
            r = await send_command("u1", rid, "/push")
            assert r["ok"] is True
            assert _active_upgrades[rid].get("pending_prompt") == "push_test_confirm"

            # Step 2: Answer N (skip tests, push directly)
            r = await send_command("u1", rid, "n")
            assert r["ok"] is True
            assert "Pushed" in r["message"]

            # File should be modified on disk
            assert (tmp_path / "a.py").read_text() == "x = 2\n"

            # Git operations called
            mock_git.add_all.assert_awaited_once()
            mock_git.commit.assert_awaited_once()
            mock_git.push.assert_awaited_once()
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_send_command_push_no_changes():
    import asyncio

    rid = "cmd-push-empty-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "task_results": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
            r = await send_command("u1", rid, "/push")
            assert r["ok"] is True
            assert "No changes" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# set_narrator_watching
# -----------------------------------------------------------------------


def test_set_narrator_watching_no_state():
    assert set_narrator_watching("nonexistent", True) is False


def test_set_narrator_watching_toggle():
    rid = "narrator-test"
    _active_upgrades[rid] = {"status": "running", "narrator_watching": False}
    try:
        assert set_narrator_watching(rid, True) is True
        assert _active_upgrades[rid]["narrator_watching"] is True
        assert set_narrator_watching(rid, False) is True
        assert _active_upgrades[rid]["narrator_watching"] is False
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# _detect_test_command
# -----------------------------------------------------------------------

def test_detect_test_command_python_pytest(tmp_path):
    """Repos with a tests/ dir should get pytest."""
    (tmp_path / "tests").mkdir()
    from app.services.upgrade_executor import _detect_test_command
    label, cmd = _detect_test_command(str(tmp_path))
    assert "pytest" in label.lower()
    assert cmd[0] == "python"

def test_detect_test_command_node(tmp_path):
    """Repos with package.json + test script should get npm test."""
    import json as _json
    (tmp_path / "package.json").write_text(_json.dumps({
        "scripts": {"test": "vitest run"}
    }))
    from app.services.upgrade_executor import _detect_test_command
    label, cmd = _detect_test_command(str(tmp_path))
    assert "npm" in label.lower()
    assert cmd == ["npm", "test"]

def test_detect_test_command_empty(tmp_path):
    """Empty repos with no recognisable config should return empty."""
    from app.services.upgrade_executor import _detect_test_command
    label, cmd = _detect_test_command(str(tmp_path))
    assert cmd == []


# -----------------------------------------------------------------------
# Push prompt flow — Y path with test runner
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_prompt_y_runs_tests(tmp_path):
    """Answering Y to push prompt should apply changes and run tests."""
    import asyncio

    rid = "cmd-push-y-test"
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "tests").mkdir()  # so _detect_test_command picks pytest

    _active_upgrades[rid] = {
        "status": "completed",
        "logs": [],
        "repo_name": "test/repo",
        "working_dir": str(tmp_path),
        "access_token": "ghp_test",
        "branch": "main",
        "task_results": [
            {"status": "proposed", "task_name": "Upgrade A", "changes_count": 1,
             "llm_result": {"changes": [
                {"file": "a.py", "action": "modify",
                 "before_snippet": "x = 1\n",
                 "after_snippet": "x = 2\n"}
             ]}},
        ],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
            patch("app.services.upgrade_executor.git_client") as mock_git,
            patch("subprocess.run") as mock_sub_run,
        ):
            # Mock test run — pass
            mock_sub_run.return_value = MagicMock(
                stdout="1 passed\n", stderr="", returncode=0,
            )

            mock_git.add_all = AsyncMock()
            mock_git.commit = AsyncMock(return_value="abc12345")
            mock_git._run_git = AsyncMock(return_value="main")
            mock_git.set_remote = AsyncMock()
            mock_git.pull_rebase = AsyncMock()
            mock_git.push = AsyncMock()

            # Step 1: /push
            r = await send_command("u1", rid, "/push")
            assert _active_upgrades[rid].get("pending_prompt") == "push_test_confirm"

            # Step 2: Y
            r = await send_command("u1", rid, "y")
            assert r["ok"] is True
            assert "Pushed" in r["message"]

            # File modified + git called
            assert (tmp_path / "a.py").read_text() == "x = 2\n"
            mock_git.push.assert_awaited_once()
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# _apply_file_change
# -----------------------------------------------------------------------


def test_apply_file_change_create(tmp_path):
    _apply_file_change(str(tmp_path), {
        "file": "new_dir/hello.py",
        "action": "create",
        "after_snippet": "print('hello')",
    })
    assert (tmp_path / "new_dir" / "hello.py").read_text() == "print('hello')"


def test_apply_file_change_modify(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ny = 2\nz = 3\n")
    _apply_file_change(str(tmp_path), {
        "file": "app.py",
        "action": "modify",
        "before_snippet": "y = 2",
        "after_snippet": "y = 99",
    })
    assert "y = 99" in (tmp_path / "app.py").read_text()
    assert "y = 2" not in (tmp_path / "app.py").read_text()


def test_apply_file_change_delete(tmp_path):
    (tmp_path / "old.txt").write_text("gone")
    _apply_file_change(str(tmp_path), {
        "file": "old.txt",
        "action": "delete",
    })
    assert not (tmp_path / "old.txt").exists()


def test_apply_file_change_modify_missing_falls_back_to_create(tmp_path):
    """When modify targets a missing file but after_snippet is present, create it."""
    _apply_file_change(str(tmp_path), {
        "file": "nope.py",
        "action": "modify",
        "before_snippet": "x",
        "after_snippet": "y",
    })
    assert (tmp_path / "nope.py").read_text() == "y"


def test_apply_file_change_modify_missing_no_after_raises(tmp_path):
    """When modify targets a missing file and no after_snippet, raise."""
    with pytest.raises(FileNotFoundError):
        _apply_file_change(str(tmp_path), {
            "file": "nope.py",
            "action": "modify",
            "before_snippet": "x",
            "after_snippet": "",
        })


# -----------------------------------------------------------------------
# /start command
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_command_start():
    """'/start' returns ok (normally intercepted by frontend)."""
    import asyncio

    rid = "cmd-start-test"
    _active_upgrades[rid] = {
        "status": "ready",
        "logs": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
            r = await send_command("u1", rid, "/start")
            assert r["ok"] is True
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# prepare_upgrade_workspace
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_upgrade_workspace():
    """prepare_upgrade_workspace clones repo and sets ready state."""
    with (
        patch("app.services.upgrade_executor.get_scout_run", new_callable=AsyncMock) as mock_run,
        patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
        patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        patch("app.services.upgrade_executor.git_client") as mock_git,
        patch("app.services.upgrade_executor.tempfile") as mock_tmp,
    ):
        mock_run.return_value = {
            "id": RUN_ID,
            "user_id": USER_ID,
            "repo_name": "test/repo",
        }
        mock_tmp.mkdtemp.return_value = "/tmp/forgeguard_upgrade_xyz"
        mock_git.clone_repo = AsyncMock()
        mock_git.get_file_list = AsyncMock(return_value=["a.py", "b.py"])
        mock_git._run_git = AsyncMock(return_value="main")

        result = await prepare_upgrade_workspace(
            USER_ID, RUN_ID, access_token="ghp_test",
        )

        assert result["status"] == "ready"
        assert result["file_count"] == 2
        assert result["clone_ok"] is True

        rid = str(RUN_ID)
        assert _active_upgrades[rid]["status"] == "ready"
        assert _active_upgrades[rid]["working_dir"] is not None
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# _plan_task_with_llm / _build_task_with_llm
# -----------------------------------------------------------------------

FAKE_TASK = {
    "id": "MIG-1",
    "from_state": "React 17",
    "to_state": "React 18",
    "category": "framework",
    "rationale": "EOL",
    "steps": ["Update package.json"],
    "effort": "medium",
    "risk": "low",
}


@pytest.mark.asyncio
async def test_plan_task_with_llm_returns_plan():
    """Sonnet planner should return a parsed plan dict."""
    plan_json = '{"analysis":"upgrade React","plan":[{"file":"package.json","action":"modify","description":"bump version","key_considerations":"none"}],"risks":[],"verification_strategy":["npm test"],"implementation_notes":"straightforward"}'
    with patch("app.services.upgrade_executor.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = {
            "text": plan_json,
            "usage": {"input_tokens": 500, "output_tokens": 200},
        }
        plan, usage = await _plan_task_with_llm(
            "u1", "r1", "test/repo", {"primary_language": "TypeScript"},
            FAKE_TASK, api_key="sk-test", model="claude-sonnet-4-5",
        )
        assert plan is not None
        assert plan["analysis"] == "upgrade React"
        assert len(plan["plan"]) == 1
        assert usage["input_tokens"] == 500


@pytest.mark.asyncio
async def test_plan_task_no_key_returns_none():
    plan, usage = await _plan_task_with_llm(
        "u1", "r1", "test/repo", {}, FAKE_TASK, api_key="", model="m",
    )
    assert plan is None
    assert usage["input_tokens"] == 0


@pytest.mark.asyncio
async def test_build_task_with_llm_uses_plan_context():
    """Opus builder should receive Sonnet's plan in its input."""
    code_json = '{"thinking":["step1"],"changes":[{"file":"pkg.json","action":"modify","description":"bump","before_snippet":"17","after_snippet":"18"}],"warnings":[],"verification_steps":["test"],"status":"proposed"}'
    fake_plan = {"analysis": "upgrade React", "plan": []}

    with patch("app.services.upgrade_executor.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = {
            "text": code_json,
            "usage": {"input_tokens": 800, "output_tokens": 400},
        }
        result, usage = await _build_task_with_llm(
            "u1", "r1", "test/repo", {}, FAKE_TASK, fake_plan,
            api_key="sk-test", model="claude-opus-4-6",
        )
        assert result is not None
        assert result["status"] == "proposed"
        assert len(result["changes"]) == 1
        assert usage["input_tokens"] == 800

        # Verify the plan context was included in the call
        call_args = mock_chat.call_args
        msg_content = call_args[1]["messages"][0]["content"]
        assert "planner_analysis" in msg_content


@pytest.mark.asyncio
async def test_build_task_no_key_returns_none():
    result, usage = await _build_task_with_llm(
        "u1", "r1", "test/repo", {}, FAKE_TASK, None,
        api_key="", model="m",
    )
    assert result is None


# -----------------------------------------------------------------------
# _gather_file_contents
# -----------------------------------------------------------------------


def test_gather_file_contents_reads_plan_files(tmp_path):
    """Should read files referenced in Sonnet's plan from the workspace."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("console.log('hello');", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"test"}', encoding="utf-8")

    plan = {
        "analysis": "upgrade deps",
        "plan": [
            {"file": "src/app.ts", "action": "modify", "description": "update"},
            {"file": "package.json", "action": "modify", "description": "bump"},
        ],
    }
    result = _gather_file_contents(str(tmp_path), plan)
    assert "src/app.ts" in result
    assert result["src/app.ts"] == "console.log('hello');"
    assert "package.json" in result
    assert result["package.json"] == '{"name":"test"}'


def test_gather_file_contents_skips_missing_files(tmp_path):
    """Files referenced in plan but not on disk should be skipped."""
    plan = {
        "plan": [
            {"file": "nonexistent.py", "action": "modify", "description": "x"},
        ],
    }
    result = _gather_file_contents(str(tmp_path), plan)
    assert result == {}


def test_gather_file_contents_skips_create_new_files(tmp_path):
    """'create' actions for files that don't exist yet should be skipped."""
    plan = {
        "plan": [
            {"file": "brand_new.py", "action": "create", "description": "new file"},
        ],
    }
    result = _gather_file_contents(str(tmp_path), plan)
    assert result == {}


def test_gather_file_contents_none_plan():
    """None plan should return empty dict."""
    assert _gather_file_contents("/some/path", None) == {}
    assert _gather_file_contents("", {"plan": []}) == {}


def test_gather_file_contents_budget_limit(tmp_path):
    """Should respect the total content budget."""
    # Create a file that's larger than budget
    big = "x" * 300_000
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")

    plan = {"plan": [{"file": "big.txt", "action": "modify", "description": "x"}]}
    result = _gather_file_contents(str(tmp_path), plan)
    # Should be truncated (contains the marker)
    assert "big.txt" in result
    assert "[FILE TOO LARGE" in result["big.txt"]


@pytest.mark.asyncio
async def test_build_task_with_llm_injects_workspace_files(tmp_path):
    """Opus builder should receive workspace_files when working_dir is given."""
    (tmp_path / "index.js").write_text("const x = 1;", encoding="utf-8")

    code_json = '{"thinking":["step1"],"changes":[],"warnings":[],"verification_steps":[],"status":"proposed"}'
    fake_plan = {
        "analysis": "upgrade",
        "plan": [{"file": "index.js", "action": "modify", "description": "x"}],
    }

    with patch("app.services.upgrade_executor.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = {
            "content": [{"type": "text", "text": code_json}],
            "usage": {"input_tokens": 900, "output_tokens": 300},
            "stop_reason": "end_turn",
        }
        result, usage = await _build_task_with_llm(
            "u1", "r1", "test/repo", {}, FAKE_TASK, fake_plan,
            api_key="sk-test", model="claude-opus-4-6",
            working_dir=str(tmp_path),
        )
        assert result is not None

        # Verify workspace_files was sent to the LLM
        call_args = mock_chat.call_args
        msg_content = call_args[1]["messages"][0]["content"]
        assert "workspace_files" in msg_content
        assert "const x = 1;" in msg_content


# -----------------------------------------------------------------------
# /retry command
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_no_failed_tasks():
    """'/retry' when there are no skipped tasks returns nothing-to-retry."""
    import asyncio

    rid = "retry-noop-test"
    _active_upgrades[rid] = {
        "status": "completed",
        "logs": [],
        "task_results": [
            {"task_id": "MIG-1", "status": "proposed", "changes_count": 2},
        ],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        ):
            r = await send_command("u1", rid, "/retry")
            assert r["ok"] is True
            assert "Nothing to retry" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_retry_requires_finished_state():
    """'/retry' while still running should be rejected."""
    import asyncio

    rid = "retry-running-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "task_results": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
        ):
            r = await send_command("u1", rid, "/retry")
            assert r["ok"] is False
            assert "Wait for the run" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_retry_launches_run_for_skipped_tasks():
    """'/retry' with skipped tasks should launch _run_retry."""
    import asyncio

    rid = "retry-launch-test"
    sonnet_w = _WorkerSlot(label="sonnet", api_key="sk-test",
                           model="claude-sonnet-4-5", display="Sonnet")
    opus_w = _WorkerSlot(label="opus", api_key="sk-test",
                         model="claude-opus-4-6", display="Opus")
    _active_upgrades[rid] = {
        "status": "completed",
        "logs": [],
        "task_results": [
            {"task_id": "MIG-1", "status": "proposed", "changes_count": 2},
            {
                "task_id": "MIG-2", "status": "skipped", "changes_count": 0,
                "_retry_task": {
                    "id": "MIG-2", "from_state": "v1", "to_state": "v2",
                    "priority": "high", "category": "deps",
                    "rationale": "EOL", "steps": ["update"],
                },
                "_retry_plan": None,
            },
        ],
        "_sonnet_worker": sonnet_w,
        "_opus_worker": opus_w,
        "_stack_profile": {},
        "_narrator_key": "",
        "_narrator_model": "",
        "repo_name": "test/repo",
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._run_retry", new_callable=AsyncMock) as mock_retry,
        ):
            r = await send_command("u1", rid, "/retry")
            assert r["ok"] is True
            assert "Retrying 1 task(s)" in r["message"]
            assert _active_upgrades[rid]["status"] == "running"
            # _run_retry should have been scheduled (we patched it)
            # Allow the created task to execute
            await asyncio.sleep(0.05)
            mock_retry.assert_awaited_once()
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_run_retry_replaces_skipped_entry():
    """_run_retry should replace skipped entries in-place with new results."""
    rid = "retry-replace-test"
    tok = _TokenAccumulator()
    task = {
        "id": "MIG-3", "from_state": "old", "to_state": "new",
        "priority": "medium", "category": "framework",
        "rationale": "upgrade", "steps": ["migrate"],
    }
    skipped_entry = {
        "task_id": "MIG-3", "task_name": "old → new",
        "status": "skipped", "changes_count": 0,
        "_retry_task": task, "_retry_plan": None,
    }
    state = {
        "status": "running",
        "logs": [],
        "task_results": [{"status": "proposed"}, skipped_entry],
        "tokens": {},
        "_token_tracker": tok,
    }
    _active_upgrades[rid] = state

    sonnet_w = _WorkerSlot(label="sonnet", api_key="sk-test",
                           model="claude-sonnet-4-5", display="Sonnet")
    opus_w = _WorkerSlot(label="opus", api_key="sk-test",
                         model="claude-opus-4-6", display="Opus")

    plan_result = {
        "analysis": "Need to upgrade",
        "plan": [{"file": "index.js", "action": "modify", "description": "update"}],
        "risks": [],
    }
    code_result = {
        "thinking": ["Updating index.js"],
        "changes": [{"file": "index.js", "action": "modify",
                     "description": "updated import", "content": "new code"}],
        "warnings": [],
        "verification_steps": ["Run tests"],
        "status": "proposed",
    }

    failed_entries = [(1, skipped_entry)]

    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._plan_task_with_llm",
                  new_callable=AsyncMock,
                  return_value=(plan_result, {"input_tokens": 100, "output_tokens": 50})),
            patch("app.services.upgrade_executor._build_task_with_llm",
                  new_callable=AsyncMock,
                  return_value=(code_result, {"input_tokens": 200, "output_tokens": 100})),
        ):
            await _run_retry(
                "u1", rid, state, failed_entries,
                sonnet_w, opus_w, "test/repo", {},
            )

        # The skipped entry should now be proposed
        assert state["task_results"][1]["status"] == "proposed"
        assert state["task_results"][1]["changes_count"] == 1
        assert state["task_results"][1]["task_id"] == "MIG-3"
        assert state["status"] == "completed"
        # First entry should be untouched
        assert state["task_results"][0]["status"] == "proposed"
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_run_retry_still_failed_keeps_retry_data():
    """When Opus returns None on retry, entry stays skipped with retry data."""
    rid = "retry-stillfail-test"
    tok = _TokenAccumulator()
    task = {
        "id": "MIG-4", "from_state": "a", "to_state": "b",
        "priority": "low", "category": "lib",
        "rationale": "update", "steps": ["bump"],
    }
    skipped_entry = {
        "task_id": "MIG-4", "task_name": "a → b",
        "status": "skipped", "changes_count": 0,
        "_retry_task": task, "_retry_plan": None,
    }
    state = {
        "status": "running",
        "logs": [],
        "task_results": [skipped_entry],
        "tokens": {},
        "_token_tracker": tok,
    }
    _active_upgrades[rid] = state

    sonnet_w = _WorkerSlot(label="sonnet", api_key="sk-test",
                           model="claude-sonnet-4-5", display="Sonnet")
    opus_w = _WorkerSlot(label="opus", api_key="sk-test",
                         model="claude-opus-4-6", display="Opus")

    failed_entries = [(0, skipped_entry)]

    try:
        with (
            patch("app.services.upgrade_executor._log", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit", new_callable=AsyncMock),
            patch("app.services.upgrade_executor._plan_task_with_llm",
                  new_callable=AsyncMock,
                  return_value=(None, {"input_tokens": 50, "output_tokens": 10})),
            patch("app.services.upgrade_executor._build_task_with_llm",
                  new_callable=AsyncMock,
                  return_value=(None, {"input_tokens": 60, "output_tokens": 15})),
        ):
            await _run_retry(
                "u1", rid, state, failed_entries,
                sonnet_w, opus_w, "test/repo", {},
            )

        # Should still be skipped but with retry data preserved
        assert state["task_results"][0]["status"] == "skipped"
        assert state["task_results"][0]["_retry_task"] == task
        assert state["status"] == "completed"
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# Inline per-file deterministic audit
# -----------------------------------------------------------------------


class TestAuditFileChange:
    """Tests for _audit_file_change deterministic checks."""

    def test_pass_clean_python(self):
        change = {
            "file": "app/main.py",
            "action": "modify",
            "after_snippet": "import os\n\ndef hello():\n    return 'world'\n",
        }
        verdict, findings = _audit_file_change(change)
        assert verdict == "PASS"
        assert findings == []

    def test_fail_syntax_error(self):
        change = {
            "file": "app/broken.py",
            "action": "create",
            "after_snippet": "def foo(\n    pass\n",
        }
        verdict, findings = _audit_file_change(change)
        assert verdict == "FAIL"
        assert any("Syntax error" in f for f in findings)

    def test_fail_invalid_json(self):
        change = {
            "file": "config.json",
            "action": "create",
            "after_snippet": '{"key": }',
        }
        verdict, findings = _audit_file_change(change)
        assert verdict == "FAIL"
        assert any("Invalid JSON" in f for f in findings)

    def test_pass_valid_json(self):
        change = {
            "file": "config.json",
            "action": "create",
            "after_snippet": '{"key": "value"}',
        }
        verdict, findings = _audit_file_change(change)
        assert verdict == "PASS"

    def test_fail_import_star(self):
        change = {
            "file": "app/utils.py",
            "action": "modify",
            "after_snippet": "from os import *\n\ndef fn():\n    pass\n",
        }
        verdict, findings = _audit_file_change(change)
        assert verdict == "FAIL"
        assert any("Wildcard import" in f for f in findings)

    def test_pass_delete_action(self):
        change = {"file": "old.py", "action": "delete", "after_snippet": ""}
        verdict, findings = _audit_file_change(change)
        assert verdict == "PASS"
        assert findings == []

    def test_pass_in_scope(self):
        change = {
            "file": "app/main.py",
            "action": "modify",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["app/main.py", "tests/test_main.py"])
        assert verdict == "PASS"

    def test_reject_out_of_scope(self):
        change = {
            "file": "app/unplanned.py",
            "action": "create",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["app/main.py"])
        assert verdict == "REJECT"
        assert any("Scope deviation" in f for f in findings)

    def test_no_scope_check_when_none(self):
        """When planned_files is None, scope check is skipped."""
        change = {
            "file": "anywhere.py",
            "action": "create",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(change, planned_files=None)
        assert verdict == "PASS"


# -----------------------------------------------------------------------
# Evidence file writers
# -----------------------------------------------------------------------


class TestWriteDiffLog:
    """Tests for _write_diff_log evidence writer."""

    def test_writes_valid_markdown(self, tmp_path):
        changes = [
            {"file": "app/main.py", "action": "modify",
             "description": "Updated handler",
             "before_snippet": "old", "after_snippet": "new"},
            {"file": "app/new.py", "action": "create",
             "description": "New file", "after_snippet": "content"},
        ]
        path = _write_diff_log(str(tmp_path), changes, "test/repo")
        content = Path(path).read_text(encoding="utf-8")
        assert "# Diff Log" in content
        assert "test/repo" in content
        assert "2 file(s)" in content
        assert "app/main.py" in content
        assert "app/new.py" in content

    def test_creates_evidence_directory(self, tmp_path):
        path = _write_diff_log(str(tmp_path), [], "test/repo")
        assert Path(path).exists()
        assert (tmp_path / "Forge" / "evidence" / "diff_log.md").exists()


class TestWriteAuditTrail:
    """Tests for _write_audit_trail evidence writer."""

    def test_writes_valid_markdown(self, tmp_path):
        results = [
            {"file": "a.py", "action": "modify", "verdict": "PASS",
             "findings": []},
            {"file": "b.py", "action": "create", "verdict": "FAIL",
             "findings": ["Syntax error"]},
        ]
        path = _write_audit_trail(str(tmp_path), results, "test/repo")
        content = Path(path).read_text(encoding="utf-8")
        assert "# Audit Ledger" in content
        assert "1/2 files passed" in content
        assert "1 failed" in content
        assert "a.py" in content
        assert "b.py" in content
        assert "Syntax error" in content

    def test_all_pass(self, tmp_path):
        results = [
            {"file": "a.py", "action": "modify", "verdict": "PASS",
             "findings": []},
        ]
        path = _write_audit_trail(str(tmp_path), results, "test/repo")
        content = Path(path).read_text(encoding="utf-8")
        assert "1/1 files passed" in content
        assert "0 failed" in content


# -----------------------------------------------------------------------
# Pre-push audit gate
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_push_audit_all_pass(tmp_path):
    """When all audits pass, push proceeds to commit_and_push."""
    rid = "audit-pass-test"
    state = {
        "working_dir": str(tmp_path),
        "repo_name": "test/repo",
        "access_token": "ghp_test",
        "branch": "main",
        "logs": [],
        "audit_results": [
            {"file": "a.py", "action": "modify", "verdict": "PASS",
             "findings": []},
        ],
    }
    _active_upgrades[rid] = state
    changes = [{"file": "a.py", "action": "modify",
                "after_snippet": "x = 1"}]
    try:
        with (
            patch("app.services.upgrade_executor._log",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._commit_and_push",
                  new_callable=AsyncMock,
                  return_value={"ok": True, "message": "Pushed"}),
        ):
            r = await _run_pre_push_audit("u1", rid, state, changes, [])
            assert r["ok"] is True
            assert "Pushed" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_pre_push_audit_fail_prompts(tmp_path):
    """When audit has failures, shows Y/N prompt instead of pushing."""
    rid = "audit-fail-test"
    state = {
        "working_dir": str(tmp_path),
        "repo_name": "test/repo",
        "access_token": "ghp_test",
        "branch": "main",
        "logs": [],
        "audit_results": [
            {"file": "bad.py", "action": "create", "verdict": "FAIL",
             "findings": ["Syntax error"]},
        ],
    }
    _active_upgrades[rid] = state
    changes = [{"file": "bad.py", "action": "create",
                "after_snippet": "def("}]
    try:
        with (
            patch("app.services.upgrade_executor._log",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit",
                  new_callable=AsyncMock),
        ):
            r = await _run_pre_push_audit("u1", rid, state, changes, [])
            assert r["ok"] is True
            assert "Audit failed" in r["message"]
            assert state["pending_prompt"] == "push_audit_confirm"
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_push_audit_confirm_yes_pushes(tmp_path):
    """Answering Y to audit failure prompt pushes anyway."""
    import asyncio

    rid = "audit-confirm-y-test"
    state = {
        "working_dir": str(tmp_path),
        "repo_name": "test/repo",
        "access_token": "ghp_test",
        "branch": "main",
        "logs": [],
        "pending_prompt": "push_audit_confirm",
        "_push_changes": [{"file": "a.py", "action": "create",
                           "after_snippet": "x = 1"}],
        "_push_task_results": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    _active_upgrades[rid] = state
    try:
        with (
            patch("app.services.upgrade_executor._log",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._commit_and_push",
                  new_callable=AsyncMock,
                  return_value={"ok": True, "message": "Pushed"}),
        ):
            r = await send_command("u1", rid, "y")
            assert r["ok"] is True
            assert "Pushed" in r["message"]
    finally:
        _active_upgrades.pop(rid, None)


@pytest.mark.asyncio
async def test_push_audit_confirm_no_cancels():
    """Answering N to audit failure prompt cancels the push."""
    import asyncio

    rid = "audit-confirm-n-test"
    state = {
        "working_dir": "/tmp/fake",
        "repo_name": "test/repo",
        "logs": [],
        "pending_prompt": "push_audit_confirm",
        "_push_changes": [],
        "_push_task_results": [],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    _active_upgrades[rid] = state
    try:
        with (
            patch("app.services.upgrade_executor._log",
                  new_callable=AsyncMock),
            patch("app.services.upgrade_executor._emit",
                  new_callable=AsyncMock),
        ):
            r = await send_command("u1", rid, "n")
            assert r["ok"] is True
            assert "cancelled" in r["message"].lower()
    finally:
        _active_upgrades.pop(rid, None)


# -----------------------------------------------------------------------
# Plan Pool dataclass tests
# -----------------------------------------------------------------------


class TestPlanPoolItem:
    """Tests for _PlanPoolItem dataclass."""

    def test_create_item(self):
        task = {"id": "T-0", "from_state": "A", "to_state": "B"}
        plan = {"analysis": "test", "plan": []}
        usage = {"input_tokens": 100, "output_tokens": 50}
        item = _PlanPoolItem(
            task_index=0, task=task,
            plan_result=plan, plan_usage=usage)
        assert item.task_index == 0
        assert item.task is task
        assert item.plan_result == plan
        assert item.plan_usage == usage

    def test_none_plan(self):
        item = _PlanPoolItem(task_index=1, task={},
                             plan_result=None, plan_usage={})
        assert item.plan_result is None


class TestRemediationItem:
    """Tests for _RemediationItem dataclass."""

    def test_priority_ordering(self):
        high = _RemediationItem(
            file="a.py", findings=["err"], original_change={},
            task_id="T-0", priority=1, _seq=1)
        low = _RemediationItem(
            file="b.py", findings=["err"], original_change={},
            task_id="T-1", priority=10, _seq=2)
        assert high < low
        assert not low < high

    def test_seq_tiebreaker(self):
        a = _RemediationItem(
            file="a.py", findings=[], original_change={},
            task_id="T-0", priority=10, _seq=1)
        b = _RemediationItem(
            file="b.py", findings=[], original_change={},
            task_id="T-0", priority=10, _seq=2)
        assert a < b

    def test_default_priority(self):
        item = _RemediationItem(
            file="x.py", findings=[], original_change={},
            task_id="T-0")
        assert item.priority == 10


# -----------------------------------------------------------------------
# Plan pool queue integration
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_pool_ordering():
    """Plans pushed into asyncio.Queue come out in FIFO order."""
    import asyncio
    pool: asyncio.Queue[_PlanPoolItem | None] = asyncio.Queue()
    items = [
        _PlanPoolItem(i, {"id": f"T-{i}"}, None, {})
        for i in range(5)
    ]
    for item in items:
        await pool.put(item)
    await pool.put(None)  # sentinel

    retrieved = []
    while True:
        it = await pool.get()
        if it is None:
            break
        retrieved.append(it.task_index)

    assert retrieved == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_remediation_pool_priority_ordering():
    """Remediation pool returns items in priority order."""
    import asyncio
    pool: asyncio.PriorityQueue = asyncio.PriorityQueue()
    low = _RemediationItem(
        file="low.py", findings=[], original_change={},
        task_id="T-0", priority=10, _seq=1)
    high = _RemediationItem(
        file="high.py", findings=[], original_change={},
        task_id="T-1", priority=1, _seq=2)
    # Insert low-priority first
    await pool.put(low)
    await pool.put(high)

    first = await pool.get()
    second = await pool.get()
    assert first.file == "high.py"
    assert second.file == "low.py"


# -----------------------------------------------------------------------
# Deterministic remediation plan builder
# -----------------------------------------------------------------------


class TestBuildDeterministicFix:
    """Tests for _build_deterministic_fix."""

    def test_wildcard_import(self):
        fix = _build_deterministic_fix(
            "app/utils.py",
            ["Wildcard import — app/utils.py: from os import *"],
            {"after_snippet": "from os import *\n"},
        )
        assert fix is not None
        assert len(fix["plan"]) == 1
        assert fix["plan"][0]["action"] == "modify"
        assert "wildcard" in fix["plan"][0]["description"].lower()

    def test_scope_deviation(self):
        fix = _build_deterministic_fix(
            "app/unplanned.py",
            ["Scope deviation — app/unplanned.py: not in plan"],
            {"after_snippet": "x = 1\n"},
        )
        assert fix is not None
        assert fix["plan"][0]["action"] == "delete"

    def test_syntax_error(self):
        fix = _build_deterministic_fix(
            "app/bad.py",
            ["Syntax error — app/bad.py:1: invalid syntax"],
            {"after_snippet": "def(\n"},
        )
        assert fix is not None
        assert fix["plan"][0]["action"] == "modify"

    def test_unknown_finding_returns_none(self):
        fix = _build_deterministic_fix(
            "app/x.py",
            ["Secret detected — app/x.py: AWS key"],
            {"after_snippet": "AKIA...\n"},
        )
        assert fix is None


@pytest.mark.asyncio
async def test_generate_remediation_plan_deterministic():
    """_generate_remediation_plan uses deterministic path for known types."""
    tokens = _TokenAccumulator()
    result = await _generate_remediation_plan(
        "u1", "r1", "app/bad.py",
        ["Syntax error — app/bad.py:1: invalid syntax"],
        {"after_snippet": "def(\n"},
        api_key="fake", model="fake",
        tokens=tokens,
    )
    assert result is not None
    assert result["plan"][0]["action"] == "modify"
    # No LLM call — tokens should be untouched
    assert tokens.sonnet_in == 0
    assert tokens.sonnet_out == 0
