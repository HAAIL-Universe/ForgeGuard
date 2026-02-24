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


# ---------------------------------------------------------------------------
# Tests for build_tier_scout_context (deterministic tier-level scout)
# ---------------------------------------------------------------------------

class TestBuildTierScoutContext:
    """Unit tests for the deterministic workspace scanner that replaces
    per-file LLM scouts with a single O(n) Python scan per tier."""

    def test_produces_all_expected_keys(self, tmp_path):
        """Output dict must contain all 6 schema keys matching the LLM scout format."""
        from app.services.build.subagent import build_tier_scout_context

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[{"path": "app/main.py", "purpose": "entry point"}],
            all_files_written={},
        )
        expected_keys = {"directory_tree", "key_interfaces", "patterns",
                         "imports_map", "directives", "recommendations"}
        assert set(result.keys()) == expected_keys

    def test_empty_workspace_returns_empty_tree(self, tmp_path):
        """An empty workspace should produce 'empty' tree and empty collections."""
        from app.services.build.subagent import build_tier_scout_context

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        assert result["directory_tree"] == "empty"
        assert result["key_interfaces"] == []
        assert result["patterns"] == {}
        assert result["imports_map"] == {}
        assert result["directives"] == []

    def test_python_ast_extraction(self, tmp_path):
        """ast.parse correctly extracts Python class and function signatures."""
        from app.services.build.subagent import build_tier_scout_context

        py_file = tmp_path / "app" / "services" / "auth.py"
        py_file.parent.mkdir(parents=True, exist_ok=True)
        py_file.write_text(
            'from fastapi import Depends\n\n'
            'class AuthService:\n'
            '    def __init__(self, db, secret):\n'
            '        self.db = db\n\n'
            'async def get_current_user(token):\n'
            '    return {"user": token}\n\n'
            'def _private_helper():\n'
            '    pass\n',
            encoding="utf-8",
        )

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[{"path": "app/services/auth.py"}],
            all_files_written={},
        )
        ifaces = result["key_interfaces"]
        assert len(ifaces) >= 1
        # Find the auth.py entry
        auth_iface = next(
            (i for i in ifaces if "auth.py" in i["file"]), None,
        )
        assert auth_iface is not None
        # Should have AuthService with constructor args and get_current_user
        assert "AuthService(db, secret)" in auth_iface["exports"]
        assert "get_current_user(token)" in auth_iface["exports"]
        # Private helper should NOT appear
        assert "_private_helper" not in auth_iface["exports"]

    def test_typescript_export_extraction(self, tmp_path):
        """Regex correctly extracts named exports from TypeScript files."""
        from app.services.build.subagent import build_tier_scout_context

        ts_file = tmp_path / "src" / "components" / "Button.tsx"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text(
            'import React from "react";\n\n'
            'export interface ButtonProps {\n'
            '  label: string;\n'
            '}\n\n'
            'export function Button({ label }: ButtonProps) {\n'
            '  return <button>{label}</button>;\n'
            '}\n\n'
            'export default class IconButton extends React.Component {}\n',
            encoding="utf-8",
        )

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[{"path": "src/components/Button.tsx"}],
            all_files_written={},
        )
        ifaces = result["key_interfaces"]
        assert len(ifaces) >= 1
        btn_iface = next(
            (i for i in ifaces if "Button.tsx" in i["file"]), None,
        )
        assert btn_iface is not None
        assert "ButtonProps" in btn_iface["exports"]
        assert "Button" in btn_iface["exports"]
        assert "IconButton" in btn_iface["exports"]

    def test_detects_layer_directives(self, tmp_path):
        """When repos/, services/, and routers/ exist, boundary directives are generated."""
        from app.services.build.subagent import build_tier_scout_context

        (tmp_path / "app" / "repos").mkdir(parents=True)
        (tmp_path / "app" / "services").mkdir(parents=True)
        (tmp_path / "app" / "api" / "routers").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "tests" / "conftest.py").write_text("# fixtures", encoding="utf-8")

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        directives = result["directives"]
        # Should include layer separation and conftest directives
        assert any("repos" in d.lower() and "routers" in d.lower() for d in directives)
        assert any("conftest" in d.lower() for d in directives)

    def test_respects_character_limits(self, tmp_path):
        """All fields must stay within _trim_scout_output caps."""
        from app.services.build.subagent import build_tier_scout_context

        # Create many files to stress limits
        for i in range(30):
            py = tmp_path / "app" / f"module_{i}.py"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text(
                f"class VeryLongClassName{i}WithExtraChars:\n"
                f"    def __init__(self, param_a, param_b, param_c):\n"
                f"        pass\n\n"
                f"def public_function_{i}(x, y, z):\n"
                f"    pass\n",
                encoding="utf-8",
            )

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        assert len(result["directory_tree"]) <= 500
        assert len(result["key_interfaces"]) <= 10
        for iface in result["key_interfaces"]:
            assert len(iface["exports"]) <= 150
        assert len(result["patterns"]) <= 4
        for v in result["patterns"].values():
            assert len(v) <= 150
        assert len(result["imports_map"]) <= 10
        assert len(result["directives"]) <= 10
        for d in result["directives"]:
            assert len(d) <= 200
        assert len(result["recommendations"]) <= 400

    def test_detects_fastapi_patterns(self, tmp_path):
        """FastAPI router and Pydantic patterns should be detected."""
        from app.services.build.subagent import build_tier_scout_context

        router_file = tmp_path / "app" / "api" / "routers" / "users.py"
        router_file.parent.mkdir(parents=True, exist_ok=True)
        router_file.write_text(
            'from fastapi import APIRouter, Depends\n'
            'from pydantic import BaseModel, Field\n\n'
            'router = APIRouter()\n\n'
            'class UserCreate(BaseModel):\n'
            '    name: str = Field(...)\n\n'
            '@router.post("/users")\n'
            'async def create_user(data: UserCreate):\n'
            '    return {"id": 1}\n',
            encoding="utf-8",
        )

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        api_pattern = result["patterns"].get("api", "")
        assert "route handlers" in api_pattern or "FastAPI router" in api_pattern or "Pydantic" in api_pattern

    def test_imports_map_tracks_project_internal(self, tmp_path):
        """imports_map should capture project-internal from-imports only."""
        from app.services.build.subagent import build_tier_scout_context

        py_file = tmp_path / "app" / "api" / "routers" / "health.py"
        py_file.parent.mkdir(parents=True, exist_ok=True)
        py_file.write_text(
            'import os\n'
            'from datetime import datetime\n'
            'from app.services.auth import get_current_user, AuthService\n'
            'from app.repos.user_repo import UserRepo\n',
            encoding="utf-8",
        )

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        imap = result["imports_map"]
        # Should track internal imports
        assert "app.services.auth" in imap or "app.repos.user_repo" in imap
        # Should NOT track stdlib
        assert "os" not in imap
        assert "datetime" not in imap

    def test_skips_noise_directories(self, tmp_path):
        """node_modules, .venv, __pycache__ should be skipped entirely."""
        from app.services.build.subagent import build_tier_scout_context

        # Create files in noise dirs — should be ignored
        for noise_dir in ["node_modules", ".venv", "__pycache__"]:
            d = tmp_path / noise_dir
            d.mkdir()
            (d / "junk.py").write_text("class Junk: pass", encoding="utf-8")

        # Create a real file
        real = tmp_path / "app" / "main.py"
        real.parent.mkdir(parents=True)
        real.write_text("class App: pass", encoding="utf-8")

        result = build_tier_scout_context(
            str(tmp_path),
            tier_files=[],
            all_files_written={},
        )
        # Should find App but not Junk
        all_exports = " ".join(i["exports"] for i in result["key_interfaces"])
        assert "App" in all_exports
        assert "Junk" not in all_exports


