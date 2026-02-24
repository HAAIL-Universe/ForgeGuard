"""Tests for app.services.build.pipeline_state — typed pipeline state infrastructure.

Covers:
  - Reducer functions (_append_list, _merge_dict, _append_capped)
  - PipelineStateManager (apply_update, scoped_read, snapshot)
  - Role-scoped read keys
  - _state_to_context_files() bridge
  - _extract_exports() parser
  - HandoffData + handoff filters
  - _extract_lessons_from_result() + _lessons_to_context()
  - make_empty_lessons()
"""

import json
import pytest

from app.services.build.pipeline_state import (
    # Reducers
    _append_list,
    _merge_dict,
    _append_capped,
    # State classes
    FilePipelineState,
    TierState,
    LessonsState,
    # State manager
    PipelineStateManager,
    _extract_reducers,
    # Role scopes
    SCOUT_READ_KEYS,
    CODER_READ_KEYS,
    AUDITOR_READ_KEYS,
    FIXER_READ_KEYS,
    # Context bridge
    _state_to_context_files,
    _extract_exports,
    # Handoff filters
    HandoffData,
    strip_tool_calls,
    strip_raw_text,
    cap_prior_output,
    compose_filters,
    SCOUT_TO_CODER_FILTER,
    CODER_TO_AUDITOR_FILTER,
    AUDITOR_TO_FIXER_FILTER,
    # Lessons
    make_empty_lessons,
    _extract_lessons_from_result,
    _lessons_to_context,
)


# ---------------------------------------------------------------------------
# Reducer tests
# ---------------------------------------------------------------------------

