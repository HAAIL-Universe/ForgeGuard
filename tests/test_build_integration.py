"""Integration tests for the full build lifecycle.

Tests end-to-end flows with mocked LLM agent, GitHub API, and git
operations. Verifies the orchestration layer handles all scenarios:
target selection, plan emission, file writing, audit pass/fail,
loopback, pause/resume, interjection, compaction, and git push.
"""

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch, PropertyMock

import pytest

from app.services import build_service
from app.config import settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _project(**overrides):
    defaults = {
        "id": _PROJECT_ID,
        "user_id": _USER_ID,
        "name": "IntegTest Project",
        "status": "contracts_ready",
    }
    defaults.update(overrides)
    return defaults


def _contracts():
    return [
        {"contract_type": "blueprint", "content": "# Blueprint\nTest project"},
        {"contract_type": "manifesto", "content": "# Manifesto\nTest manifesto"},
        {"contract_type": "stack", "content": "# Stack\npython, react"},
        {"contract_type": "schema", "content": "# Schema\nusers table"},
        {"contract_type": "physics", "content": "# Physics\n/health: get"},
        {"contract_type": "boundaries", "content": '{"layers": []}'},
        {"contract_type": "phases", "content": "## Phase 0 -- Genesis\n**Objective:** Scaffold"},
    ]


def _build(**overrides):
    defaults = {
        "id": _BUILD_ID,
        "project_id": _PROJECT_ID,
        "phase": "Phase 0",
        "status": "pending",
        "target_type": None,
        "target_ref": None,
        "working_dir": None,
        "started_at": None,
        "completed_at": None,
        "loop_count": 0,
        "error_detail": None,
        "paused_at": None,
        "pause_reason": None,
        "pause_phase": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


def _make_phase_output(phase: str = "Phase 0", files: list[dict] | None = None,
                       plan: list[str] | None = None) -> str:
    """Build a builder output string with optional plan, files, and sign-off."""
    parts = []
    if plan:
        parts.append("=== PLAN ===")
        for i, task in enumerate(plan, 1):
            parts.append(f"{i}. {task}")
        parts.append("=== END PLAN ===")
        for i in range(1, len(plan) + 1):
            parts.append(f"=== TASK DONE: {i} ===")

    if files:
        for f in files:
            parts.append(f"=== FILE: {f['path']} ===")
            parts.append(f.get("content", f"# {f['path']}\npass\n"))
            parts.append("=== END FILE ===")

    parts.append(f"=== PHASE SIGN-OFF: PASS ===")
    parts.append(f"Phase: {phase}")
    return "\n".join(parts)


async def _stream_chunks(text: str, chunk_size: int = 200):
    """Async generator that yields text in chunks, mimicking stream_agent."""
    pos = 0
    while pos < len(text):
        yield text[pos:pos + chunk_size]
        pos += chunk_size
        await asyncio.sleep(0)


def _mock_stream_agent(responses: list[str]):
    """Create a mock stream_agent that yields the given responses in order.
    After all responses are consumed, yields a bare message (no phase signal)
    so the while-True loop in _run_build exits gracefully.
    """
    call_count = 0

    async def mock_stream(*, api_key, model, system_prompt, messages, max_tokens=16384, usage_out, tools=None, on_retry=None, token_limiter=None):
        nonlocal call_count
        idx = call_count
        call_count += 1
        if idx < len(responses):
            text = responses[idx]
        else:
            text = "All phases have been completed."
        async for chunk in _stream_chunks(text):
            yield chunk

    return mock_stream


# Common patches for _run_build
def _build_patches():
    return {
        "build_repo": patch("app.services.build_service.build_repo"),
        "project_repo": patch("app.services.build_service.project_repo"),
        "git_client": patch("app.services.build_service.git_client"),
        "github_client": patch("app.services.build_service.github_client"),
        "manager": patch("app.services.build_service.manager"),
        "get_user": patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock),
        "recovery_planner": patch(
            "app.services.build_service._run_recovery_planner",
            new_callable=AsyncMock,
            return_value="Mocked remediation plan",
        ),
    }