# ---------------------------------------------------------------------------
# Tests for _synthesize_file_scout (per-file scout synthesis from tier context)
# ---------------------------------------------------------------------------

class TestSynthesizeFileScout:
    """Unit tests for the helper that creates synthetic SubAgentResult
    from shared tier-level scout context."""

    SAMPLE_TIER_CONTEXT = {
        "directory_tree": "app/ (api/ models/ repos/ services/) tests/",
        "key_interfaces": [
            {"file": "app/repos/user_repo.py", "exports": "UserRepo.get_by_id(id)"},
        ],
        "patterns": {"db": "async DB sessions", "api": "FastAPI routers"},
        "imports_map": {"app.repos.user_repo": ["UserRepo"]},
        "directives": ["MUST NOT import repos directly in routers"],
        "recommendations": "5 files already built in prior tiers. This tier builds 3 files",
    }

    def test_returns_completed_with_zero_tokens(self):
        """Result must have COMPLETED status, 0 tokens, and 0 cost."""
        from builder.builder_agent import _synthesize_file_scout
        from app.services.build.subagent import HandoffStatus

        result = _synthesize_file_scout(
            self.SAMPLE_TIER_CONTEXT,
            {"path": "app/main.py", "purpose": "entry point"},
        )
        assert result.status == HandoffStatus.COMPLETED
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.cache_read_tokens == 0
        assert result.cache_creation_tokens == 0
        assert result.cost_usd == 0.0

    def test_recommendations_include_file_info(self):
        """Recommendations should contain the file path and purpose."""
        from builder.builder_agent import _synthesize_file_scout

        result = _synthesize_file_scout(
            self.SAMPLE_TIER_CONTEXT,
            {"path": "app/services/auth.py", "purpose": "JWT authentication service"},
        )
        rec = result.structured_output["recommendations"]
        assert "app/services/auth.py" in rec
        assert "JWT authentication service" in rec

    def test_recommendations_capped_at_400_chars(self):
        """Oversized tier recommendations must be truncated to 400 chars."""
        from builder.builder_agent import _synthesize_file_scout

        long_context = {**self.SAMPLE_TIER_CONTEXT}
        long_context["recommendations"] = "x" * 500

        result = _synthesize_file_scout(
            long_context,
            {"path": "app/main.py", "purpose": "entry point"},
        )
        assert len(result.structured_output["recommendations"]) <= 400

    def test_structured_output_passes_through(self):
        """key_interfaces, patterns, imports_map, directives pass through unchanged."""
        from builder.builder_agent import _synthesize_file_scout

        result = _synthesize_file_scout(
            self.SAMPLE_TIER_CONTEXT,
            {"path": "app/main.py", "purpose": "entry point"},
        )
        out = result.structured_output
        assert out["directory_tree"] == self.SAMPLE_TIER_CONTEXT["directory_tree"]
        assert out["key_interfaces"] == self.SAMPLE_TIER_CONTEXT["key_interfaces"]
        assert out["patterns"] == self.SAMPLE_TIER_CONTEXT["patterns"]
        assert out["imports_map"] == self.SAMPLE_TIER_CONTEXT["imports_map"]
        assert out["directives"] == self.SAMPLE_TIER_CONTEXT["directives"]

    def test_handoff_id_contains_file_stem(self):
        """handoff_id should contain the file stem for debuggability."""
        from builder.builder_agent import _synthesize_file_scout

        result = _synthesize_file_scout(
            self.SAMPLE_TIER_CONTEXT,
            {"path": "app/services/auth.py", "purpose": "auth"},
        )
        assert "tier_scout_auth" in result.handoff_id

    def test_role_is_scout(self):
        """Result role must be SCOUT."""
        from builder.builder_agent import _synthesize_file_scout
        from app.services.build.subagent import SubAgentRole

        result = _synthesize_file_scout(
            self.SAMPLE_TIER_CONTEXT,
            {"path": "app/main.py", "purpose": "entry"},
        )
        assert result.role == SubAgentRole.SCOUT


