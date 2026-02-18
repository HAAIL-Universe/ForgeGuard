"""Tests for upgrade_executor service."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.upgrade_executor import (
    _active_upgrades,
    _log,
    _strip_codeblock,
    _TokenAccumulator,
    _fmt_tokens,
    execute_upgrade,
    get_available_commands,
    get_upgrade_status,
    send_command,
    set_narrator_watching,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")

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
        assert result["workers"] == ["opus"]

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
        assert result["workers"] == ["opus"]
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
        assert result["workers"] == ["opus"]

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
        mock_settings.LLM_NARRATOR_MODEL = "claude-3-5-haiku-20241022"

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
    assert t.haiku_in == 0

    t.add("haiku", 200, 80)
    assert t.haiku_in == 200
    assert t.haiku_out == 80

    snap = t.snapshot()
    assert snap["opus"]["input"] == 100
    assert snap["opus"]["output"] == 50
    assert snap["opus"]["total"] == 150
    assert snap["haiku"]["total"] == 280
    assert snap["total"] == 430


def test_token_accumulator_multiple_adds():
    t = _TokenAccumulator()
    t.add("opus", 500, 200)
    t.add("opus", 300, 100)
    assert t.opus_in == 800
    assert t.opus_out == 300
    assert t.snapshot()["opus"]["total"] == 1100


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
async def test_send_command_push_manifest():
    import asyncio

    rid = "cmd-push-test"
    _active_upgrades[rid] = {
        "status": "running",
        "logs": [],
        "task_results": [
            {"llm_result": {"changes": [{"file": "a.py", "action": "modify"}]}},
        ],
        "_pause_event": asyncio.Event(),
        "_stop_flag": asyncio.Event(),
    }
    try:
        with patch("app.services.upgrade_executor._log", new_callable=AsyncMock):
            r = await send_command("u1", rid, "/push")
            assert r["ok"] is True
            assert "1 changes catalogued" in r["message"]
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