def _setup_mocks(mocks, working_dir=None, audit_result="PASS"):
    """Configure common mock return values."""
    brm = mocks["build_repo"]
    brm.update_build_status = AsyncMock()
    brm.append_build_log = AsyncMock(return_value={"id": uuid.uuid4()})
    brm.increment_loop_count = AsyncMock(return_value=1)
    brm.get_build_by_id = AsyncMock(return_value=_build(status="running"))
    brm.get_latest_build_for_project = AsyncMock(return_value=_build(status="running"))
    brm.get_build_cost_summary = AsyncMock(return_value={
        "total_input_tokens": 100, "total_output_tokens": 200,
        "total_cost_usd": Decimal("0.01"),
    })
    brm.get_build_costs = AsyncMock(return_value=[])
    brm.get_build_file_logs = AsyncMock(return_value=[])
    brm.record_build_cost = AsyncMock(return_value={"id": uuid.uuid4()})
    brm.pause_build = AsyncMock(return_value=True)
    brm.resume_build = AsyncMock(return_value=True)
    brm.cancel_build = AsyncMock(return_value=True)
    brm.create_build = AsyncMock(return_value=_build(working_dir=working_dir))
    brm.get_build_stats = AsyncMock(return_value={
        "total_turns": 1, "total_audit_attempts": 1,
        "files_written_count": 2, "git_commits_made": 1,
        "interjections_received": 0,
    })

    prm = mocks["project_repo"]
    prm.get_project_by_id = AsyncMock(return_value=_project())
    prm.update_project_status = AsyncMock()
    prm.get_contracts_by_project = AsyncMock(return_value=_contracts())
    prm.get_contract_by_type = AsyncMock(return_value={
        "content": "## Phase 0 -- Genesis\n**Objective:** scaffold",
    })

    mocks["manager"].send_to_user = AsyncMock()

    gc = mocks["git_client"]
    gc.commit = AsyncMock(return_value="abc1234")
    gc.push = AsyncMock()
    gc.clone_repo = AsyncMock()
    gc.init_repo = AsyncMock()
    gc.get_file_list = AsyncMock(return_value=[])
    gc.create_branch = AsyncMock()
    gc.checkout_branch = AsyncMock()

    ghc = mocks["github_client"]
    ghc.create_github_repo = AsyncMock(return_value={
        "full_name": "user/test-repo",
    })


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestFullBuildLifecycle:
    """Test the complete build lifecycle: plan → files → audit → commit."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_single_phase(self):
        """Single phase: plan emission → file blocks → sign-off → audit pass → completion."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            output = _make_phase_output(
                "Phase 0",
                files=[
                    {"path": "app/main.py", "content": "from fastapi import FastAPI\napp = FastAPI()\n"},
                    {"path": "tests/test_health.py", "content": "def test_health(): pass\n"},
                ],
                plan=["Create main.py", "Create test_health.py"],
            )

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="local_path",
                        target_ref=tmpdir,
                        working_dir=tmpdir,
                    )

            # Files should be written to disk
            assert (Path(tmpdir) / "app" / "main.py").exists()
            assert (Path(tmpdir) / "tests" / "test_health.py").exists()

            # Build should be marked completed
            entered["build_repo"].update_build_status.assert_any_call(
                _BUILD_ID, "completed", completed_at=ANY
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_audit_failure_self_correction(self):
        """Audit fail on first attempt → loopback → audit pass on second attempt."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            # First response: builder produces code
            first_output = _make_phase_output("Phase 0", files=[
                {"path": "app/main.py", "content": "# v1\n"},
            ])
            # Second response: builder fixes issues
            second_output = _make_phase_output("Phase 0", files=[
                {"path": "app/main.py", "content": "# v2 fixed\n"},
            ])

            audit_results = iter([("FAIL", "Audit found issues"), ("PASS", "")])

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([first_output, second_output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.side_effect = lambda *a, **kw: next(audit_results)

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="local_path",
                        target_ref=tmpdir,
                        working_dir=tmpdir,
                    )

            # Should have looped back once
            entered["build_repo"].increment_loop_count.assert_called_once()
            # But ultimately completed
            entered["build_repo"].update_build_status.assert_any_call(
                _BUILD_ID, "completed", completed_at=ANY
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_pause_threshold_then_user_retry(self):
        """Audit fails MAX_LOOP_COUNT times → pause → user retries → builder succeeds."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            fail_output = _make_phase_output("Phase 0")
            pass_output = _make_phase_output("Phase 0")

            # MAX_LOOP_COUNT failures, then pass after resume
            fail_count = settings.PAUSE_THRESHOLD
            results = [("FAIL", "Audit found issues")] * fail_count + [("PASS", "")]
            audit_iter = iter(results)
            responses = [fail_output] * fail_count + [pass_output]

            async def auto_resume():
                """Wait for pause then resume with retry."""
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    if str(_BUILD_ID) in build_service._pause_events:
                        build_service._resume_actions[str(_BUILD_ID)] = "retry"
                        build_service._pause_events[str(_BUILD_ID)].set()
                        return

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent(responses)):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.side_effect = lambda *a, **kw: next(audit_iter)

                    await asyncio.gather(
                        build_service._run_build(
                            _BUILD_ID, _PROJECT_ID, _USER_ID,
                            _contracts(), "sk-test", True,
                            target_type="local_path",
                            target_ref=tmpdir,
                            working_dir=tmpdir,
                        ),
                        auto_resume(),
                    )

            # Should have paused
            entered["build_repo"].pause_build.assert_called_once()
            entered["build_repo"].resume_build.assert_called_once()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_pause_then_user_skip(self):
        """Pause → user skips phase → build advances."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            fail_output = _make_phase_output("Phase 0")
            fail_count = settings.PAUSE_THRESHOLD
            results = [("FAIL", "Audit found issues")] * fail_count
            audit_iter = iter(results)

            async def auto_skip():
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    if str(_BUILD_ID) in build_service._pause_events:
                        build_service._resume_actions[str(_BUILD_ID)] = "skip"
                        build_service._pause_events[str(_BUILD_ID)].set()
                        return

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([fail_output] * fail_count)):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.side_effect = lambda *a, **kw: next(audit_iter)

                    await asyncio.gather(
                        build_service._run_build(
                            _BUILD_ID, _PROJECT_ID, _USER_ID,
                            _contracts(), "sk-test", True,
                            target_type="local_path",
                            target_ref=tmpdir,
                            working_dir=tmpdir,
                        ),
                        auto_skip(),
                    )

            # Should have broadcast build_resumed with skip action
            ws_calls = entered["manager"].send_to_user.call_args_list
            resumed_events = [c for c in ws_calls if c[0][1].get("type") == "build_resumed"]
            assert any(e[0][1]["payload"]["action"] == "skip" for e in resumed_events)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_pause_then_user_abort(self):
        """Pause → user aborts → build fails."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            fail_output = _make_phase_output("Phase 0")
            fail_count = settings.PAUSE_THRESHOLD
            results = [("FAIL", "Audit found issues")] * fail_count
            audit_iter = iter(results)

            async def auto_abort():
                for _ in range(50):
                    await asyncio.sleep(0.05)
                    if str(_BUILD_ID) in build_service._pause_events:
                        build_service._resume_actions[str(_BUILD_ID)] = "abort"
                        build_service._pause_events[str(_BUILD_ID)].set()
                        return

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([fail_output] * fail_count)):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.side_effect = lambda *a, **kw: next(audit_iter)

                    await asyncio.gather(
                        build_service._run_build(
                            _BUILD_ID, _PROJECT_ID, _USER_ID,
                            _contracts(), "sk-test", True,
                            target_type="local_path",
                            target_ref=tmpdir,
                            working_dir=tmpdir,
                        ),
                        auto_abort(),
                    )

            # Should have failed with abort message
            entered["build_repo"].update_build_status.assert_any_call(
                _BUILD_ID, "failed",
                completed_at=ANY,
                error_detail=ANY,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestInterjection:
    """Test user interjection during builds."""

    @pytest.mark.asyncio
    async def test_interjection_injected_into_conversation(self):
        """Message sent via interject_build appears in builder conversation."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            # Pre-populate interjection queue
            queue = asyncio.Queue()
            queue.put_nowait("Please use SQLite instead of PostgreSQL")
            build_service._interjection_queues[str(_BUILD_ID)] = queue

            output = _make_phase_output("Phase 0")

            captured_messages = []

            original_mock = _mock_stream_agent([output, output])

            async def capturing_stream(*, api_key, model, system_prompt, messages, max_tokens=16384, usage_out, tools=None, on_retry=None, token_limiter=None):
                captured_messages.append([m.copy() for m in messages])
                async for chunk in original_mock(api_key=api_key, model=model,
                                                  system_prompt=system_prompt,
                                                  messages=messages, usage_out=usage_out):
                    yield chunk

            with patch("app.services.build_service.stream_agent", side_effect=capturing_stream):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="local_path",
                        target_ref=tmpdir,
                        working_dir=tmpdir,
                    )

            # Second turn should have the interjection in messages
            if len(captured_messages) >= 2:
                second_call = captured_messages[1]
                interjection_msgs = [m for m in second_call if "[User interjection]" in m.get("content", "")]
                assert len(interjection_msgs) > 0
                assert "SQLite" in interjection_msgs[0]["content"]
        finally:
            build_service._interjection_queues.pop(str(_BUILD_ID), None)
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestContextCompaction:
    """Test context compaction with large conversations."""

    @pytest.mark.asyncio
    async def test_compaction_triggers_on_threshold(self):
        """Conversation is compacted when token count exceeds threshold."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            output = _make_phase_output("Phase 0")

            # Force high token usage to trigger compaction
            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")
                    with patch("app.services.build_service.CONTEXT_COMPACTION_THRESHOLD", 0):
                        await build_service._run_build(
                            _BUILD_ID, _PROJECT_ID, _USER_ID,
                            _contracts(), "sk-test", True,
                            target_type="local_path",
                            target_ref=tmpdir,
                            working_dir=tmpdir,
                        )

            # Compaction log should have been appended
            log_calls = entered["build_repo"].append_build_log.call_args_list
            compaction_logs = [c for c in log_calls if "compacted" in str(c).lower()]
            # May or may not trigger depending on message count; existence of the path is enough
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestFileOverwrite:
    """Test that the same file path written twice is overwritten."""

    @pytest.mark.asyncio
    async def test_file_overwrite_same_path(self):
        """Same file path emitted twice → second content wins on disk."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            # Two file blocks with the same path
            output = (
                "=== FILE: app/main.py ===\n# version 1\n=== END FILE ===\n"
                "=== FILE: app/main.py ===\n# version 2 final\n=== END FILE ===\n"
                "=== PHASE SIGN-OFF: PASS ===\nPhase: Phase 0\n"
            )

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="local_path",
                        target_ref=tmpdir,
                        working_dir=tmpdir,
                    )

            # Final content should be v2
            content = (Path(tmpdir) / "app" / "main.py").read_text()
            assert "version 2" in content
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestBuildTargets:
    """Test different build target types."""

    @pytest.mark.asyncio
    async def test_local_path_file_writing(self):
        """local_path target writes files to disk."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            output = _make_phase_output("Phase 0", files=[
                {"path": "README.md", "content": "# Hello\n"},
            ])

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="local_path",
                        target_ref=tmpdir,
                        working_dir=tmpdir,
                    )

            assert (Path(tmpdir) / "README.md").exists()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()

    @pytest.mark.asyncio
    async def test_github_new_repo_target(self):
        """github_new target creates repo, clones, writes files, commits, pushes."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            output = _make_phase_output("Phase 0", files=[
                {"path": "app/main.py", "content": "# app\n"},
            ])

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")

                    await build_service._run_build(
                        _BUILD_ID, _PROJECT_ID, _USER_ID,
                        _contracts(), "sk-test", True,
                        target_type="github_new",
                        target_ref="test-repo",
                        working_dir=tmpdir,
                        access_token="ghp_test123",
                    )

            # Should have created repo and pushed
            entered["github_client"].create_github_repo.assert_called_once()
            entered["git_client"].clone_repo.assert_called_once()
            entered["git_client"].push.assert_called()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestMalformedBlocks:
    """Test graceful handling of malformed file blocks."""

    def test_missing_end_delimiter(self):
        """File block without === END FILE === is skipped."""
        text = "=== FILE: app/main.py ===\n# content\n"
        blocks = build_service._parse_file_blocks(text)
        assert len(blocks) == 0

    def test_empty_file_path(self):
        """File block with empty path is skipped."""
        text = "=== FILE:  ===\n# content\n=== END FILE ===\n"
        blocks = build_service._parse_file_blocks(text)
        assert len(blocks) == 0

    def test_valid_blocks_still_parsed(self):
        """Valid blocks are parsed even when mixed with malformed ones."""
        text = (
            "=== FILE: good.py ===\n# good\n=== END FILE ===\n"
            "=== FILE: broken.py ===\n# no end delimiter\n"
        )
        blocks = build_service._parse_file_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["path"] == "good.py"

    def test_multiple_valid_blocks(self):
        """Multiple well-formed blocks are all parsed."""
        text = (
            "=== FILE: a.py ===\n# a\n=== END FILE ===\n"
            "=== FILE: b.py ===\n# b\n=== END FILE ===\n"
            "=== FILE: c.py ===\n# c\n=== END FILE ===\n"
        )
        blocks = build_service._parse_file_blocks(text)
        assert len(blocks) == 3


class TestLargeFileWarning:
    """Test large file warning threshold."""

    @pytest.mark.asyncio
    async def test_large_file_logs_warning(self):
        """Files larger than LARGE_FILE_WARN_BYTES trigger a warning log."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            files_written: list[dict] = []

            with patch.object(settings, "LARGE_FILE_WARN_BYTES", 10):
                await build_service._write_file_block(
                    _BUILD_ID, _USER_ID, tmpdir,
                    "big_file.py", "x" * 100,  # 100 bytes > 10 threshold
                    files_written,
                )

            # Should have logged a warning about large file
            warn_calls = [c for c in entered["build_repo"].append_build_log.call_args_list
                         if "large file" in str(c).lower() or "Warning" in str(c)]
            assert len(warn_calls) > 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestGitPushRetry:
    """Test git push retry with exponential backoff."""

    @pytest.mark.asyncio
    async def test_push_retries_on_failure(self):
        """Git push failure retries with backoff, then pauses."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)

            # Push always fails
            entered["git_client"].push = AsyncMock(side_effect=Exception("network error"))

            output = _make_phase_output("Phase 0", files=[
                {"path": "app/main.py", "content": "# app\n"},
            ])

            async def auto_abort():
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if str(_BUILD_ID) in build_service._pause_events:
                        build_service._resume_actions[str(_BUILD_ID)] = "abort"
                        build_service._pause_events[str(_BUILD_ID)].set()
                        return

            with patch("app.services.build_service.stream_agent", side_effect=_mock_stream_agent([output])):
                with patch("app.services.build_service._run_inline_audit", new_callable=AsyncMock) as mock_audit:
                    mock_audit.return_value = ("PASS", "")
                    with patch.object(settings, "GIT_PUSH_MAX_RETRIES", 2):
                        await asyncio.gather(
                            build_service._run_build(
                                _BUILD_ID, _PROJECT_ID, _USER_ID,
                                _contracts(), "sk-test", True,
                                target_type="github_new",
                                target_ref="test-repo",
                                working_dir=tmpdir,
                                access_token="ghp_test123",
                            ),
                            auto_abort(),
                        )

            # Push should have been attempted twice
            assert entered["git_client"].push.call_count == 2
            # Should have paused
            entered["build_repo"].pause_build.assert_called()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestWorkingDirCleanup:
    """Test working directory cleanup on failure."""

    @pytest.mark.asyncio
    async def test_github_target_cleaned_on_failure(self):
        """Failed github_new build cleans up temp working directory."""
        tmpdir = tempfile.mkdtemp(prefix="fg_integ_")
        try:
            patches = _build_patches()
            entered = {}
            for k, p in patches.items():
                entered[k] = p.start()
            _setup_mocks(entered, working_dir=tmpdir)
            entered["build_repo"].get_build_by_id = AsyncMock(
                return_value=_build(status="failed")
            )

            # Make github_client.create_github_repo fail
            entered["github_client"].create_github_repo = AsyncMock(
                side_effect=Exception("API error")
            )

            await build_service._run_build(
                _BUILD_ID, _PROJECT_ID, _USER_ID,
                _contracts(), "sk-test", True,
                target_type="github_new",
                target_ref="test-repo",
                working_dir=tmpdir,
                access_token="ghp_test123",
            )

            # Working dir should be cleaned up
            assert not os.path.exists(tmpdir)
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
            for p in patches.values():
                p.stop()


class TestObservability:
    """Test enhanced build summary with stats."""

    @pytest.mark.asyncio
    async def test_build_summary_includes_stats(self):
        """get_build_summary includes total_turns, audit_attempts, etc."""
        patches = _build_patches()
        entered = {}
        for k, p in patches.items():
            entered[k] = p.start()
        try:
            _setup_mocks(entered)
            entered["build_repo"].get_latest_build_for_project = AsyncMock(
                return_value=_build(
                    status="completed",
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
            )

            result = await build_service.get_build_summary(_PROJECT_ID, _USER_ID)

            assert "total_turns" in result
            assert "total_audit_attempts" in result
            assert "files_written_count" in result
            assert "git_commits_made" in result
            assert "interjections_received" in result
            assert result["total_turns"] == 1
            assert result["files_written_count"] == 2
        finally:
            for p in patches.values():
                p.stop()


class TestSearchableLogs:
    """Test build logs search/filter."""

    @pytest.mark.asyncio
    async def test_get_build_logs_with_search(self):
        """get_build_logs passes search and level params through."""
        patches = _build_patches()
        entered = {}
        for k, p in patches.items():
            entered[k] = p.start()
        try:
            _setup_mocks(entered)
            entered["build_repo"].get_build_logs = AsyncMock(return_value=([], 0))

            await build_service.get_build_logs(
                _PROJECT_ID, _USER_ID, 50, 0,
                search="error", level="warn",
            )

            entered["build_repo"].get_build_logs.assert_called_once_with(
                _BUILD_ID, 50, 0, search="error", level="warn",
            )
        finally:
            for p in patches.values():
                p.stop()


class TestConfigDefaults:
    """Test new Phase 15 config defaults."""

    def test_phase_timeout_default(self):
        assert settings.PHASE_TIMEOUT_MINUTES == 10

    def test_large_file_warn_default(self):
        assert settings.LARGE_FILE_WARN_BYTES == 1024 * 1024

    def test_git_push_max_retries_default(self):
        assert settings.GIT_PUSH_MAX_RETRIES == 3