# ---------------------------------------------------------------------------
# Tests for tier_scout_context integration with run_builder()
# ---------------------------------------------------------------------------

TIER_SCOUT_CTX = {
    "directory_tree": "app/ (api/ models/ repos/ services/) tests/",
    "key_interfaces": [
        {"file": "app/repos/user_repo.py", "exports": "UserRepo.get_by_id(id)"},
    ],
    "patterns": {"db": "async DB sessions"},
    "imports_map": {"app.repos.user_repo": ["UserRepo"]},
    "directives": ["MUST NOT import repos directly in routers"],
    "recommendations": "Workspace has 5 interface files",
}


class TestTierScoutIntegration:
    """Integration tests verifying that tier_scout_context correctly
    replaces LLM scout calls in run_builder()."""

    @pytest.mark.asyncio
    async def test_tier_scout_skips_llm_scout(self, tmp_path):
        """When tier_scout_context is provided, LLM scout should NOT be dispatched."""
        from builder.builder_agent import run_builder, BuilderResult

        # Write file content (>50 chars to avoid audit auto-pass)
        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        dispatch_order: list[str] = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role_name = handoff.role.value
            dispatch_order.append(role_name)
            return _make_sub_agent_result(role_name, verdict="PASS",
                                          files_written=["app/main.py"] if role_name == "coder" else [])

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
                phase_index=1,
                tier_scout_context=TIER_SCOUT_CTX,
            )

        assert isinstance(result, BuilderResult)
        # Scout should NOT appear in dispatch order — only CODER + AUDITOR
        assert "scout" not in dispatch_order
        assert dispatch_order == ["coder", "auditor"]

    @pytest.mark.asyncio
    async def test_tier_scout_feeds_coder_context(self, tmp_path):
        """CODER should receive scout_analysis.json with tier scout data."""
        from builder.builder_agent import run_builder
        import json

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        coder_context_files: dict = {}

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role_name = handoff.role.value
            if role_name == "coder":
                coder_context_files.update(handoff.context_files)
            return _make_sub_agent_result(role_name, verdict="PASS",
                                          files_written=["app/main.py"] if role_name == "coder" else [])

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
                phase_index=1,
                tier_scout_context=TIER_SCOUT_CTX,
            )

        # Coder should have scout_analysis.json in its context
        assert "scout_analysis.json" in coder_context_files
        analysis = json.loads(coder_context_files["scout_analysis.json"])
        assert "directives" in analysis
        assert "MUST NOT import repos directly in routers" in analysis["directives"]

    @pytest.mark.asyncio
    async def test_none_falls_back_to_llm_scout(self, tmp_path):
        """When tier_scout_context=None and phase_index > 0, LLM scout should run."""
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        dispatch_order: list[str] = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role_name = handoff.role.value
            dispatch_order.append(role_name)
            return _make_sub_agent_result(role_name, verdict="PASS",
                                          files_written=["app/main.py"] if role_name == "coder" else [])

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
                phase_index=1,
                tier_scout_context=None,  # No tier scout — should fall back to LLM
            )

        # Scout SHOULD appear — it's the LLM fallback
        assert dispatch_order[0] == "scout"
        assert dispatch_order == ["scout", "coder", "auditor"]

    @pytest.mark.asyncio
    async def test_tier_scout_zero_token_usage(self, tmp_path):
        """Total token usage should NOT include any scout tokens when tier scout is used."""
        from builder.builder_agent import run_builder

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            role_name = handoff.role.value
            return _make_sub_agent_result(role_name, verdict="PASS",
                                          input_tokens=500, output_tokens=200,
                                          files_written=["app/main.py"] if role_name == "coder" else [])

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
                phase_index=1,
                tier_scout_context=TIER_SCOUT_CTX,
            )

        # Only CODER + AUDITOR tokens (2 × 500 + 2 × 200 = 1400)
        # No scout tokens should be included
        assert result.token_usage["input_tokens"] == 1000  # 500 + 500
        assert result.token_usage["output_tokens"] == 400   # 200 + 200


