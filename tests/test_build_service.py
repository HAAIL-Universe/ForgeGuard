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
@patch("app.services.build_service.manager")
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
    """_build_directive assembles contracts in canonical order with universal governance."""
    contracts = [
        {"contract_type": "manifesto", "content": "# Manifesto"},
        {"contract_type": "blueprint", "content": "# Blueprint"},
    ]

    result = build_service._build_directive(contracts)

    # Should include the universal governance heading and per-project contracts
    assert "Forge Governance" in result or "Project Contracts" in result
    # Blueprint should come before manifesto in canonical order
    bp_pos = result.index("blueprint")
    mf_pos = result.index("manifesto")
    assert bp_pos < mf_pos


# ---------------------------------------------------------------------------
# Tests: _run_inline_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
async def test_run_inline_audit(mock_build_repo):
    """_run_inline_audit returns PASS when audit is disabled."""
    mock_build_repo.append_build_log = AsyncMock()

    result = await build_service._run_inline_audit(
        _BUILD_ID, "Phase 1", "builder output", _contracts(), "sk-ant-test", False
    )

    assert result == "PASS"
    mock_build_repo.append_build_log.assert_called()


@pytest.mark.asyncio
@patch("app.services.build_service.build_repo")
async def test_run_inline_audit_enabled_no_prompt(mock_build_repo, tmp_path, monkeypatch):
    """_run_inline_audit falls back to PASS when auditor_prompt.md is missing."""
    mock_build_repo.append_build_log = AsyncMock()
    # Point FORGE_CONTRACTS_DIR to empty tmp dir → no auditor_prompt.md
    monkeypatch.setattr(build_service, "FORGE_CONTRACTS_DIR", tmp_path)

    result = await build_service._run_inline_audit(
        _BUILD_ID, "Phase 1", "output", _contracts(), "sk-ant-test", True
    )

    assert result == "PASS"


# ---------------------------------------------------------------------------
# Tests: _fail_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.build_service.manager")
@patch("app.services.build_service.build_repo")
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
@patch("app.services.build_service.build_repo")
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

    haiku_in, haiku_out = build_service._get_token_rates("claude-haiku-4-5-20251001")
    assert haiku_in == Decimal("0.000001")
    assert haiku_out == Decimal("0.000005")

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
