"""
Tests for the builder agent orchestrator pipeline.

Covers:
  1. Happy-path pipeline returns BuilderResult (SCOUT → CODER → AUDITOR → PASS)
  2. generate_file() no longer raises NotImplementedError
  3. stop_event interrupts the pipeline cleanly
  4. AUDITOR FAIL triggers FIXER dispatch
  5. Token usage accumulates across all sub-agent results
  6. File parallelism: semaphore caps at 3, locks protect shared state
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FILE_ENTRY = {
    "path": "app/main.py",
    "purpose": "FastAPI entry point",
    "estimated_lines": 40,
    "language": "python",
}

BUILD_ID = str(uuid4())
USER_ID = str(uuid4())
API_KEY = "sk-ant-test"
WORKING_DIR = "/tmp/forge_test_workspace"


def _make_sub_agent_result(
    role_str: str = "scout",
    status_str: str = "completed",
    verdict: str = "PASS",
    input_tokens: int = 100,
    output_tokens: int = 50,
    error: str = "",
    files_written: list | None = None,
):
    """Build a SubAgentResult-shaped MagicMock for testing."""
    from app.services.build.subagent import HandoffStatus, SubAgentRole

    role_map = {
        "scout": SubAgentRole.SCOUT,
        "coder": SubAgentRole.CODER,
        "auditor": SubAgentRole.AUDITOR,
        "fixer": SubAgentRole.FIXER,
    }
    status_map = {
        "completed": HandoffStatus.COMPLETED,
        "failed": HandoffStatus.FAILED,
    }

    m = MagicMock()
    m.role = role_map[role_str]
    m.status = status_map[status_str]
    m.input_tokens = input_tokens
    m.output_tokens = output_tokens
    m.error = error
    m.text_output = ""
    m.files_written = files_written or []
    m.structured_output = {"verdict": verdict, "findings": []}
    m.handoff_id = f"{role_str}-test-id"
    return m


# ---------------------------------------------------------------------------
# 1. Happy-path pipeline returns BuilderResult
# ---------------------------------------------------------------------------


class TestRunBuilderReturnsBuildResult:
    """Mock all sub-agents, verify pipeline runs and BuilderResult is returned."""

    @pytest.mark.asyncio
    async def test_run_builder_returns_builder_result(self, tmp_path):
        from builder.builder_agent import run_builder, BuilderResult

        scout_r = _make_sub_agent_result("scout", input_tokens=120, output_tokens=60)
        coder_r = _make_sub_agent_result("coder", input_tokens=200, output_tokens=150,
                                          files_written=["app/main.py"])
        auditor_r = _make_sub_agent_result("auditor", verdict="PASS",
                                            input_tokens=80, output_tokens=40)

        # Write a placeholder file so the auditor context read succeeds.
        # Must be > 50 chars stripped to avoid _audit_auto_pass.
        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        call_sequence = [scout_r, coder_r, auditor_r]
        call_idx = 0

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            nonlocal call_idx
            r = call_sequence[call_idx]
            call_idx += 1
            return r

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=["## Stack\npython fastapi"],
                context=[],
                phase_deliverables=["FastAPI hello endpoint"],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        assert isinstance(result, BuilderResult)
        assert result.status == "completed"
        assert result.file_path == "app/main.py"
        assert result.error == ""
        assert call_idx == 3  # SCOUT + CODER + AUDITOR called

    @pytest.mark.asyncio
    async def test_pipeline_order_scout_coder_auditor(self, tmp_path):
        """Verify sub-agents are dispatched in SCOUT → CODER → AUDITOR order."""
        from app.services.build.subagent import SubAgentRole
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n", encoding="utf-8")

        dispatch_order: list[str] = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role_name = handoff.role.value
            dispatch_order.append(role_name)
            verdict = "PASS" if role_name == "auditor" else "PASS"
            return _make_sub_agent_result(role_name, verdict=verdict)

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        assert dispatch_order == ["scout", "coder", "auditor"]


# ---------------------------------------------------------------------------
# 2. generate_file() no longer raises NotImplementedError
# ---------------------------------------------------------------------------


class TestGenerateFileNoLongerRaises:
    """After wiring, generate_file() delegates to run_builder() — no NotImplementedError."""

    @pytest.mark.asyncio
    async def test_generate_file_does_not_raise_not_implemented(self, tmp_path):
        from builder.builder_agent import BuilderResult
        from app.services.build.builder_agent import generate_file

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text("# generated\n", encoding="utf-8")

        mock_result = BuilderResult(
            file_path="app/main.py",
            content="# generated\n",
            status="completed",
        )

        with patch(
            "app.services.build.builder_agent.run_builder",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            content = await generate_file(
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
            )

        assert content == "# generated\n"
        assert isinstance(content, str)

    @pytest.mark.asyncio
    async def test_generate_file_raises_builder_error_on_failure(self, tmp_path):
        """generate_file() raises BuilderError (not NotImplementedError) on pipeline failure."""
        from builder.builder_agent import BuilderError, BuilderResult
        from app.services.build.builder_agent import generate_file

        mock_result = BuilderResult(
            file_path="app/main.py",
            content="",
            status="failed",
            error="Audit failed after max retries",
        )

        with patch(
            "app.services.build.builder_agent.run_builder",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            with pytest.raises(BuilderError, match="Builder failed for app/main.py"):
                await generate_file(
                    build_id=BUILD_ID,
                    user_id=USER_ID,
                    api_key=API_KEY,
                    file_entry=FILE_ENTRY,
                    contracts=[],
                    context=[],
                    phase_deliverables=[],
                    working_dir=str(tmp_path),
                )


# ---------------------------------------------------------------------------
# 3. stop_event interrupts the pipeline before the first sub-agent
# ---------------------------------------------------------------------------


class TestBuilderStopsOnStopEvent:
    """A pre-set stop_event raises BuilderError before dispatching SCOUT."""

    @pytest.mark.asyncio
    async def test_stop_event_set_before_start(self, tmp_path):
        from builder.builder_agent import BuilderError, run_builder

        stop = threading.Event()
        stop.set()  # set BEFORE calling run_builder

        dispatch_calls = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            dispatch_calls.append(handoff.role.value)
            return _make_sub_agent_result(handoff.role.value)

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            with pytest.raises(BuilderError, match="interrupted by stop signal"):
                await run_builder(
                    file_entry=FILE_ENTRY,
                    contracts=[],
                    context=[],
                    phase_deliverables=[],
                    working_dir=str(tmp_path),
                    build_id=BUILD_ID,
                    user_id=USER_ID,
                    api_key=API_KEY,
                    stop_event=stop,
                    verbose=False,
                )

        # SCOUT should NOT have been dispatched
        assert "scout" not in dispatch_calls

    @pytest.mark.asyncio
    async def test_stop_event_after_scout_returns_failed_result(self, tmp_path):
        """stop_event set between SCOUT and CODER → status=failed (not an exception)."""
        from builder.builder_agent import BuilderError, run_builder

        stop = threading.Event()
        call_idx = 0

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            nonlocal call_idx
            call_idx += 1
            result = _make_sub_agent_result(handoff.role.value)
            # Set stop after SCOUT completes
            if handoff.role.value == "scout":
                stop.set()
            return result

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                stop_event=stop,
                verbose=False,
            )

        assert result.status == "failed"
        assert "interrupted" in result.error
        assert call_idx == 1  # only SCOUT was dispatched


# ---------------------------------------------------------------------------
# 4. AUDITOR FAIL triggers FIXER dispatch
# ---------------------------------------------------------------------------


class TestAuditorFailTriggersFixer:
    """When AUDITOR returns FAIL, FIXER is dispatched and AUDITOR re-runs."""

    @pytest.mark.asyncio
    async def test_fixer_dispatched_on_audit_fail(self, tmp_path):
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n", encoding="utf-8")

        dispatch_order: list[str] = []
        auditor_call_count = 0

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            nonlocal auditor_call_count
            role = handoff.role.value
            dispatch_order.append(role)

            if role == "auditor":
                auditor_call_count += 1
                # First audit: FAIL; subsequent: PASS
                verdict = "FAIL" if auditor_call_count == 1 else "PASS"
                return _make_sub_agent_result(role, verdict=verdict)

            return _make_sub_agent_result(role, verdict="PASS")

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        assert result.status == "completed"
        assert "fixer" in dispatch_order, "FIXER should have been dispatched"
        assert dispatch_order.count("auditor") == 2, "AUDITOR should run twice (initial + re-audit)"
        # Order: scout, coder, auditor(fail), fixer, auditor(pass)
        assert dispatch_order == ["scout", "coder", "auditor", "fixer", "auditor"]

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_returns_failed(self, tmp_path):
        """If AUDITOR always FAILs, status=failed after max retries."""
        from builder.builder_agent import MAX_FIX_RETRIES, run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role = handoff.role.value
            verdict = "FAIL" if role == "auditor" else "PASS"
            return _make_sub_agent_result(role, verdict=verdict)

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        assert result.status == "failed"
        assert "Audit failed" in result.error


# ---------------------------------------------------------------------------
# 5. Token accounting accumulates across all sub-agent results
# ---------------------------------------------------------------------------


class TestTokenAccounting:
    """token_usage accumulates input + output tokens from all sub-agents."""

    @pytest.mark.asyncio
    async def test_tokens_accumulate_across_sub_agents(self, tmp_path):
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n", encoding="utf-8")

        sub_agent_tokens = {
            "scout":   (100, 50),
            "coder":   (200, 150),
            "auditor": (80, 40),
        }

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role = handoff.role.value
            inp, out = sub_agent_tokens.get(role, (10, 5))
            return _make_sub_agent_result(role, input_tokens=inp, output_tokens=out)

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        expected_input = 100 + 200 + 80
        expected_output = 50 + 150 + 40

        assert result.token_usage["input_tokens"] == expected_input
        assert result.token_usage["output_tokens"] == expected_output

    @pytest.mark.asyncio
    async def test_token_usage_includes_fixer_tokens(self, tmp_path):
        """FIXER tokens are included in the total when audit fails first."""
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n", encoding="utf-8")

        auditor_call_count = 0

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            nonlocal auditor_call_count
            role = handoff.role.value

            if role == "auditor":
                auditor_call_count += 1
                verdict = "FAIL" if auditor_call_count == 1 else "PASS"
                return _make_sub_agent_result(role, verdict=verdict, input_tokens=80, output_tokens=40)
            if role == "fixer":
                return _make_sub_agent_result(role, input_tokens=60, output_tokens=30)

            return _make_sub_agent_result(role, input_tokens=100, output_tokens=50)

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
            )

        # scout(100+50) + coder(100+50) + auditor_fail(80+40) + fixer(60+30) + auditor_pass(80+40)
        expected_input = 100 + 100 + 80 + 60 + 80
        expected_output = 50 + 50 + 40 + 30 + 40
        assert result.token_usage["input_tokens"] == expected_input
        assert result.token_usage["output_tokens"] == expected_output
        assert result.iterations == 5  # 5 sub-agent calls


# ---------------------------------------------------------------------------
# 6. File parallelism in execute_tier
# ---------------------------------------------------------------------------


def _make_builder_result(file_path: str, content: str = "# ok\n", status: str = "completed"):
    """Build a minimal BuilderResult for execute_tier tests."""
    from builder.builder_agent import BuilderResult
    return BuilderResult(
        file_path=file_path,
        content=content,
        status=status,
        token_usage={"input_tokens": 100, "output_tokens": 50},
        sub_agent_results=[],
        iterations=3,
    )


def _mock_planner_state(tmp_path):
    """Create a mock _state object with all async methods properly mocked."""
    mock_state = MagicMock()
    mock_state._broadcast_build_event = AsyncMock()
    mock_state.build_repo = MagicMock()
    mock_state.build_repo.append_build_log = AsyncMock()
    mock_state.build_repo.record_build_cost = AsyncMock()
    mock_state.FORGE_CONTRACTS_DIR = tmp_path
    mock_state._detect_language = MagicMock(return_value="python")
    return mock_state


class TestExecuteTierParallelism:
    """Verify semaphore caps concurrency at 3 and locks protect shared state."""

    @pytest.mark.asyncio
    async def test_semaphore_caps_at_3_concurrent(self, tmp_path):
        """With 5 files, at most 3 should run concurrently."""
        from app.services.build.planner import execute_tier

        tier_files = [
            {"path": f"src/file_{i}.py", "purpose": f"File {i}", "depends_on": [], "context_files": []}
            for i in range(5)
        ]

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_run_builder(*, file_entry, working_dir, build_id, user_id, api_key, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.05)  # Simulate work
            async with lock:
                current_concurrent -= 1
            return _make_builder_result(file_entry["path"])

        with patch("builder.builder_agent.run_builder", side_effect=mock_run_builder), \
             patch("app.services.build.planner._state", _mock_planner_state(tmp_path)), \
             patch("app.services.build.subagent.build_context_pack", return_value={}):

            written, lessons = await execute_tier(
                build_id=uuid4(),
                user_id=uuid4(),
                api_key="sk-test",
                tier_index=0,
                tier_files=tier_files,
                contracts=[],
                phase_deliverables="",
                working_dir=str(tmp_path),
                interface_map="",
                all_files_written={},
            )

        assert max_concurrent <= 3, f"Expected max 3 concurrent, got {max_concurrent}"
        assert max_concurrent >= 2, f"Expected at least 2 concurrent with 5 files, got {max_concurrent}"
        assert len(written) == 5

    @pytest.mark.asyncio
    async def test_tier_written_consistent_under_concurrency(self, tmp_path):
        """All completed files appear in tier_written even under concurrent writes."""
        from app.services.build.planner import execute_tier

        n_files = 6
        tier_files = [
            {"path": f"src/mod_{i}.py", "purpose": f"Module {i}", "depends_on": [], "context_files": []}
            for i in range(n_files)
        ]

        async def mock_run_builder(*, file_entry, working_dir, build_id, user_id, api_key, **kwargs):
            await asyncio.sleep(0.02)
            return _make_builder_result(file_entry["path"], content=f"# {file_entry['path']}\n")

        with patch("builder.builder_agent.run_builder", side_effect=mock_run_builder), \
             patch("app.services.build.planner._state", _mock_planner_state(tmp_path)), \
             patch("app.services.build.subagent.build_context_pack", return_value={}):

            written, lessons = await execute_tier(
                build_id=uuid4(),
                user_id=uuid4(),
                api_key="sk-test",
                tier_index=0,
                tier_files=tier_files,
                contracts=[],
                phase_deliverables="",
                working_dir=str(tmp_path),
                interface_map="",
                all_files_written={},
            )

        assert len(written) == n_files
        for i in range(n_files):
            assert f"src/mod_{i}.py" in written

    @pytest.mark.asyncio
    async def test_lessons_accumulate_under_concurrency(self, tmp_path):
        """Fixed findings from multiple concurrent files accumulate in lessons."""
        from app.services.build.planner import execute_tier
        from builder.builder_agent import BuilderResult

        tier_files = [
            {"path": f"src/svc_{i}.py", "purpose": f"Service {i}", "depends_on": [], "context_files": []}
            for i in range(4)
        ]

        async def mock_run_builder(*, file_entry, working_dir, build_id, user_id, api_key, **kwargs):
            await asyncio.sleep(0.02)
            return BuilderResult(
                file_path=file_entry["path"],
                content=f"# {file_entry['path']}\n",
                status="completed",
                token_usage={"input_tokens": 50, "output_tokens": 25},
                sub_agent_results=[],
                iterations=3,
                fixed_findings=f"fixed-import-in-{file_entry['path']}",
            )

        with patch("builder.builder_agent.run_builder", side_effect=mock_run_builder), \
             patch("app.services.build.planner._state", _mock_planner_state(tmp_path)), \
             patch("app.services.build.subagent.build_context_pack", return_value={}):

            written, lessons = await execute_tier(
                build_id=uuid4(),
                user_id=uuid4(),
                api_key="sk-test",
                tier_index=0,
                tier_files=tier_files,
                contracts=[],
                phase_deliverables="",
                working_dir=str(tmp_path),
                interface_map="",
                all_files_written={},
            )

        # All 4 files should have contributed lessons
        for i in range(4):
            assert f"fixed-import-in-src/svc_{i}.py" in lessons


# ---------------------------------------------------------------------------
# 7. Config-file skip logic
# ---------------------------------------------------------------------------


class TestConfigFileSkip:
    """Verify _is_config_file correctly identifies config/static files."""

    def test_is_config_file_true(self):
        from builder.builder_agent import _is_config_file

        config_files = [
            "tsconfig.json", "package.json", "Dockerfile",
            "docker-compose.yml", "pyproject.toml", "requirements.txt",
            ".gitignore", "vite.config.ts", "index.html",
            "Makefile", "Procfile", ".prettierrc",
        ]
        for f in config_files:
            assert _is_config_file(f), f"{f} should be detected as config"
            # Also test with directory prefix
            assert _is_config_file(f"web/{f}"), f"web/{f} should be detected as config"

    def test_is_config_file_false(self):
        from builder.builder_agent import _is_config_file

        source_files = [
            "app/main.py", "src/index.ts", "lib/utils.py",
            "api/routes/health.py", "components/App.tsx",
            "models/user.py", "tests/test_main.py",
        ]
        for f in source_files:
            assert not _is_config_file(f), f"{f} should NOT be detected as config"

    @pytest.mark.asyncio
    async def test_config_file_skips_scout(self, tmp_path):
        """Config files should skip SCOUT (only CODER + AUDITOR)."""
        from builder.builder_agent import run_builder

        fe = {
            "path": "tsconfig.json",
            "purpose": "TypeScript config",
            "estimated_lines": 15,
            "language": "json",
        }
        (tmp_path / "tsconfig.json").write_text(
            '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "module": "ESNext",\n    "strict": true\n  }\n}\n',
            encoding="utf-8",
        )

        dispatch_order: list[str] = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role = handoff.role.value
            dispatch_order.append(role)
            return _make_sub_agent_result(role, verdict="PASS")

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=fe,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
                phase_index=1,  # non-zero so only config check triggers skip
            )

        assert "scout" not in dispatch_order, "SCOUT should be skipped for config files"
        assert "coder" in dispatch_order, "CODER should still run"

    @pytest.mark.asyncio
    async def test_config_file_auto_passes_audit(self, tmp_path):
        """Config files should auto-pass audit (no AUDITOR sub-agent call)."""
        from builder.builder_agent import run_builder

        fe = {
            "path": "package.json",
            "purpose": "npm package manifest",
            "estimated_lines": 20,
            "language": "json",
        }
        (tmp_path / "package.json").write_text(
            '{\n  "name": "my-app",\n  "version": "1.0.0",\n  "dependencies": {\n    "react": "^18.2.0"\n  }\n}\n',
            encoding="utf-8",
        )

        dispatch_order: list[str] = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role = handoff.role.value
            dispatch_order.append(role)
            return _make_sub_agent_result(role, verdict="PASS",
                                          files_written=["package.json"] if role == "coder" else [])

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            result = await run_builder(
                file_entry=fe,
                contracts=[],
                context=[],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
                phase_index=1,
            )

        assert "auditor" not in dispatch_order, "AUDITOR should be skipped for config files"
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# 8. Token reporting — cache breakdown
# ---------------------------------------------------------------------------


class TestTokenReportingCache:
    """Verify cache_read_tokens and cache_creation_tokens accumulate correctly."""

    def test_accumulate_tokens_includes_cache(self):
        from builder.builder_agent import _accumulate_tokens
        from app.services.build.subagent import SubAgentResult, SubAgentRole

        total: dict[str, int] = {}
        r1 = SubAgentResult(handoff_id="h1", role=SubAgentRole.CODER)
        r1.input_tokens = 1000
        r1.output_tokens = 200
        r1.cache_read_tokens = 800
        r1.cache_creation_tokens = 100

        r2 = SubAgentResult(handoff_id="h2", role=SubAgentRole.AUDITOR)
        r2.input_tokens = 500
        r2.output_tokens = 100
        r2.cache_read_tokens = 400
        r2.cache_creation_tokens = 50

        _accumulate_tokens(total, r1)
        _accumulate_tokens(total, r2)

        assert total["input_tokens"] == 1500
        assert total["output_tokens"] == 300
        assert total["cache_read_tokens"] == 1200
        assert total["cache_creation_tokens"] == 150

    def test_print_summary_shows_cache_breakdown(self, capsys):
        from builder.builder_agent import BuilderResult, _print_summary

        result = BuilderResult(
            file_path="app/main.py",
            content="# ok\n",
            status="completed",
            token_usage={
                "input_tokens": 5000,
                "output_tokens": 1000,
                "cache_read_tokens": 4000,
                "cache_creation_tokens": 500,
            },
            iterations=3,
        )
        _print_summary(result)
        captured = capsys.readouterr()

        assert "fresh:" in captured.out
        assert "cached:" in captured.out
        assert "cache-create:" in captured.out
        # fresh = 5000 - 4000 - 500 = 500
        assert "500" in captured.out
        assert "4,000" in captured.out
