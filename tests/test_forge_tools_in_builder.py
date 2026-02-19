"""Tests for Forge governance tools executed via execute_tool_async.

Verifies all 5 forge tools work correctly when dispatched through the
build loop's main tool executor.
"""

import asyncio
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workdir(tmp_path: Path) -> Path:
    """Create a minimal Forge/Contracts/ workspace in tmp_path."""
    contracts = tmp_path / "Forge" / "Contracts"
    contracts.mkdir(parents=True)

    (contracts / "blueprint.md").write_text(
        "# TaskFlow Blueprint\n\nA task management app.\n", encoding="utf-8"
    )
    (contracts / "stack.md").write_text(
        "# TaskFlow Stack\n\nFastAPI + PostgreSQL + React.\n", encoding="utf-8"
    )
    (contracts / "boundaries.json").write_text(
        json.dumps({
            "description": "Layer boundaries for TaskFlow",
            "layers": [
                {"name": "routers", "glob": "app/api/**/*.py", "forbidden": []},
                {"name": "services", "glob": "app/services/**/*.py", "forbidden": []},
            ],
            "known_violations": [],
        }),
        encoding="utf-8",
    )
    (contracts / "physics.yaml").write_text(
        "info:\n  title: TaskFlow API\n  version: '1.0'\npaths: {}\n",
        encoding="utf-8",
    )
    (contracts / "phases.md").write_text(
        "# TaskFlow Phases\n\n"
        "## Phase 0 — Genesis\n\nObjective: Bootstrap.\n\n**Deliverables:**\n- Boot script\n\n**Exit criteria:**\n- GET /health → 200\n\n"
        "## Phase 1 — Authentication\n\nObjective: OAuth + JWT.\n\n**Deliverables:**\n- GitHub OAuth flow\n\n**Exit criteria:**\n- Login works\n\n"
        "## Phase 2 — Core Features\n\nObjective: Task CRUD.\n\n**Deliverables:**\n- Tasks API\n\n**Exit criteria:**\n- Tests pass\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# forge_get_contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forge_get_contract_reads_markdown(tmp_path):
    """forge_get_contract returns content of a markdown contract."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_contract", {"name": "blueprint"}, wd
    )

    assert "TaskFlow Blueprint" in result
    assert "task management" in result.lower()


@pytest.mark.asyncio
async def test_forge_get_contract_reads_json(tmp_path):
    """forge_get_contract returns JSON boundaries contract."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_contract", {"name": "boundaries"}, wd
    )

    assert "routers" in result
    assert "services" in result


@pytest.mark.asyncio
async def test_forge_get_contract_blocks_phases(tmp_path):
    """forge_get_contract blocks the 'phases' name and redirects to forge_get_phase_window."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_contract", {"name": "phases"}, wd
    )

    assert "Error" in result
    assert "forge_get_phase_window" in result


@pytest.mark.asyncio
async def test_forge_get_contract_unknown_name(tmp_path):
    """forge_get_contract returns Error for unknown contract names."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_contract", {"name": "nonexistent_contract"}, wd
    )

    assert "Error" in result
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_forge_get_contract_missing_dir(tmp_path):
    """forge_get_contract returns Error when Forge/Contracts/ is absent."""
    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_contract", {"name": "blueprint"}, str(tmp_path)
    )

    assert "Error" in result


# ---------------------------------------------------------------------------
# forge_get_phase_window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forge_get_phase_window_returns_current_and_next(tmp_path):
    """forge_get_phase_window returns Phase 0 + Phase 1 for phase_number=0."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_phase_window", {"phase_number": 0}, wd
    )

    assert "Phase 0" in result
    assert "Phase 1" in result
    assert "Genesis" in result
    assert "Authentication" in result


@pytest.mark.asyncio
async def test_forge_get_phase_window_last_phase(tmp_path):
    """forge_get_phase_window returns only the final phase when at last phase."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_phase_window", {"phase_number": 2}, wd
    )

    assert "Phase 2" in result
    assert "Core Features" in result
    assert "final phase" in result.lower()


