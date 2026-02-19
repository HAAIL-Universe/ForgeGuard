"""Tests for forge_ide.mcp.artifact_store — project-scoped MCP temp store."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from forge_ide.mcp.artifact_store import (
    ARTIFACT_TYPES,
    _store,
    clear_artifacts,
    get_artifact,
    list_artifacts,
    store_artifact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear():
    """Wipe in-memory store between tests."""
    _store.clear()


# ---------------------------------------------------------------------------
# store_artifact
# ---------------------------------------------------------------------------


def test_store_returns_metadata():
    _clear()
    result = store_artifact("p-1", "contract", "manifesto", "# content", persist=False)
    assert result["stored"] is True
    assert result["artifact_type"] == "contract"
    assert result["key"] == "manifesto"
    assert result["ttl_hours"] == 24
    assert result["size_chars"] > 0
    assert "store_key" in result


def test_store_key_format():
    _clear()
    result = store_artifact("p-1", "scout", "dossier", {"a": 1}, persist=False)
    assert result["store_key"] == "project:p-1:scout:dossier"


def test_store_overwrites_existing():
    _clear()
    store_artifact("p-1", "contract", "stack", "v1", persist=False)
    store_artifact("p-1", "contract", "stack", "v2", persist=False)
    result = get_artifact("p-1", "contract", "stack")
    assert result["content"] == "v2"


def test_store_various_content_types():
    _clear()
    # dict
    store_artifact("p-1", "scout", "profile", {"lang": "Python"}, persist=False)
    assert get_artifact("p-1", "scout", "profile")["content"] == {"lang": "Python"}
    # list
    store_artifact("p-1", "scout", "checks", [1, 2, 3], persist=False)
    assert get_artifact("p-1", "scout", "checks")["content"] == [1, 2, 3]
    # string
    store_artifact("p-1", "directive", "main", "# Build directive", persist=False)
    assert get_artifact("p-1", "directive", "main")["content"] == "# Build directive"
    # None
    store_artifact("p-1", "phase", "output", None, persist=False)
    assert get_artifact("p-1", "phase", "output")["content"] is None


# ---------------------------------------------------------------------------
# get_artifact
# ---------------------------------------------------------------------------


def test_get_returns_content_and_source():
    _clear()
    store_artifact("p-2", "contract", "physics", {"api": "v1"}, persist=False)
    result = get_artifact("p-2", "contract", "physics")
    assert result["content"] == {"api": "v1"}
    assert result["source"] == "memory"
    assert result["project_id"] == "p-2"
    assert result["artifact_type"] == "contract"
    assert result["key"] == "physics"


def test_get_missing_returns_error():
    _clear()
    result = get_artifact("p-99", "contract", "nonexistent")
    assert "error" in result
    assert "not found" in result["error"]


def test_get_expired_returns_error():
    _clear()
    store_artifact("p-3", "contract", "stack", "data", ttl_hours=0.0, persist=False)
    # Force expiry by setting stored_at far in the past
    key = "project:p-3:contract:stack"
    stored_at, ttl_secs, content = _store[key]
    _store[key] = (stored_at - 100, ttl_secs, content)  # aged 100 seconds past TTL=0
    result = get_artifact("p-3", "contract", "stack")
    assert "error" in result


def test_get_reports_age_and_ttl_remaining():
    _clear()
    store_artifact("p-4", "scout", "arch", {}, persist=False)
    result = get_artifact("p-4", "scout", "arch")
    assert result["age_seconds"] >= 0
    assert result["ttl_remaining_seconds"] > 0


# ---------------------------------------------------------------------------
# list_artifacts
# ---------------------------------------------------------------------------


def test_list_empty_project():
    _clear()
    result = list_artifacts("empty-project")
    assert result["count"] == 0
    assert result["artifacts"] == []


def test_list_returns_all_types():
    _clear()
    store_artifact("p-5", "contract", "manifesto", "m", persist=False)
    store_artifact("p-5", "scout", "dossier", "d", persist=False)
    store_artifact("p-5", "renovation", "brief", "b", persist=False)
    result = list_artifacts("p-5")
    assert result["count"] == 3
    keys = {a["key"] for a in result["artifacts"]}
    assert keys == {"manifesto", "dossier", "brief"}


def test_list_filtered_by_type():
    _clear()
    store_artifact("p-6", "contract", "stack", "s", persist=False)
    store_artifact("p-6", "contract", "physics", "p", persist=False)
    store_artifact("p-6", "scout", "dossier", "d", persist=False)
    result = list_artifacts("p-6", artifact_type="contract")
    assert result["count"] == 2
    assert all(a["artifact_type"] == "contract" for a in result["artifacts"])


def test_list_excludes_other_projects():
    _clear()
    store_artifact("proj-A", "contract", "stack", "A", persist=False)
    store_artifact("proj-B", "contract", "stack", "B", persist=False)
    result = list_artifacts("proj-A")
    assert result["count"] == 1
    assert result["artifacts"][0]["key"] == "stack"


def test_list_evicts_expired():
    _clear()
    store_artifact("p-7", "phase", "out", "data", ttl_hours=0.0, persist=False)
    key = "project:p-7:phase:out"
    stored_at, ttl_secs, content = _store[key]
    _store[key] = (stored_at - 100, ttl_secs, content)
    result = list_artifacts("p-7")
    assert result["count"] == 0
    assert key not in _store  # Evicted during list


def test_list_sorted_by_type_then_key():
    _clear()
    store_artifact("p-8", "scout", "zebra", "z", persist=False)
    store_artifact("p-8", "contract", "manifesto", "m", persist=False)
    store_artifact("p-8", "contract", "stack", "s", persist=False)
    result = list_artifacts("p-8")
    types_keys = [(a["artifact_type"], a["key"]) for a in result["artifacts"]]
    assert types_keys == sorted(types_keys)


# ---------------------------------------------------------------------------
# clear_artifacts
# ---------------------------------------------------------------------------


def test_clear_all_for_project():
    _clear()
    store_artifact("p-9", "contract", "a", "1", persist=False)
    store_artifact("p-9", "scout", "b", "2", persist=False)
    clear_artifacts("p-9")
    assert list_artifacts("p-9")["count"] == 0


def test_clear_scoped_to_type():
    _clear()
    store_artifact("p-10", "contract", "stack", "1", persist=False)
    store_artifact("p-10", "contract", "physics", "2", persist=False)
    store_artifact("p-10", "scout", "dossier", "3", persist=False)
    clear_artifacts("p-10", artifact_type="contract")
    result = list_artifacts("p-10")
    assert result["count"] == 1
    assert result["artifacts"][0]["artifact_type"] == "scout"


def test_clear_returns_counts():
    _clear()
    store_artifact("p-11", "contract", "a", "1", persist=False)
    store_artifact("p-11", "contract", "b", "2", persist=False)
    result = clear_artifacts("p-11", artifact_type="contract")
    assert result["cleared_memory"] == 2
    assert result["project_id"] == "p-11"
    assert result["artifact_type"] == "contract"


def test_clear_nonexistent_project_is_safe():
    _clear()
    result = clear_artifacts("no-such-project")
    assert result["cleared_memory"] == 0


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------


def test_disk_roundtrip(tmp_path):
    _clear()
    with patch("forge_ide.mcp.artifact_store._ARTIFACTS_DIR", tmp_path):
        store_artifact("p-disk", "scout", "profile", {"lang": "Python"}, persist=True)
        _store.clear()  # Evict from memory

        result = get_artifact("p-disk", "scout", "profile")
        assert result["content"] == {"lang": "Python"}
        assert result["source"] == "disk"


def test_disk_warmed_to_memory(tmp_path):
    _clear()
    with patch("forge_ide.mcp.artifact_store._ARTIFACTS_DIR", tmp_path):
        store_artifact("p-warm", "contract", "manifesto", "# M", persist=True)
        _store.clear()

        # First call loads from disk
        r1 = get_artifact("p-warm", "contract", "manifesto")
        assert r1["source"] == "disk"

        # Second call should be from memory (warmed)
        r2 = get_artifact("p-warm", "contract", "manifesto")
        assert r2["source"] == "memory"


def test_disk_list_includes_disk_only(tmp_path):
    _clear()
    with patch("forge_ide.mcp.artifact_store._ARTIFACTS_DIR", tmp_path):
        store_artifact("p-list", "renovation", "brief", "text", persist=True)
        _store.clear()  # Evict

        result = list_artifacts("p-list")
        assert result["count"] == 1
        assert result["artifacts"][0]["source"] == "disk"


def test_disk_clear_removes_files(tmp_path):
    _clear()
    with patch("forge_ide.mcp.artifact_store._ARTIFACTS_DIR", tmp_path):
        store_artifact("p-clr", "contract", "stack", "s", persist=True)
        store_artifact("p-clr", "contract", "physics", "p", persist=True)
        _store.clear()

        r = clear_artifacts("p-clr")
        assert r["cleared_disk"] == 2

        result = list_artifacts("p-clr")
        assert result["count"] == 0


def test_disk_write_failure_is_non_fatal():
    """store_artifact should not raise if disk write fails."""
    _clear()
    with patch("forge_ide.mcp.artifact_store._ARTIFACTS_DIR", Path("/nonexistent/path")):
        # Should not raise — disk failure is swallowed
        result = store_artifact("p-fail", "contract", "x", "data", persist=True)
        assert result["stored"] is True


# ---------------------------------------------------------------------------
# Dispatch integration (tools.py routing)
# ---------------------------------------------------------------------------


def test_dispatch_routes_artifact_tools_in_both_modes():
    """Artifact tools must be served in-process regardless of LOCAL_MODE."""
    _clear()
    import asyncio
    from forge_ide.mcp.tools import dispatch

    async def _run():
        # Store
        r = await dispatch("forge_store_artifact", {
            "project_id": "p-disp",
            "artifact_type": "contract",
            "key": "stack",
            "content": "# Stack",
            "persist": False,
        })
        assert r["stored"] is True

        # Get
        r2 = await dispatch("forge_get_artifact", {
            "project_id": "p-disp",
            "artifact_type": "contract",
            "key": "stack",
        })
        assert r2["content"] == "# Stack"

        # List
        r3 = await dispatch("forge_list_artifacts", {"project_id": "p-disp"})
        assert r3["count"] == 1

        # Clear
        r4 = await dispatch("forge_clear_artifacts", {"project_id": "p-disp"})
        assert r4["cleared_memory"] == 1

    asyncio.run(_run())


def test_dispatch_artifact_missing_params():
    _clear()
    import asyncio
    from forge_ide.mcp.tools import dispatch

    async def _run():
        r = await dispatch("forge_get_artifact", {"project_id": "p-x"})
        assert "error" in r

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ARTIFACT_TYPES catalogue
# ---------------------------------------------------------------------------


def test_artifact_types_catalogue():
    expected = {"contract", "scout", "renovation", "directive", "phase", "seal", "diff"}
    assert ARTIFACT_TYPES == expected