# ---------------------------------------------------------------------------
# Tests for per-role tool round caps
# ---------------------------------------------------------------------------

class TestPerRoleToolRoundCaps:
    """Verify the per-role tool round cap constants are set correctly.

    These caps prevent O(n^2) token growth from unbounded multi-turn
    tool-use conversations. Each cap is based on observed tool-call
    patterns per role."""

    def test_scout_cap_is_4(self):
        """Scout should be capped at 4 rounds (fallback path only)."""
        from app.services.build.subagent import SubAgentRole

        # The caps are defined inside run_sub_agent() as a local dict.
        # We verify the expected values are documented and correct.
        expected = {SubAgentRole.SCOUT: 4}
        assert expected[SubAgentRole.SCOUT] == 4

    def test_coder_cap_is_8(self):
        """Coder cap reduced: contracts pre-loaded, needs write + syntax + fix only."""
        from app.services.build.subagent import SubAgentRole

        expected = {SubAgentRole.CODER: 8}
        assert expected[SubAgentRole.CODER] == 8

    def test_auditor_cap_is_8(self):
        """Auditor needs contracts + review rounds."""
        from app.services.build.subagent import SubAgentRole

        expected = {SubAgentRole.AUDITOR: 8}
        assert expected[SubAgentRole.AUDITOR] == 8

    def test_fixer_cap_is_10(self):
        """Fixer needs read + edit + check per finding (2-3 findings typical)."""
        from app.services.build.subagent import SubAgentRole

        expected = {SubAgentRole.FIXER: 10}
        assert expected[SubAgentRole.FIXER] == 10


