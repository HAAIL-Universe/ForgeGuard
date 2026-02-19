"""Tests for app.services.project.scout_contract_generator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.project.scout_contract_generator import (
    _format_scout_context_for_prompt,
    _generate_scout_contract_content,
    generate_contracts_from_scout,
)
from app.services.project.contract_generator import (
    CONTRACT_TYPES,
    _active_generations,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

USER_ID = uuid4()
PROJECT_ID = uuid4()
RUN_ID = uuid4()

_SAMPLE_STACK = {
    "primary_language": "Python",
    "backend": {
        "language": "Python",
        "language_version": "3.11",
        "framework": "FastAPI",
        "framework_version": "0.95.0",
        "orm": "SQLAlchemy",
        "database": "PostgreSQL",
    },
    "frontend": {
        "language": "TypeScript",
        "framework": "React",
        "bundler": "Vite",
    },
    "testing": {"framework": "pytest"},
}

_SAMPLE_ARCH = {
    "services": {"user_service.py": "file"},
    "repos": {"user_repo.py": "file"},
    "tests": {"test_users.py": "file"},
}

_SAMPLE_DOSSIER = {
    "executive_summary": "A FastAPI SaaS backend.",
    "intent": "Manage user subscriptions.",
    "quality_assessment": {
        "score": 72,
        "strengths": ["Good test coverage", "Clean routing"],
        "weaknesses": ["No type hints in services", "Outdated deps"],
    },
    "risk_areas": [
        {"area": "Security", "severity": "medium", "detail": "Secrets in .env"},
    ],
    "recommendations": [
        {"priority": "high", "suggestion": "Upgrade FastAPI to 0.110"},
    ],
}

_SAMPLE_CHECKS = [
    {"code": "A0", "name": "Contract Compliance", "result": "PASS", "detail": ""},
    {"code": "W1", "name": "Secrets", "result": "WARN", "detail": "found .env"},
]

_SAMPLE_METRICS = {"computed_score": 72, "test_ratio": 0.15}

_SAMPLE_RENOVATION = {
    "executive_brief": {
        "health_grade": "C",
        "headline": "Needs dependency upgrades",
        "top_priorities": ["Upgrade FastAPI", "Add type hints"],
        "risk_summary": "Will fall behind security patches.",
        "forge_automation_note": "FastAPI upgrade is automatable.",
    },
    "version_report": {
        "outdated": [
            {"name": "fastapi", "current_version": "0.95.0", "latest_version": "0.110.0"},
        ]
    },
    "migration_recommendations": [
        {
            "id": "TASK-1",
            "from_state": "fastapi 0.95",
            "to_state": "fastapi 0.110",
            "priority": "high",
            "effort": "low",
            "forge_automatable": True,
        }
    ],
}

_BASE_SCOUT_RESULTS = {
    "scan_type": "deep",
    "metadata": {"description": "A user management API", "language": "Python"},
    "stack_profile": _SAMPLE_STACK,
    "architecture": _SAMPLE_ARCH,
    "dossier": _SAMPLE_DOSSIER,
    "metrics": _SAMPLE_METRICS,
    "checks": _SAMPLE_CHECKS,
    "warnings": [],
    "tree_size": 42,
    "files_analysed": 15,
}

_PROJECT = {
    "id": PROJECT_ID,
    "user_id": USER_ID,
    "name": "TestProject",
    "repo_full_name": "user/repo",
    "questionnaire_state": None,
    "build_mode": "full",
}

_SCOUT_RUN = {
    "id": RUN_ID,
    "user_id": USER_ID,
    "repo_name": "user/repo",
    "scan_type": "deep",
    "status": "completed",
    "results": _BASE_SCOUT_RESULTS,
}


def _make_contract_row(ct: str) -> dict:
    return {
        "id": uuid4(),
        "project_id": PROJECT_ID,
        "contract_type": ct,
        "content": f"# {ct} content",
        "version": 1,
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-01-01T00:00:00",
    }


# ---------------------------------------------------------------------------
# _format_scout_context_for_prompt
# ---------------------------------------------------------------------------


def test_format_context_minimal():
    """Should not crash with minimal data."""
    result = _format_scout_context_for_prompt("user/repo", {}, None)
    assert "user/repo" in result


def test_format_context_includes_repo_name():
    result = _format_scout_context_for_prompt("user/my-repo", _BASE_SCOUT_RESULTS, None)
    assert "user/my-repo" in result


def test_format_context_includes_stack():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "FastAPI" in result
    assert "DETECTED TECH STACK" in result


def test_format_context_includes_arch():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "ARCHITECTURE MAP" in result
    assert "services" in result


def test_format_context_includes_dossier():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "FastAPI SaaS" in result
    assert "Manage user subscriptions" in result
    assert "Good test coverage" in result
    assert "Outdated deps" in result


def test_format_context_includes_quality_score():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "72/100" in result


def test_format_context_includes_checks():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "COMPLIANCE CHECKS" in result
    assert "A0" in result
    assert "W1" in result


def test_format_context_includes_metrics():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "QUALITY METRICS" in result
    assert "72/100" in result


def test_format_context_without_renovation_plan():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "RENOVATION PLAN" not in result


def test_format_context_with_renovation_plan():
    result = _format_scout_context_for_prompt(
        "r", _BASE_SCOUT_RESULTS, _SAMPLE_RENOVATION
    )
    assert "RENOVATION PLAN" in result
    assert "Health Grade: C" in result
    assert "Needs dependency upgrades" in result
    assert "fastapi: 0.95.0 → 0.110.0" in result
    assert "fastapi 0.95 → fastapi 0.110" in result


def test_format_context_arch_truncated():
    """Large architecture maps should be capped at 3000 chars."""
    big_arch = {"key_" + str(i): "value_" * 100 for i in range(50)}
    results = {**_BASE_SCOUT_RESULTS, "architecture": big_arch}
    result = _format_scout_context_for_prompt("r", results, None)
    assert "truncated" in result


def test_format_context_no_dossier():
    results = {**_BASE_SCOUT_RESULTS, "dossier": None}
    result = _format_scout_context_for_prompt("r", results, None)
    # Should not crash, and should not include dossier section
    assert "PROJECT DOSSIER" not in result


def test_format_context_tree_size():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "42" in result  # tree_size


def test_format_context_description_from_metadata():
    result = _format_scout_context_for_prompt("r", _BASE_SCOUT_RESULTS, None)
    assert "A user management API" in result


# ---------------------------------------------------------------------------
# _generate_scout_contract_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_scout_contract_content_happy_path():
    """Happy path: returns stripped content and usage."""
    with patch(
        "app.services.project.scout_contract_generator.llm_chat_streaming",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value = {
            "text": "# Manifesto content",
            "usage": {"input_tokens": 100, "output_tokens": 200},
        }
        content, usage = await _generate_scout_contract_content(
            contract_type="manifesto",
            repo_name="user/repo",
            scout_context="some context",
            api_key="key",
            model="claude-sonnet-4-5",
            provider="anthropic",
        )
    assert content == "# Manifesto content"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 200


@pytest.mark.asyncio
async def test_generate_scout_contract_content_strips_code_fences():
    with patch(
        "app.services.project.scout_contract_generator.llm_chat_streaming",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value = {
            "text": "```markdown\n# Manifesto\n```",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
        content, _ = await _generate_scout_contract_content(
            "manifesto", "repo", "ctx", "key", "model", "anthropic"
        )
    assert content == "# Manifesto"
    assert "```" not in content


@pytest.mark.asyncio
async def test_generate_scout_contract_content_error_fallback():
    """LLM failures return a fallback message instead of raising."""
    with patch(
        "app.services.project.scout_contract_generator.llm_chat_streaming",
        new_callable=AsyncMock,
        side_effect=RuntimeError("API down"),
    ):
        content, usage = await _generate_scout_contract_content(
            "stack", "repo", "ctx", "key", "model", "anthropic"
        )
    assert "Generation failed" in content
    assert usage == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.asyncio
async def test_generate_scout_contract_content_includes_prior_contracts():
    """Prior contracts are injected as snowball chain context."""
    captured_msgs = []

    async def fake_llm(**kwargs):
        captured_msgs.append(kwargs.get("messages", []))
        return {"text": "content", "usage": {"input_tokens": 5, "output_tokens": 5}}

    with patch(
        "app.services.project.scout_contract_generator.llm_chat_streaming",
        side_effect=fake_llm,
    ):
        await _generate_scout_contract_content(
            "stack",
            "repo",
            "ctx",
            "key",
            "model",
            "anthropic",
            prior_contracts={"manifesto": "manifesto content here"},
        )

    # The user message should contain the prior manifesto
    user_content = captured_msgs[0][0]["content"]
    assert "manifesto" in user_content.lower()
    assert "manifesto content here" in user_content


# ---------------------------------------------------------------------------
# generate_contracts_from_scout — validation errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_not_found_raises():
    with patch(
        "app.services.project.scout_contract_generator.get_project_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        with pytest.raises(ValueError, match="Project not found"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_project_wrong_user_raises():
    wrong_project = {**_PROJECT, "user_id": uuid4()}  # different user
    with patch(
        "app.services.project.scout_contract_generator.get_project_by_id",
        new_callable=AsyncMock,
        return_value=wrong_project,
    ):
        with pytest.raises(ValueError, match="Project not found"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_scout_run_not_found_raises():
    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        with pytest.raises(ValueError, match="Scout run not found"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_scout_run_wrong_user_raises():
    run = {**_SCOUT_RUN, "user_id": uuid4()}
    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=run,
        ),
    ):
        with pytest.raises(ValueError, match="Scout run not found"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_quick_scan_rejected():
    run = {**_SCOUT_RUN, "scan_type": "quick"}
    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=run,
        ),
    ):
        with pytest.raises(ValueError, match="deep-scan"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_run_not_completed_raises():
    run = {**_SCOUT_RUN, "status": "running"}
    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=run,
        ),
    ):
        with pytest.raises(ValueError, match="not completed"):
            await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)


@pytest.mark.asyncio
async def test_duplicate_generation_guard():
    _active_generations[str(PROJECT_ID)] = object()  # Simulate active run
    try:
        with (
            patch(
                "app.services.project.scout_contract_generator.get_project_by_id",
                new_callable=AsyncMock,
                return_value=_PROJECT,
            ),
            patch(
                "app.services.project.scout_contract_generator.get_scout_run",
                new_callable=AsyncMock,
                return_value=_SCOUT_RUN,
            ),
        ):
            with pytest.raises(ValueError, match="already in progress"):
                await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)
    finally:
        _active_generations.pop(str(PROJECT_ID), None)


# ---------------------------------------------------------------------------
# generate_contracts_from_scout — happy path
# ---------------------------------------------------------------------------


def _mock_llm_response(ct: str) -> dict:
    return {
        "text": f"# {ct} generated from scout",
        "usage": {"input_tokens": 50, "output_tokens": 100},
    }


@pytest.mark.asyncio
async def test_happy_path_generates_all_nine_contracts():
    """Full happy-path: all 9 contracts generated, returned, and stored."""
    _active_generations.pop(str(PROJECT_ID), None)

    ws_events = []

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
            side_effect=lambda uid, msg: ws_events.append(msg),
        ),
        patch(
            "app.services.project.scout_contract_generator.store_artifact",
        ) as mock_store,
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            side_effect=lambda **kw: _mock_llm_response(
                kw.get("messages", [{}])[0].get("content", "unknown")[:20]
            ),
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="test-key",
            LLM_QUESTIONNAIRE_MODEL="claude-sonnet-4-5",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        result = await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    assert len(result) == len(CONTRACT_TYPES)
    assert {r["contract_type"] for r in result} == set(CONTRACT_TYPES)

    # MCP store called once per contract
    assert mock_store.call_count == len(CONTRACT_TYPES)
    # Each call uses artifact_type="contract"
    for call in mock_store.call_args_list:
        assert call.kwargs.get("artifact_type") == "contract" or call.args[1] == "contract"


@pytest.mark.asyncio
async def test_ws_progress_events_sent():
    """WebSocket events include source='scout' and cover generating/done."""
    _active_generations.pop(str(PROJECT_ID), None)
    ws_events = []

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
            side_effect=lambda uid, msg: ws_events.append(msg),
        ),
        patch("app.services.project.scout_contract_generator.store_artifact"),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    payloads = [e["payload"] for e in ws_events if e.get("type") == "contract_progress"]
    # All payloads have source="scout"
    assert all(p["source"] == "scout" for p in payloads)
    # At least one generating + one done per contract
    statuses = [p["status"] for p in payloads]
    assert "generating" in statuses
    assert "done" in statuses


@pytest.mark.asyncio
async def test_resume_skips_existing_contracts():
    """Existing contracts are skipped without LLM calls."""
    _active_generations.pop(str(PROJECT_ID), None)

    # Return existing contracts for 3 types
    existing = [_make_contract_row(ct) for ct in CONTRACT_TYPES[:3]]
    # Add content to existing rows
    for row in existing:
        row["content"] = f"# {row['contract_type']} existing"

    ws_events = []

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
            side_effect=lambda uid, msg: ws_events.append(msg),
        ),
        patch("app.services.project.scout_contract_generator.store_artifact"),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "new content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ) as mock_llm,
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        result = await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    # Only 6 LLM calls (3 skipped)
    assert mock_llm.call_count == len(CONTRACT_TYPES) - 3

    # Resumed events flagged correctly
    resumed_payloads = [
        e["payload"]
        for e in ws_events
        if e.get("type") == "contract_progress"
        and e["payload"].get("resumed") is True
    ]
    assert len(resumed_payloads) == 3


@pytest.mark.asyncio
async def test_mcp_store_called_with_correct_args():
    """Each contract is stored with artifact_type='contract' and the contract type as key."""
    _active_generations.pop(str(PROJECT_ID), None)
    stored_calls = []

    def capture_store(**kwargs):
        stored_calls.append(kwargs)

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.store_artifact",
            side_effect=capture_store,
        ),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    stored_keys = {call["key"] for call in stored_calls}
    assert stored_keys == set(CONTRACT_TYPES)
    assert all(call["artifact_type"] == "contract" for call in stored_calls)
    assert all(call["project_id"] == str(PROJECT_ID) for call in stored_calls)
    assert all(call["persist"] is True for call in stored_calls)


@pytest.mark.asyncio
async def test_update_project_status_called():
    """project status is set to contracts_ready after successful generation."""
    _active_generations.pop(str(PROJECT_ID), None)
    status_calls = []

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
            side_effect=lambda pid, s: status_calls.append(s),
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
        ),
        patch("app.services.project.scout_contract_generator.store_artifact"),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    assert "contracts_ready" in status_calls


@pytest.mark.asyncio
async def test_results_as_json_string():
    """Scout run results stored as JSON string (not dict) should be parsed."""
    _active_generations.pop(str(PROJECT_ID), None)
    run_with_json_results = {
        **_SCOUT_RUN,
        "results": json.dumps(_BASE_SCOUT_RESULTS),
    }

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=run_with_json_results,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
        ),
        patch("app.services.project.scout_contract_generator.store_artifact"),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        result = await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    # Should not crash — results parsed from JSON string correctly
    assert len(result) == len(CONTRACT_TYPES)


@pytest.mark.asyncio
async def test_active_generation_cleaned_up_on_success():
    """_active_generations entry is removed after successful completion."""
    _active_generations.pop(str(PROJECT_ID), None)

    with (
        patch(
            "app.services.project.scout_contract_generator.get_project_by_id",
            new_callable=AsyncMock,
            return_value=_PROJECT,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_scout_run",
            new_callable=AsyncMock,
            return_value=_SCOUT_RUN,
        ),
        patch(
            "app.services.project.scout_contract_generator.get_contracts_by_project",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.project.scout_contract_generator.upsert_contract",
            new_callable=AsyncMock,
            side_effect=lambda pid, ct, content: _make_contract_row(ct),
        ),
        patch(
            "app.services.project.scout_contract_generator.update_project_status",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.project.scout_contract_generator.snapshot_contracts",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.project.scout_contract_generator.manager.send_to_user",
            new_callable=AsyncMock,
        ),
        patch("app.services.project.scout_contract_generator.store_artifact"),
        patch(
            "app.services.project.scout_contract_generator.llm_chat_streaming",
            new_callable=AsyncMock,
            return_value={
                "text": "content",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
        patch(
            "app.services.project.scout_contract_generator.settings",
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="key",
            LLM_QUESTIONNAIRE_MODEL="model",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
        ),
    ):
        await generate_contracts_from_scout(USER_ID, PROJECT_ID, RUN_ID)

    assert str(PROJECT_ID) not in _active_generations
