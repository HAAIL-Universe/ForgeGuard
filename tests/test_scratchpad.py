"""Unit tests for the forge_scratchpad tool handler.

Tests read/write/append/list operations, working_dir scoping,
disk persistence, and error handling — all via the internal
_exec_forge_scratchpad function.
"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_scratchpad(tmp_path: Path) -> tuple:
    """Return a clean (handler, working_dir) pair for each test."""
    from app.services.tool_executor import _exec_forge_scratchpad, _scratchpads
    wd = str(tmp_path)
    _scratchpads.pop(wd, None)  # ensure clean state
    return _exec_forge_scratchpad, wd


# ---------------------------------------------------------------------------
# Basic operations
# ---------------------------------------------------------------------------


def test_write_and_read(tmp_path):
    """Write a key then read it back."""
    fn, wd = _fresh_scratchpad(tmp_path)

    fn({"operation": "write", "key": "decision", "value": "Use JWT"}, wd)
    result = fn({"operation": "read", "key": "decision"}, wd)

    assert result == "Use JWT"


def test_append_accumulates(tmp_path):
    """Append operations accumulate without overwriting."""
    fn, wd = _fresh_scratchpad(tmp_path)

    fn({"operation": "write", "key": "notes", "value": "First"}, wd)
    fn({"operation": "append", "key": "notes", "value": " Second"}, wd)
    fn({"operation": "append", "key": "notes", "value": " Third"}, wd)

    result = fn({"operation": "read", "key": "notes"}, wd)
    assert result == "First Second Third"


def test_list_shows_all_keys(tmp_path):
    """List returns all written keys."""
    fn, wd = _fresh_scratchpad(tmp_path)

    fn({"operation": "write", "key": "alpha", "value": "a"}, wd)
    fn({"operation": "write", "key": "beta", "value": "b"}, wd)
    fn({"operation": "write", "key": "gamma", "value": "c"}, wd)

    result = fn({"operation": "list"}, wd)
    data = json.loads(result)
    assert set(data["keys"]) == {"alpha", "beta", "gamma"}
    assert data["count"] == 3


def test_list_empty(tmp_path):
    """List on empty scratchpad returns zero keys."""
    fn, wd = _fresh_scratchpad(tmp_path)

    result = fn({"operation": "list"}, wd)
    data = json.loads(result)
    assert data["keys"] == []
    assert data["count"] == 0


def test_read_missing_key_returns_error(tmp_path):
    """Reading a key that doesn't exist returns an Error string."""
    fn, wd = _fresh_scratchpad(tmp_path)

    result = fn({"operation": "read", "key": "missing_key"}, wd)
    assert "Error" in result
    assert "missing_key" in result


def test_write_overwrites_previous_value(tmp_path):
    """Write replaces the existing value for a key."""
    fn, wd = _fresh_scratchpad(tmp_path)

    fn({"operation": "write", "key": "status", "value": "planning"}, wd)
    fn({"operation": "write", "key": "status", "value": "in_progress"}, wd)

    result = fn({"operation": "read", "key": "status"}, wd)
    assert result == "in_progress"


def test_missing_key_param_for_read_returns_error(tmp_path):
    """Calling read without a key returns an Error."""
    fn, wd = _fresh_scratchpad(tmp_path)

    result = fn({"operation": "read"}, wd)
    assert "Error" in result
    assert "key" in result.lower()


def test_unknown_operation_returns_error(tmp_path):
    """Unknown operation returns an Error string."""
    fn, wd = _fresh_scratchpad(tmp_path)

    result = fn({"operation": "delete", "key": "x"}, wd)
    assert "Error" in result


# ---------------------------------------------------------------------------
# Working-dir scoping
# ---------------------------------------------------------------------------


def test_different_working_dirs_are_isolated(tmp_path):
    """Scratchpads for different working directories don't share state."""
    from app.services.tool_executor import _exec_forge_scratchpad, _scratchpads

    wd_a = str(tmp_path / "build_a")
    wd_b = str(tmp_path / "build_b")
    Path(wd_a).mkdir()
    Path(wd_b).mkdir()
    _scratchpads.pop(wd_a, None)
    _scratchpads.pop(wd_b, None)

    _exec_forge_scratchpad({"operation": "write", "key": "secret", "value": "A"}, wd_a)
    result_b = _exec_forge_scratchpad({"operation": "read", "key": "secret"}, wd_b)

    assert "Error" in result_b  # key doesn't exist in wd_b


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------


def test_persist_to_disk(tmp_path):
    """After a write, the scratchpad JSON file is created on disk."""
    from app.services.tool_executor import _exec_forge_scratchpad, _scratchpads

    wd = str(tmp_path)
    _scratchpads.pop(wd, None)

    _exec_forge_scratchpad({"operation": "write", "key": "durable", "value": "yes"}, wd)

    scratchpad_file = tmp_path / "Forge" / ".scratchpad.json"
    assert scratchpad_file.exists()
    data = json.loads(scratchpad_file.read_text(encoding="utf-8"))
    assert data["durable"] == "yes"


def test_load_from_disk_on_first_access(tmp_path):
    """When in-memory cache is empty, scratchpad is loaded from disk."""
    from app.services.tool_executor import _exec_forge_scratchpad, _scratchpads

    # Write file directly to disk, bypassing in-memory cache
    forge_dir = tmp_path / "Forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / ".scratchpad.json").write_text(
        json.dumps({"from_disk": "loaded_correctly"}),
        encoding="utf-8",
    )

    wd = str(tmp_path)
    _scratchpads.pop(wd, None)  # ensure cache miss

    result = _exec_forge_scratchpad({"operation": "read", "key": "from_disk"}, wd)
    assert result == "loaded_correctly"