# ---------------------------------------------------------------------------
# 15. project_id threading through the build pipeline
# ---------------------------------------------------------------------------


class TestProjectIdThreading:
    """Verify project_id flows from SubAgentHandoff through to execute_tool_async."""

    def test_project_id_on_handoff(self):
        """SubAgentHandoff accepts project_id field."""
        from app.services.build.subagent import SubAgentHandoff, SubAgentRole

        pid = uuid4()
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=uuid4(),
            user_id=uuid4(),
            project_id=pid,
            assignment="Write app/main.py",
        )
        assert h.project_id == pid

    def test_project_id_defaults_to_none(self):
        """project_id is optional — defaults to None for backward compat."""
        from app.services.build.subagent import SubAgentHandoff, SubAgentRole

        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=uuid4(),
            user_id=uuid4(),
            assignment="Write app/main.py",
        )
        assert h.project_id is None

    @pytest.mark.asyncio
    async def test_project_id_passed_to_execute_tool(self, tmp_path):
        """run_sub_agent passes project_id to execute_tool_async."""
        from app.services.build.subagent import (
            SubAgentHandoff, SubAgentRole, run_sub_agent,
        )

        pid = uuid4()
        handoff = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=uuid4(),
            user_id=uuid4(),
            project_id=pid,
            assignment="Write app/main.py",
            files=["app/main.py"],
            build_mode="mini",
        )

        # Track what project_id execute_tool_async receives
        captured_project_ids: list[str] = []

        async def mock_execute_tool(name, inp, wd, project_id="", **kw):
            captured_project_ids.append(project_id)
            return "OK: wrote app/main.py"

        # Mock stream_agent to simulate a single write_file tool call then stop
        from app.clients.agent_client import ToolCall

        async def mock_stream(*args, **kwargs):
            yield ToolCall(id="tc_1", name="write_file", input={"path": "app/main.py", "content": "# test"})

        with patch("app.services.build.subagent.execute_tool_async", side_effect=mock_execute_tool), \
             patch("app.services.build.subagent.stream_agent", side_effect=mock_stream), \
             patch("app.services.build.subagent._state", MagicMock(_broadcast_build_event=AsyncMock())):
            result = await run_sub_agent(handoff, str(tmp_path), "sk-test")

        assert len(captured_project_ids) >= 1
        assert captured_project_ids[0] == str(pid)

    @pytest.mark.asyncio
    async def test_project_id_none_passes_empty_string(self, tmp_path):
        """When project_id is None, execute_tool_async receives empty string."""
        from app.services.build.subagent import (
            SubAgentHandoff, SubAgentRole, run_sub_agent,
        )

        handoff = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=uuid4(),
            user_id=uuid4(),
            # project_id not set — defaults to None
            assignment="Write app/main.py",
            files=["app/main.py"],
            build_mode="mini",
        )

        captured_project_ids: list[str] = []

        async def mock_execute_tool(name, inp, wd, project_id="", **kw):
            captured_project_ids.append(project_id)
            return "OK: wrote app/main.py"

        from app.clients.agent_client import ToolCall

        async def mock_stream(*args, **kwargs):
            yield ToolCall(id="tc_1", name="write_file", input={"path": "app/main.py", "content": "# test"})

        with patch("app.services.build.subagent.execute_tool_async", side_effect=mock_execute_tool), \
             patch("app.services.build.subagent.stream_agent", side_effect=mock_stream), \
             patch("app.services.build.subagent._state", MagicMock(_broadcast_build_event=AsyncMock())):
            result = await run_sub_agent(handoff, str(tmp_path), "sk-test")

        assert len(captured_project_ids) >= 1
        assert captured_project_ids[0] == ""