@pytest.mark.asyncio
async def test_forge_get_phase_window_invalid_phase(tmp_path):
    """forge_get_phase_window returns Error for a phase that doesn't exist."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_phase_window", {"phase_number": 99}, wd
    )

    assert "Error" in result
    assert "99" in result


@pytest.mark.asyncio
async def test_forge_get_phase_window_missing_phases_file(tmp_path):
    """forge_get_phase_window returns Error when phases.md is absent."""
    # Create workdir without phases.md
    contracts = tmp_path / "Forge" / "Contracts"
    contracts.mkdir(parents=True)

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async(
        "forge_get_phase_window", {"phase_number": 0}, str(tmp_path)
    )

    assert "Error" in result
    assert "phases.md" in result


# ---------------------------------------------------------------------------
# forge_list_contracts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forge_list_contracts_returns_all_files(tmp_path):
    """forge_list_contracts lists all files in Forge/Contracts/."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async("forge_list_contracts", {}, wd)

    data = json.loads(result)
    names = [c["name"] for c in data["contracts"]]
    assert "blueprint" in names
    assert "stack" in names
    assert "boundaries" in names
    assert data["total"] >= 5


@pytest.mark.asyncio
async def test_forge_list_contracts_empty_dir(tmp_path):
    """forge_list_contracts returns empty list when no contracts exist."""
    contracts = tmp_path / "Forge" / "Contracts"
    contracts.mkdir(parents=True)

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async("forge_list_contracts", {}, str(tmp_path))

    data = json.loads(result)
    assert data["total"] == 0
    assert data["contracts"] == []


# ---------------------------------------------------------------------------
# forge_get_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forge_get_summary_returns_overview(tmp_path):
    """forge_get_summary returns a JSON summary with key fields."""
    wd = str(_make_workdir(tmp_path))

    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async("forge_get_summary", {}, wd)

    data = json.loads(result)
    assert "framework" in data
    assert "available_contracts" in data
    assert "architectural_layers" in data
    assert "key_tools" in data
    assert "critical_rules" in data

    # Boundaries were parsed — should have layer names
    assert "routers" in data["architectural_layers"]
    assert "services" in data["architectural_layers"]


@pytest.mark.asyncio
async def test_forge_get_summary_no_contracts_dir(tmp_path):
    """forge_get_summary still returns a summary when no contracts exist."""
    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async("forge_get_summary", {}, str(tmp_path))

    data = json.loads(result)
    assert "framework" in data
    assert data["available_contracts"] == []


# ---------------------------------------------------------------------------
# forge_scratchpad (via execute_tool_async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forge_scratchpad_write_and_read(tmp_path):
    """forge_scratchpad write then read returns the stored value."""
    wd = str(tmp_path)

    from app.services.tool_executor import execute_tool_async

    write_result = await execute_tool_async(
        "forge_scratchpad",
        {"operation": "write", "key": "arch", "value": "FastAPI + asyncpg"},
        wd,
    )
    assert "OK" in write_result

    read_result = await execute_tool_async(
        "forge_scratchpad",
        {"operation": "read", "key": "arch"},
        wd,
    )
    assert read_result == "FastAPI + asyncpg"


@pytest.mark.asyncio
async def test_forge_scratchpad_append(tmp_path):
    """forge_scratchpad append accumulates values."""
    wd = str(tmp_path)

    from app.services.tool_executor import execute_tool_async

    await execute_tool_async(
        "forge_scratchpad",
        {"operation": "write", "key": "notes", "value": "Line 1"},
        wd,
    )
    await execute_tool_async(
        "forge_scratchpad",
        {"operation": "append", "key": "notes", "value": "\nLine 2"},
        wd,
    )

    result = await execute_tool_async(
        "forge_scratchpad",
        {"operation": "read", "key": "notes"},
        wd,
    )
    assert "Line 1" in result
    assert "Line 2" in result


@pytest.mark.asyncio
async def test_forge_scratchpad_list(tmp_path):
    """forge_scratchpad list returns all keys."""
    wd = str(tmp_path)

    from app.services.tool_executor import execute_tool_async

    await execute_tool_async(
        "forge_scratchpad",
        {"operation": "write", "key": "key_a", "value": "val_a"},
        wd,
    )
    await execute_tool_async(
        "forge_scratchpad",
        {"operation": "write", "key": "key_b", "value": "val_b"},
        wd,
    )

    result = await execute_tool_async("forge_scratchpad", {"operation": "list"}, wd)
    data = json.loads(result)
    assert "key_a" in data["keys"]
    assert "key_b" in data["keys"]


@pytest.mark.asyncio
async def test_forge_scratchpad_unknown_tool_returns_error():
    """execute_tool_async returns Error string for unknown tool names."""
    from app.services.tool_executor import execute_tool_async
    result = await execute_tool_async("unknown_tool_xyz", {}, "/tmp")
    assert "Error" in result
    assert "unknown_tool_xyz" in result