class TestReducers:
    def test_append_list(self):
        assert _append_list([1, 2], [3, 4]) == [1, 2, 3, 4]

    def test_append_list_empty(self):
        assert _append_list([], [1]) == [1]
        assert _append_list([1], []) == [1]

    def test_merge_dict(self):
        result = _merge_dict({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_merge_dict_empty(self):
        assert _merge_dict({}, {"a": 1}) == {"a": 1}
        assert _merge_dict({"a": 1}, {}) == {"a": 1}

    def test_append_capped(self):
        reducer = _append_capped(3)
        assert reducer([1, 2], [3, 4]) == [2, 3, 4]

    def test_append_capped_under_cap(self):
        reducer = _append_capped(10)
        assert reducer([1], [2]) == [1, 2]

    def test_append_capped_exact_cap(self):
        reducer = _append_capped(3)
        assert reducer([1], [2, 3]) == [1, 2, 3]


# ---------------------------------------------------------------------------
# _extract_reducers tests
# ---------------------------------------------------------------------------

class TestExtractReducers:
    def test_file_pipeline_state_reducers(self):
        reducers = _extract_reducers(FilePipelineState)
        # Annotated fields should have reducers
        assert reducers["audit_findings"] is _append_list
        assert reducers["fixes_applied"] is _append_list
        assert reducers["integration_findings"] is _append_list
        assert reducers["prior_file_summaries"] is _append_list
        assert reducers["artifact_trail"] is _merge_dict
        # errors uses _append_capped(20) — a factory-created function
        assert reducers["errors"] is not None
        assert callable(reducers["errors"])

        # Non-annotated fields should have None (overwrite)
        assert reducers["file_path"] is None
        assert reducers["scout_analysis"] is None
        assert reducers["generated_code"] is None

    def test_lessons_state_reducers(self):
        reducers = _extract_reducers(LessonsState)
        assert reducers["import_conventions"] is _merge_dict
        assert callable(reducers["confirmed_patterns"])
        assert callable(reducers["fix_patterns"])

    def test_tier_state_reducers(self):
        reducers = _extract_reducers(TierState)
        assert reducers["completed_files"] is _append_list


# ---------------------------------------------------------------------------
# PipelineStateManager tests
# ---------------------------------------------------------------------------

class TestPipelineStateManager:
    def _make_mgr(self, **initial):
        defaults = {
            "file_path": "app/main.py",
            "file_entry": {"path": "app/main.py", "purpose": "Entry point"},
            "build_id": "test-build",
            "project_id": "test-project",
            "phase_index": 0,
            "contracts": {},
            "phase_deliverables": "- Health endpoint",
            "audit_findings": [],
            "fixes_applied": [],
            "integration_findings": [],
            "prior_file_summaries": [],
            "artifact_trail": {},
            "errors": [],
        }
        defaults.update(initial)
        return PipelineStateManager(FilePipelineState, defaults)

    def test_initial_state(self):
        mgr = self._make_mgr()
        assert mgr.state["file_path"] == "app/main.py"
        assert mgr.state["audit_findings"] == []

    def test_overwrite_semantics(self):
        mgr = self._make_mgr()
        mgr.apply_update({"file_path": "app/other.py"})
        assert mgr.state["file_path"] == "app/other.py"

    def test_append_semantics(self):
        mgr = self._make_mgr()
        mgr.apply_update({"audit_findings": [{"line": 1, "severity": "error", "message": "bad"}]})
        mgr.apply_update({"audit_findings": [{"line": 2, "severity": "warn", "message": "meh"}]})
        assert len(mgr.state["audit_findings"]) == 2

    def test_merge_semantics(self):
        mgr = self._make_mgr()
        mgr.apply_update({"artifact_trail": {"a.py": "created"}})
        mgr.apply_update({"artifact_trail": {"b.py": "created", "a.py": "modified"}})
        trail = mgr.state["artifact_trail"]
        assert trail == {"a.py": "modified", "b.py": "created"}

    def test_capped_append(self):
        mgr = self._make_mgr()
        # errors is capped at 20
        for i in range(25):
            mgr.apply_update({"errors": [{"phase": "test", "message": f"err-{i}", "resolved": False}]})
        assert len(mgr.state["errors"]) == 20
        assert mgr.state["errors"][-1]["message"] == "err-24"
        assert mgr.state["errors"][0]["message"] == "err-5"

    def test_scoped_read(self):
        mgr = self._make_mgr(
            scout_analysis={"tree": "..."},
            generated_code="print('hi')",
        )
        coder_view = mgr.scoped_read(CODER_READ_KEYS)
        assert "scout_analysis" in coder_view
        assert "file_path" in coder_view
        # generated_code is not in CODER_READ_KEYS
        assert "generated_code" not in coder_view

    def test_scoped_read_auditor(self):
        mgr = self._make_mgr(generated_code="print('hi')")
        auditor_view = mgr.scoped_read(AUDITOR_READ_KEYS)
        assert "generated_code" in auditor_view
        # scout_analysis is not in AUDITOR_READ_KEYS
        assert "scout_analysis" not in auditor_view

    def test_snapshot_is_deep_copy(self):
        mgr = self._make_mgr()
        snap = mgr.snapshot()
        snap["audit_findings"].append({"line": 99})
        assert mgr.state["audit_findings"] == []

    def test_state_is_shallow_copy(self):
        mgr = self._make_mgr()
        view = mgr.state
        view["file_path"] = "changed"
        # Original should NOT be changed (shallow copy of top-level)
        assert mgr.state["file_path"] == "app/main.py"

    def test_apply_update_new_key(self):
        mgr = self._make_mgr()
        mgr.apply_update({"scout_analysis": {"tree": "abc"}})
        assert mgr.state["scout_analysis"]["tree"] == "abc"


# ---------------------------------------------------------------------------
# Role scope tests
# ---------------------------------------------------------------------------

class TestRoleScopes:
    def test_scout_scope_minimal(self):
        assert "file_path" in SCOUT_READ_KEYS
        assert "contracts" in SCOUT_READ_KEYS
        assert "generated_code" not in SCOUT_READ_KEYS
        assert "audit_findings" not in SCOUT_READ_KEYS

    def test_coder_scope_includes_scout(self):
        assert "scout_analysis" in CODER_READ_KEYS
        assert "scout_directives" in CODER_READ_KEYS
        assert "audit_findings" not in CODER_READ_KEYS

    def test_auditor_scope_includes_code(self):
        assert "generated_code" in AUDITOR_READ_KEYS
        assert "coder_decisions" in AUDITOR_READ_KEYS
        assert "scout_analysis" not in AUDITOR_READ_KEYS

    def test_fixer_scope_includes_findings(self):
        assert "audit_findings" in FIXER_READ_KEYS
        assert "generated_code" in FIXER_READ_KEYS
        assert "scout_analysis" not in FIXER_READ_KEYS
        assert "prior_file_summaries" not in FIXER_READ_KEYS


# ---------------------------------------------------------------------------
# _state_to_context_files tests
# ---------------------------------------------------------------------------

class TestStateToContextFiles:
    def test_empty_state(self):
        ctx = _state_to_context_files({})
        assert ctx == {}

    def test_contracts_pass_through(self):
        ctx = _state_to_context_files({
            "contracts": {"contract_stack.md": "# Stack\nPython 3.12"},
        })
        assert "contract_stack.md" in ctx

    def test_scout_analysis_serialized(self):
        ctx = _state_to_context_files({
            "scout_analysis": {"tree": "..."},
            "scout_directives": ["MUST use async"],
            "scout_interfaces": [{"file": "app/main.py", "exports": "app"}],
            "scout_patterns": {"db": "SQLAlchemy"},
            "scout_imports_map": {"app.main": ["app"]},
        })
        assert "scout_analysis.json" in ctx
        data = json.loads(ctx["scout_analysis.json"])
        assert data["directives"] == ["MUST use async"]

    def test_generated_code_uses_file_path(self):
        ctx = _state_to_context_files({
            "file_path": "app/services/auth.py",
            "generated_code": "def login(): pass",
        })
        assert "app/services/auth.py" in ctx
        assert ctx["app/services/auth.py"] == "def login(): pass"

    def test_coder_intent(self):
        ctx = _state_to_context_files({
            "coder_decisions": "Used JWT for auth",
            "coder_known_issues": "none",
        })
        assert "coder_intent.md" in ctx
        assert "JWT" in ctx["coder_intent.md"]

    def test_prior_files(self):
        ctx = _state_to_context_files({
            "prior_file_summaries": [
                {"path": "app/models/user.py", "purpose": "User model", "key_exports": ["User", "UserCreate"]},
            ],
        })
        assert "prior_files.md" in ctx
        assert "User" in ctx["prior_files.md"]

    def test_phase_deliverables(self):
        ctx = _state_to_context_files({
            "phase_deliverables": "- Build health endpoint\n- Add tests",
        })
        assert "phase_deliverables.md" in ctx

    def test_audit_findings(self):
        ctx = _state_to_context_files({
            "audit_findings": [{"line": 5, "severity": "error", "message": "bad import"}],
        })
        assert "prior_audit_findings.json" in ctx
        data = json.loads(ctx["prior_audit_findings.json"])
        assert len(data) == 1


# ---------------------------------------------------------------------------
# _extract_exports tests
# ---------------------------------------------------------------------------

class TestExtractExports:
    def test_empty_source(self):
        assert _extract_exports("") == []

    def test_class_and_function(self):
        code = """
class User:
    pass

class _Internal:
    pass

def get_user():
    pass

async def create_user():
    pass

def _private():
    pass
"""
        exports = _extract_exports(code)
        assert "User" in exports
        assert "get_user" in exports
        assert "create_user" in exports
        assert "_Internal" not in exports
        assert "_private" not in exports

    def test_all_dunder(self):
        code = '''__all__ = ["User", "UserCreate", "get_db"]'''
        exports = _extract_exports(code)
        assert exports == ["User", "UserCreate", "get_db"]

    def test_all_dunder_single_quotes(self):
        code = "__all__ = ['Foo', 'Bar']"
        exports = _extract_exports(code)
        assert exports == ["Foo", "Bar"]

    def test_max_10_exports(self):
        lines = [f"class C{i}:\n    pass\n" for i in range(15)]
        code = "\n".join(lines)
        exports = _extract_exports(code)
        assert len(exports) == 10


# ---------------------------------------------------------------------------
# HandoffData + filter tests
# ---------------------------------------------------------------------------

class TestHandoffFilters:
    def _make_data(self):
        return HandoffData(
            pipeline_state={"file_path": "a.py", "contracts": {}},
            prior_stage_output={"tree": "abc", "long_field": "x" * 5000},
            tool_call_log=[{"tool": "read_file", "args": {"path": "a.py"}}],
            text_output="some verbose text output from the agent",
        )

    def test_strip_tool_calls(self):
        data = self._make_data()
        result = strip_tool_calls(data)
        assert result.tool_call_log == []
        assert result.pipeline_state == data.pipeline_state  # unchanged

    def test_strip_raw_text(self):
        data = self._make_data()
        result = strip_raw_text(data)
        assert result.text_output == ""
        assert result.prior_stage_output == data.prior_stage_output  # unchanged

    def test_cap_prior_output(self):
        data = self._make_data()
        result = cap_prior_output(100)(data)
        assert len(result.prior_stage_output["long_field"]) < 5000
        assert result.prior_stage_output["long_field"].endswith("[truncated]")
        # Short field unchanged
        assert result.prior_stage_output["tree"] == "abc"

    def test_compose_filters(self):
        data = self._make_data()
        composed = compose_filters(strip_tool_calls, strip_raw_text)
        result = composed(data)
        assert result.tool_call_log == []
        assert result.text_output == ""

    def test_scout_to_coder_filter(self):
        data = self._make_data()
        result = SCOUT_TO_CODER_FILTER(data)
        assert result.tool_call_log == []
        assert result.text_output == ""

    def test_coder_to_auditor_filter(self):
        data = self._make_data()
        result = CODER_TO_AUDITOR_FILTER(data)
        assert result.tool_call_log == []
        assert result.text_output == ""
        assert result.prior_stage_output["long_field"].endswith("[truncated]")

    def test_auditor_to_fixer_filter(self):
        data = self._make_data()
        result = AUDITOR_TO_FIXER_FILTER(data)
        assert result.tool_call_log == []
        assert result.text_output == ""

    def test_handoff_data_frozen(self):
        data = self._make_data()
        with pytest.raises(AttributeError):
            data.text_output = "changed"

    def test_clone(self):
        data = self._make_data()
        cloned = data.clone(text_output="new")
        assert cloned.text_output == "new"
        assert data.text_output != "new"  # original unchanged


# ---------------------------------------------------------------------------
# Lessons tests
# ---------------------------------------------------------------------------

class TestLessons:
    def test_make_empty_lessons(self):
        lessons = make_empty_lessons()
        assert lessons["confirmed_patterns"] == []
        assert lessons["fix_patterns"] == []
        assert lessons["import_conventions"] == {}
        assert lessons["error_resolutions"] == []
        assert lessons["decisions"] == []

    def test_lessons_to_context_empty(self):
        result = _lessons_to_context(make_empty_lessons())
        assert result == ""

    def test_lessons_to_context_confirmed_patterns(self):
        lessons = make_empty_lessons()
        lessons["confirmed_patterns"] = ["async SQLAlchemy via get_db()", "JWT auth"]
        result = _lessons_to_context(lessons)
        assert "Confirmed Patterns" in result
        assert "async SQLAlchemy" in result

    def test_lessons_to_context_fix_patterns(self):
        lessons = make_empty_lessons()
        lessons["fix_patterns"] = ["routers must not import repos"]
        result = _lessons_to_context(lessons)
        assert "Anti-Patterns" in result

    def test_lessons_to_context_import_conventions(self):
        lessons = make_empty_lessons()
        lessons["import_conventions"] = {"app.repos.user_repo": ["UserRepo"]}
        result = _lessons_to_context(lessons)
        assert "Import Conventions" in result
        assert "from app.repos.user_repo import UserRepo" in result

    def test_lessons_to_context_all_sections(self):
        lessons = {
            "confirmed_patterns": ["pattern1"],
            "fix_patterns": ["anti1"],
            "import_conventions": {"mod": ["A"]},
            "error_resolutions": [{"file": "a.py", "error": "err", "resolution": "fix"}],
            "decisions": ["chose X because Y"],
        }
        result = _lessons_to_context(lessons)
        assert "Confirmed Patterns" in result
        assert "Anti-Patterns" in result
        assert "Import Conventions" in result
        assert "Resolved Errors" in result
        assert "Architecture Decisions" in result


class TestExtractLessonsFromResult:
    """Tests for _extract_lessons_from_result using mock objects."""

    class MockResult:
        def __init__(self, audit_verdict="PASS", fixed_findings=""):
            self.audit_verdict = audit_verdict
            self.fixed_findings = fixed_findings

    def _make_state_mgr(self, **state):
        defaults = {
            "file_path": "app/main.py",
            "scout_patterns": {"db": "SQLAlchemy async"},
            "scout_imports_map": {"app.db": ["get_db"]},
            "audit_findings": [],
            "fixes_applied": [],
            "coder_decisions": "Used dependency injection",
        }
        defaults.update(state)
        return PipelineStateManager(FilePipelineState, defaults)

    def test_pass_first_try_confirms_patterns(self):
        result = self.MockResult(audit_verdict="PASS", fixed_findings="")
        mgr = self._make_state_mgr()
        lessons = _extract_lessons_from_result(result, mgr)
        assert len(lessons["confirmed_patterns"]) > 0
        assert "db: SQLAlchemy async" in lessons["confirmed_patterns"]

    def test_pass_with_fixes_no_confirmation(self):
        result = self.MockResult(audit_verdict="PASS", fixed_findings="fixed import")
        mgr = self._make_state_mgr()
        lessons = _extract_lessons_from_result(result, mgr)
        # Patterns NOT confirmed because fixer ran
        assert lessons["confirmed_patterns"] == []

    def test_fail_extracts_anti_patterns(self):
        result = self.MockResult(audit_verdict="PASS", fixed_findings="fixed import")
        mgr = self._make_state_mgr(
            audit_findings=[{"severity": "error", "message": "bad import"}],
            fixes_applied=[{"finding_ref": "bad import", "change": "fixed it"}],
        )
        lessons = _extract_lessons_from_result(result, mgr)
        assert "bad import" in lessons["fix_patterns"]
        assert len(lessons["error_resolutions"]) > 0

    def test_imports_extracted(self):
        result = self.MockResult(audit_verdict="PASS")
        mgr = self._make_state_mgr()
        lessons = _extract_lessons_from_result(result, mgr)
        assert "app.db" in lessons["import_conventions"]

    def test_decisions_extracted(self):
        result = self.MockResult(audit_verdict="PASS")
        mgr = self._make_state_mgr()
        lessons = _extract_lessons_from_result(result, mgr)
        assert len(lessons["decisions"]) > 0
        assert "dependency injection" in lessons["decisions"][0]


# ---------------------------------------------------------------------------
# TierState with PipelineStateManager tests
# ---------------------------------------------------------------------------

class TestTierStateManager:
    def test_completed_files_append(self):
        mgr = PipelineStateManager(TierState, {
            "completed_files": [],
            "lessons": make_empty_lessons(),
        })
        mgr.apply_update({
            "completed_files": [{"path": "a.py", "purpose": "A", "key_exports": ["A"]}],
        })
        mgr.apply_update({
            "completed_files": [{"path": "b.py", "purpose": "B", "key_exports": ["B"]}],
        })
        assert len(mgr.state["completed_files"]) == 2
        assert mgr.state["completed_files"][0]["path"] == "a.py"

    def test_lessons_overwrite(self):
        """Lessons at TierState level use overwrite semantics (managed externally)."""
        mgr = PipelineStateManager(TierState, {
            "completed_files": [],
            "lessons": make_empty_lessons(),
        })
        mgr.apply_update({"lessons": {"confirmed_patterns": ["p1"]}})
        # TierState.lessons is plain dict — overwrite semantics
        assert mgr.state["lessons"]["confirmed_patterns"] == ["p1"]