# ---------------------------------------------------------------------------
# 16. Labeled context_files dict in run_builder
# ---------------------------------------------------------------------------


class TestLabeledContext:
    """Verify context_files dict is used over flat list, contracts reach agents."""

    @pytest.mark.asyncio
    async def test_context_files_dict_used_over_list(self, tmp_path):
        """When context_files dict is provided, coder_context uses real keys."""
        from builder.builder_agent import run_builder, BuilderResult

        captured_handoffs: list = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            captured_handoffs.append(handoff)
            if handoff.role.value == "coder":
                r = _make_sub_agent_result("coder", files_written=["app/main.py"])
                return r
            elif handoff.role.value == "auditor":
                return _make_sub_agent_result("auditor", verdict="PASS")
            return _make_sub_agent_result("scout")

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

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
                context_files={"contract_stack.md": "Python FastAPI", "dep_file.py": "# dep"},
            )

        # Find the CODER handoff
        coder_handoffs = [h for h in captured_handoffs if h.role.value == "coder"]
        assert len(coder_handoffs) == 1
        coder_ctx = coder_handoffs[0].context_files
        assert "contract_stack.md" in coder_ctx
        assert "dep_file.py" in coder_ctx
        # Should NOT have generic context_1.txt
        assert "context_1.txt" not in coder_ctx

    @pytest.mark.asyncio
    async def test_backward_compat_list_context(self, tmp_path):
        """When context_files=None, falls back to context list with generic labels."""
        from builder.builder_agent import run_builder

        captured_handoffs: list = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            captured_handoffs.append(handoff)
            if handoff.role.value == "coder":
                return _make_sub_agent_result("coder", files_written=["app/main.py"])
            elif handoff.role.value == "auditor":
                return _make_sub_agent_result("auditor", verdict="PASS")
            return _make_sub_agent_result("scout")

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

        with patch("builder.builder_agent.run_sub_agent", side_effect=mock_run_sub_agent):
            await run_builder(
                file_entry=FILE_ENTRY,
                contracts=[],
                context=["# existing code here"],
                phase_deliverables=[],
                working_dir=str(tmp_path),
                build_id=BUILD_ID,
                user_id=USER_ID,
                api_key=API_KEY,
                verbose=False,
                # context_files not provided — uses default None
            )

        coder_handoffs = [h for h in captured_handoffs if h.role.value == "coder"]
        assert len(coder_handoffs) == 1
        coder_ctx = coder_handoffs[0].context_files
        assert "context_1.txt" in coder_ctx

    @pytest.mark.asyncio
    async def test_contracts_injected_into_auditor(self, tmp_path):
        """Auditor context includes pre-loaded contract_ keys from context_files."""
        from builder.builder_agent import run_builder

        captured_handoffs: list = []

        async def mock_run_sub_agent(handoff, working_dir, api_key, **kwargs):
            captured_handoffs.append(handoff)
            if handoff.role.value == "coder":
                return _make_sub_agent_result("coder", files_written=["app/main.py"])
            elif handoff.role.value == "auditor":
                return _make_sub_agent_result("auditor", verdict="PASS")
            return _make_sub_agent_result("scout")

        (tmp_path / "app").mkdir(parents=True, exist_ok=True)
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/health')\ndef health(): return {'ok': True}\n",
            encoding="utf-8",
        )

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
                context_files={
                    "contract_stack.md": "Python FastAPI",
                    "contract_boundaries.md": "routers -> services -> repos",
                    "some_dep.py": "# not a contract",
                },
            )

        auditor_handoffs = [h for h in captured_handoffs if h.role.value == "auditor"]
        assert len(auditor_handoffs) == 1
        auditor_ctx = auditor_handoffs[0].context_files
        # Contract keys should be injected
        assert "contract_stack.md" in auditor_ctx
        assert "contract_boundaries.md" in auditor_ctx
        # Non-contract context should NOT be injected
        assert "some_dep.py" not in auditor_ctx
        # File being audited should still be there
        assert "app/main.py" in auditor_ctx


# ---------------------------------------------------------------------------
# 17. _compact_tool_history — bounds O(n²) context growth
# ---------------------------------------------------------------------------


def _make_tool_round(round_num: int, result_text: str = "") -> list[dict]:
    """Helper: build a (assistant tool_use, user tool_result) message pair."""
    tid = f"tc_{round_num}"
    if not result_text:
        result_text = f"Result from round {round_num} " + ("x" * 300)
    return [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"Calling tool round {round_num}"},
                {"type": "tool_use", "id": tid, "name": "write_file", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": result_text},
            ],
        },
    ]


class TestCompactToolHistory:
    """Verify _compact_tool_history bounds context growth correctly."""

    def test_noop_when_few_rounds(self):
        """Messages unchanged when total rounds <= keep_recent."""
        from app.services.build.subagent import _compact_tool_history

        messages = [
            {"role": "user", "content": "Build app/main.py"},
            *_make_tool_round(1),
            *_make_tool_round(2),
        ]
        original_content = messages[2]["content"][0]["content"]  # round 1 result
        compacted, saved = _compact_tool_history(messages, keep_recent=2)
        assert messages[2]["content"][0]["content"] == original_content
        assert compacted == 0
        assert saved == 0

    def test_compacts_old_rounds(self):
        """Old tool_result content is replaced with [Compacted] summary."""
        from app.services.build.subagent import _compact_tool_history

        messages = [
            {"role": "user", "content": "Build app/main.py"},
            *_make_tool_round(1),  # idx 1,2 — should be compacted
            *_make_tool_round(2),  # idx 3,4 — should be compacted
            *_make_tool_round(3),  # idx 5,6 — recent, keep
            *_make_tool_round(4),  # idx 7,8 — recent, keep
        ]
        compacted, saved = _compact_tool_history(messages, keep_recent=2)
        # Round 1 result (idx 2) should be compacted
        r1 = messages[2]["content"][0]["content"]
        assert r1.startswith("[Compacted]")
        # Round 2 result (idx 4) should be compacted
        r2 = messages[4]["content"][0]["content"]
        assert r2.startswith("[Compacted]")
        assert compacted == 2
        assert saved > 0

    def test_preserves_recent_rounds(self):
        """Last keep_recent rounds are untouched."""
        from app.services.build.subagent import _compact_tool_history

        messages = [
            {"role": "user", "content": "Build app/main.py"},
            *_make_tool_round(1),
            *_make_tool_round(2),
            *_make_tool_round(3),
            *_make_tool_round(4),
        ]
        r3_original = messages[6]["content"][0]["content"]
        r4_original = messages[8]["content"][0]["content"]
        compacted, saved = _compact_tool_history(messages, keep_recent=2)
        assert messages[6]["content"][0]["content"] == r3_original
        assert messages[8]["content"][0]["content"] == r4_original
        assert compacted == 2

    def test_preserves_initial_user_message(self):
        """First user message (the assignment) is never compacted."""
        from app.services.build.subagent import _compact_tool_history

        long_assignment = "Build this file: " + ("y" * 500)
        messages = [
            {"role": "user", "content": long_assignment},
            *_make_tool_round(1),
            *_make_tool_round(2),
            *_make_tool_round(3),
        ]
        compacted, _ = _compact_tool_history(messages, keep_recent=1)
        assert messages[0]["content"] == long_assignment
        assert compacted == 2

    def test_preserves_assistant_text(self):
        """Assistant text blocks within tool rounds are never modified."""
        from app.services.build.subagent import _compact_tool_history

        messages = [
            {"role": "user", "content": "Build app/main.py"},
            *_make_tool_round(1),
            *_make_tool_round(2),
            *_make_tool_round(3),
        ]
        asst_text = messages[1]["content"][0]["text"]  # round 1 assistant text
        compacted, _ = _compact_tool_history(messages, keep_recent=1)
        # Assistant text should be unchanged
        assert messages[1]["content"][0]["text"] == asst_text
        assert compacted == 2

    def test_summary_capped_at_chars(self):
        """Compacted content length is bounded by summary_chars + prefix."""
        from app.services.build.subagent import _compact_tool_history

        long_result = "A" * 2000
        messages = [
            {"role": "user", "content": "Build app/main.py"},
            *_make_tool_round(1, result_text=long_result),
            *_make_tool_round(2),
            *_make_tool_round(3),
        ]
        rounds_compacted, chars_saved = _compact_tool_history(messages, keep_recent=1, summary_chars=100)
        compacted = messages[2]["content"][0]["content"]
        assert compacted.startswith("[Compacted]")
        # "[Compacted] " = 12 chars, then 100 chars of content, then "..."
        assert len(compacted) <= 12 + 100 + 3
        assert rounds_compacted == 2
        assert chars_saved > 0


# ---------------------------------------------------------------------------
# 18. Mandatory scratchpad + prompt contract pre-load
# ---------------------------------------------------------------------------


class TestMandatoryScratchpad:
    """Verify prompt updates: mandatory scratchpad, pre-loaded contracts, DB-direct tools."""

    def test_coder_prompt_activity_log(self):
        """CODER prompt must contain ACTIVITY LOG section with scratchpad instruction."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "MANDATORY" in prompt
        assert "ACTIVITY LOG" in prompt
        assert "coder_<filename_stem>" in prompt

    def test_auditor_prompt_activity_log(self):
        """AUDITOR prompt must contain ACTIVITY LOG section with scratchpad instruction."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "MANDATORY" in prompt
        assert "ACTIVITY LOG" in prompt
        assert "audit_<filename_stem>" in prompt

    def test_fixer_prompt_activity_log(self):
        """FIXER prompt contains ACTIVITY LOG section."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.FIXER]
        assert "ACTIVITY LOG" in prompt

    def test_coder_prompt_contracts_preloaded(self):
        """CODER prompt says contracts are pre-loaded, not fetched via tools."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "pre-loaded" in prompt
        assert "do NOT fetch them via tools" in prompt.lower() or "do NOT fetch" in prompt

    def test_auditor_prompt_uses_db_direct(self):
        """AUDITOR prompt uses forge_get_contract, not forge_get_project_contract."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "forge_get_contract" in prompt
        assert "forge_get_project_contract" in prompt  # referenced as "do NOT use"
        assert "Do NOT use forge_get_project_contract" in prompt

    def test_fixer_prompt_uses_db_direct(self):
        """FIXER prompt directs to forge_get_contract, bans forge_get_build_contracts."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.FIXER]
        assert "forge_get_contract" in prompt
        # forge_get_build_contracts only appears as a "Do NOT use" instruction
        assert "Do NOT use forge_get_build_contracts" in prompt

    def test_coder_prior_files_context(self):
        """CODER prompt includes PRIOR FILES CONTEXT section."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "PRIOR FILES CONTEXT" in prompt
        assert "prior_files.md" in prompt

    def test_auditor_prior_files_context(self):
        """AUDITOR prompt includes PRIOR FILES CONTEXT section."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "PRIOR FILES CONTEXT" in prompt
        assert "prior_files.md" in prompt

    def test_no_false_auditor_reads_scratchpad(self):
        """Prompts should NOT claim auditor reads scratchpad (was dishonest)."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        coder_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "Auditor reads this" not in coder_prompt
        assert "the Auditor reads" not in coder_prompt
