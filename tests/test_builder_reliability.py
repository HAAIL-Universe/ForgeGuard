"""Anti-regression tests for builder pipeline reliability fixes.

Covers:
  - Error 1: __init__.py empty response (keyword tightening + CODER fallback)
  - Error 2: Invariant violation on Phase 0 (baseline reset)
  - Error 3: Dependency gate false positive (underscore→hyphen mapping)

Added alongside commit fixing all three build errors (2026-02-24).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fix sys.path so imports work from the repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from builder.builder_agent import (
    _is_init_with_exports,
    _is_trivial_file,
    _NONTRIVIAL_KEYWORDS,
)
from forge_ide.invariants import InvariantRegistry
from app.audit.runner import _PY_NAME_MAP


# ═══════════════════════════════════════════════════════════════════════════
# Error 1: __init__.py classification (keyword tightening)
# ═══════════════════════════════════════════════════════════════════════════


class TestTrivialFileDetection:
    """_is_trivial_file should return True for plain package markers."""

    def test_plain_package_marker(self):
        assert _is_trivial_file("app/__init__.py", "Package marker") is True

    def test_package_initialization(self):
        assert _is_trivial_file("app/__init__.py", "Package initialization") is True

    def test_purpose_with_word_imports(self):
        """'import' alone should NOT trigger nontrivial classification."""
        assert _is_trivial_file(
            "app/services/__init__.py",
            "Initialize the services package — handles imports",
        ) is True

    def test_purpose_with_word_export(self):
        """'export' alone should NOT trigger nontrivial classification."""
        assert _is_trivial_file(
            "tests/__init__.py",
            "Test package marker, exports nothing",
        ) is True

    def test_empty_purpose(self):
        assert _is_trivial_file("app/__init__.py", "") is True

    def test_purpose_see_deliverables(self):
        """Common planner fallback purpose."""
        assert _is_trivial_file("app/__init__.py", "see deliverables") is True

    def test_non_init_file_is_never_trivial(self):
        assert _is_trivial_file("app/main.py", "Package marker") is False

    def test_py_typed_is_trivial(self):
        assert _is_trivial_file("app/py.typed", "Type stub marker") is True

    def test_gitkeep_is_trivial(self):
        assert _is_trivial_file("static/.gitkeep", "Placeholder") is True


class TestTrivialFileWithReexports:
    """_is_trivial_file should return False for genuine re-export purposes."""

    def test_barrel_reexport(self):
        assert _is_trivial_file(
            "app/__init__.py", "Barrel re-export of all services"
        ) is False

    def test_public_api(self):
        assert _is_trivial_file(
            "app/__init__.py", "Public API surface for the package"
        ) is False

    def test_public_interface(self):
        assert _is_trivial_file(
            "app/__init__.py", "Public interface for external consumers"
        ) is False


class TestInitWithExports:
    """_is_init_with_exports should only trigger on specific re-export keywords."""

    def test_reexport_keyword(self):
        assert _is_init_with_exports("app/__init__.py", "re-export all models") is True

    def test_barrel_keyword(self):
        assert _is_init_with_exports("app/__init__.py", "barrel file") is True

    def test_public_api_keyword(self):
        assert _is_init_with_exports("app/__init__.py", "public api surface") is True

    def test_public_interface_keyword(self):
        assert _is_init_with_exports("app/__init__.py", "public interface") is True

    def test_plain_package_marker_not_init_with_exports(self):
        assert _is_init_with_exports("app/__init__.py", "Package marker") is False

    def test_word_import_alone_not_init_with_exports(self):
        """Regression test: 'import' alone must NOT trigger init-with-exports."""
        assert _is_init_with_exports(
            "app/__init__.py", "imports needed for module"
        ) is False

    def test_word_export_alone_not_init_with_exports(self):
        """Regression test: 'export' alone must NOT trigger init-with-exports."""
        assert _is_init_with_exports(
            "app/__init__.py", "exports nothing"
        ) is False

    def test_non_init_file_never_init_with_exports(self):
        assert _is_init_with_exports("app/main.py", "re-export all") is False


class TestNontrivialKeywordsRobustness:
    """Ensure the keyword list itself is correct."""

    def test_import_not_in_keywords(self):
        """'import' was removed because it's too broad."""
        assert "import" not in _NONTRIVIAL_KEYWORDS

    def test_export_not_in_keywords(self):
        """'export' alone was removed because it's too broad."""
        assert "export" not in _NONTRIVIAL_KEYWORDS

    def test_reexport_in_keywords(self):
        assert "re-export" in _NONTRIVIAL_KEYWORDS

    def test_barrel_in_keywords(self):
        assert "barrel" in _NONTRIVIAL_KEYWORDS


# ═══════════════════════════════════════════════════════════════════════════
# Error 2: Invariant baseline for Phase 0
# ═══════════════════════════════════════════════════════════════════════════


class TestInvariantBaselines:
    """Invariant registry should allow Phase 0 to start from zero baseline."""

    def test_phase_0_fresh_start_allows_zero_tests(self):
        """Phase 0: baseline = 0, actual = 0 → PASS."""
        registry = InvariantRegistry()
        registry.register_builtins({})  # Empty dict = Phase 0 fresh start
        result = registry.check("backend_test_count", 0)
        assert result.passed is True

    def test_phase_0_fresh_start_allows_one_test(self):
        """Phase 0: baseline = 0, actual = 1 (genesis health test) → PASS."""
        registry = InvariantRegistry()
        registry.register_builtins({})
        result = registry.check("backend_test_count", 1)
        assert result.passed is True

    def test_phase_1_resume_enforces_monotonic(self):
        """Phase 1+ resume: baseline = 53, actual = 10 → FAIL."""
        registry = InvariantRegistry()
        registry.register_builtins({"backend_test_count": 53})
        result = registry.check("backend_test_count", 10)
        assert result.passed is False

    def test_phase_1_resume_allows_growth(self):
        """Phase 1+ resume: baseline = 53, actual = 60 → PASS."""
        registry = InvariantRegistry()
        registry.register_builtins({"backend_test_count": 53})
        result = registry.check("backend_test_count", 60)
        assert result.passed is True

    def test_phase_1_resume_allows_equal(self):
        """Phase 1+ resume: baseline = 53, actual = 53 → PASS (monotonic_up = >=)."""
        registry = InvariantRegistry()
        registry.register_builtins({"backend_test_count": 53})
        result = registry.check("backend_test_count", 53)
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════════
# Error 3: Dependency gate false positives (underscore→hyphen)
# ═══════════════════════════════════════════════════════════════════════════


class TestDependencyGateMapping:
    """_PY_NAME_MAP should cover common underscore→hyphen mismatches."""

    def test_pydantic_settings_mapped(self):
        assert _PY_NAME_MAP["pydantic_settings"] == "pydantic-settings"

    def test_dateutil_mapped(self):
        assert _PY_NAME_MAP["dateutil"] == "python-dateutil"

    def test_attrs_mapped(self):
        assert _PY_NAME_MAP["attr"] == "attrs"

    def test_existing_mappings_preserved(self):
        """Ensure we didn't break existing mappings."""
        assert _PY_NAME_MAP["PIL"] == "Pillow"
        assert _PY_NAME_MAP["cv2"] == "opencv-python"
        assert _PY_NAME_MAP["sklearn"] == "scikit-learn"
        assert _PY_NAME_MAP["yaml"] == "PyYAML"
        assert _PY_NAME_MAP["bs4"] == "beautifulsoup4"
        assert _PY_NAME_MAP["dotenv"] == "python-dotenv"
        assert _PY_NAME_MAP["jose"] == "python-jose"
        assert _PY_NAME_MAP["jwt"] == "PyJWT"
        assert _PY_NAME_MAP["pydantic"] == "pydantic"


class TestUnderscoreHyphenFallback:
    """The G3 gate should match imports with underscores against pip packages with hyphens."""

    def test_underscore_to_hyphen_conversion(self):
        """Basic string conversion used as fallback."""
        imp = "pydantic_settings"
        alt = imp.replace("_", "-")
        assert alt == "pydantic-settings"

    def test_fallback_matches_requirements_line(self):
        """Simulates the fallback regex search in verification.py."""
        dep_content = "pydantic-settings==2.7.1\nfastapi==0.115.0\n"
        imp = "pydantic_settings"

        # Primary lookup (what was failing before)
        look_for = _PY_NAME_MAP.get(imp, imp)
        primary_match = re.search(re.escape(look_for), dep_content, re.IGNORECASE)
        assert primary_match is not None  # Now passes because of new mapping

    def test_fallback_for_unmapped_package(self):
        """For a package NOT in _PY_NAME_MAP, the underscore→hyphen fallback should work."""
        dep_content = "some-weird-package==1.0.0\n"
        imp = "some_weird_package"

        # Primary lookup misses (not in map, underscore != hyphen)
        look_for = _PY_NAME_MAP.get(imp, imp)
        primary_match = re.search(re.escape(look_for), dep_content, re.IGNORECASE)
        assert primary_match is None  # Expected: no match

        # Fallback kicks in
        alt = imp.replace("_", "-")
        fallback_match = re.search(re.escape(alt), dep_content, re.IGNORECASE)
        assert fallback_match is not None  # Fallback succeeds

    def test_no_false_fallback_for_stdlib(self):
        """Stdlib modules (no underscores) shouldn't trigger false matches."""
        dep_content = "fastapi==0.115.0\n"
        imp = "os"
        alt = imp.replace("_", "-")
        assert alt == imp  # No change, so fallback is skipped


# ═══════════════════════════════════════════════════════════════════════════
# Phase review gate + _identify_files_to_fix
# ═══════════════════════════════════════════════════════════════════════════

from app.services.build._state import (
    register_phase_review,
    resolve_phase_review,
    pop_phase_review_response,
    cleanup_phase_review,
)
from app.services.build_service import _identify_files_to_fix


class TestPhaseReviewGate:
    """Phase review gate should follow the plan_review gate pattern."""

    def test_register_creates_event(self):
        event = register_phase_review("test-build-1")
        assert event is not None
        assert not event.is_set()
        cleanup_phase_review("test-build-1")

    def test_resolve_sets_event(self):
        event = register_phase_review("test-build-2")
        result = resolve_phase_review("test-build-2", {"action": "continue"})
        assert result is True
        assert event.is_set()
        cleanup_phase_review("test-build-2")

    def test_resolve_nonexistent_returns_false(self):
        result = resolve_phase_review("nonexistent-build", {"action": "fix"})
        assert result is False

    def test_pop_returns_response(self):
        register_phase_review("test-build-3")
        resolve_phase_review("test-build-3", {"action": "fix"})
        response = pop_phase_review_response("test-build-3")
        assert response == {"action": "fix"}

    def test_pop_cleans_up(self):
        register_phase_review("test-build-4")
        resolve_phase_review("test-build-4", {"action": "continue"})
        pop_phase_review_response("test-build-4")
        # Second pop should return None
        assert pop_phase_review_response("test-build-4") is None

    def test_cleanup_removes_all_state(self):
        register_phase_review("test-build-5")
        resolve_phase_review("test-build-5", {"action": "fix"})
        cleanup_phase_review("test-build-5")
        assert pop_phase_review_response("test-build-5") is None


class TestIdentifyFilesToFix:
    """_identify_files_to_fix should extract file paths from governance + verification."""

    def test_g3_dependency_gate_extracts_file(self):
        governance = {
            "checks": [{
                "code": "G3", "name": "Dependency gate", "result": "FAIL",
                "detail": "app/config.py imports 'pydantic_settings' (not in requirements.txt)",
            }],
        }
        verification = {}
        phase_files = {"app/config.py": "content", "app/main.py": "content"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert "app/config.py" in result

    def test_g2_boundary_extracts_file(self):
        governance = {
            "checks": [{
                "code": "G2", "name": "Boundary compliance", "result": "FAIL",
                "detail": "app/routers/auth.py imports app.services.user (forbidden)",
            }],
        }
        verification = {}
        phase_files = {"app/routers/auth.py": "content"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert "app/routers/auth.py" in result

    def test_syntax_error_extracts_file(self):
        governance = {"checks": []}
        verification = {
            "syntax_error_details": [{"file": "app/models.py", "error": "SyntaxError: invalid syntax"}],
        }
        phase_files = {"app/models.py": "content"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert "app/models.py" in result

    def test_fallback_to_all_phase_files(self):
        """When no specific files identified, fall back to all phase files."""
        governance = {"checks": []}
        verification = {}
        phase_files = {"app/a.py": "a", "app/b.py": "b"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert set(result) == {"app/a.py", "app/b.py"}

    def test_only_returns_phase_files(self):
        """Files not in the current phase should be excluded."""
        governance = {
            "checks": [{
                "code": "G3", "name": "Dependency gate", "result": "FAIL",
                "detail": "app/config.py imports 'foo' (not in requirements.txt)",
            }],
        }
        verification = {}
        phase_files = {"app/main.py": "content"}  # config.py NOT in this phase
        result = _identify_files_to_fix(governance, verification, phase_files)
        # config.py is not in phase_files, so falls back to all phase files
        assert "app/main.py" in result

    def test_pass_checks_ignored(self):
        """PASS checks should not contribute files."""
        governance = {
            "checks": [
                {"code": "G1", "name": "Scope", "result": "PASS", "detail": "all files present"},
                {"code": "G3", "name": "Dependency gate", "result": "FAIL",
                 "detail": "app/config.py imports 'x' (not in requirements.txt)"},
            ],
        }
        verification = {}
        phase_files = {"app/config.py": "content", "app/main.py": "content"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert result == ["app/config.py"]

    def test_multiple_issues_combined(self):
        """Multiple governance failures and syntax errors should all be collected."""
        governance = {
            "checks": [{
                "code": "G3", "name": "Dependency gate", "result": "FAIL",
                "detail": "app/config.py imports 'x'; app/main.py imports 'y'",
            }],
        }
        verification = {
            "syntax_error_details": [{"file": "app/utils.py", "error": "SyntaxError"}],
        }
        phase_files = {"app/config.py": "c", "app/main.py": "m", "app/utils.py": "u"}
        result = _identify_files_to_fix(governance, verification, phase_files)
        assert "app/config.py" in result
        assert "app/main.py" in result
        assert "app/utils.py" in result


# ═══════════════════════════════════════════════════════════════════════════
# Error categorization (Hybrid Error Actions)
# ═══════════════════════════════════════════════════════════════════════════

from app.services.build_service import (
    _categorize_error,
    _extract_regen_path,
    _is_trivial_regen,
)


class TestCategorizeError:
    """_categorize_error should return the correct category for each error type."""

    def test_governance_g3_is_fixable(self):
        error = {"source": "governance", "message": "[G3] Dependency gate: app/config.py imports 'x'"}
        assert _categorize_error(error) == "fixable"

    def test_governance_g2_is_fixable(self):
        error = {"source": "governance", "message": "[G2] Boundary compliance: forbidden import"}
        assert _categorize_error(error) == "fixable"

    def test_governance_g1_is_fixable(self):
        error = {"source": "governance", "message": "[G1] Scope: phantom files on disk"}
        assert _categorize_error(error) == "fixable"

    def test_governance_g4_warning_is_dismiss_only(self):
        error = {"source": "governance", "message": "[G4] Secrets scan: potential key found"}
        assert _categorize_error(error) == "dismiss_only"

    def test_governance_g7_is_dismiss_only(self):
        error = {"source": "governance", "message": "[G7] TODO scan: 3 placeholders found"}
        assert _categorize_error(error) == "dismiss_only"

    def test_file_generation_is_regeneratable(self):
        error = {"source": "file_generation", "message": "Failed to generate tests/__init__.py: Empty response"}
        assert _categorize_error(error) == "regeneratable"

    def test_file_generation_no_text_block_is_regeneratable(self):
        error = {"source": "file_generation", "message": "Failed to generate app/main.py: No text block"}
        assert _categorize_error(error) == "regeneratable"

    def test_verify_syntax_is_fixable(self):
        error = {"source": "verify", "file_path": "app/models.py", "message": "Syntax error in app/models.py"}
        assert _categorize_error(error) == "fixable"

    def test_audit_test_failure_is_fixable(self):
        error = {"source": "audit", "file_path": "app/main.py", "message": "Tests failed: 3 failures"}
        assert _categorize_error(error) == "fixable"

    def test_verify_without_file_path_is_dismiss_only(self):
        """Verify errors without a file path can't be fixed."""
        error = {"source": "verify", "message": "Syntax error somewhere"}
        assert _categorize_error(error) == "dismiss_only"

    def test_invariant_is_dismiss_only(self):
        error = {"source": "invariant", "message": "INVARIANT VIOLATION: backend_test_count"}
        assert _categorize_error(error) == "dismiss_only"

    def test_tier_system_is_dismiss_only(self):
        error = {"source": "tier_system", "message": "Chunk system error: timeout"}
        assert _categorize_error(error) == "dismiss_only"

    def test_system_is_dismiss_only(self):
        error = {"source": "system", "message": "Build failed: some error"}
        assert _categorize_error(error) == "dismiss_only"

    def test_unknown_source_is_dismiss_only(self):
        error = {"source": "unknown", "message": "Something went wrong"}
        assert _categorize_error(error) == "dismiss_only"


class TestExtractRegenPath:
    """_extract_regen_path should parse file paths from error messages."""

    def test_standard_format(self):
        msg = "Failed to generate tests/__init__.py: Empty response from Anthropic API"
        assert _extract_regen_path(msg) == "tests/__init__.py"

    def test_no_text_block_format(self):
        msg = "Failed to generate app/services/__init__.py: No text block in Anthropic API response"
        assert _extract_regen_path(msg) == "app/services/__init__.py"

    def test_nested_path(self):
        msg = "Failed to generate app/services/auth/handler.py: Empty response"
        assert _extract_regen_path(msg) == "app/services/auth/handler.py"

    def test_no_match(self):
        msg = "Some other error message"
        assert _extract_regen_path(msg) is None

    def test_colon_stripped(self):
        msg = "Failed to generate app/main.py: timeout"
        assert _extract_regen_path(msg) == "app/main.py"


class TestIsTrivialRegen:
    """_is_trivial_regen should identify files that can be regenerated as empty."""

    def test_init_py(self):
        assert _is_trivial_regen("app/__init__.py") is True

    def test_nested_init_py(self):
        assert _is_trivial_regen("app/services/auth/__init__.py") is True

    def test_gitkeep(self):
        assert _is_trivial_regen("static/.gitkeep") is True

    def test_py_typed(self):
        assert _is_trivial_regen("app/py.typed") is True

    def test_regular_python_file(self):
        assert _is_trivial_regen("app/main.py") is False

    def test_config_file(self):
        assert _is_trivial_regen("app/config.py") is False

    def test_tsx_file(self):
        assert _is_trivial_regen("web/src/App.tsx") is False


# ═══════════════════════════════════════════════════════════════════════════
# Gate Persistence — interrupt_stale_builds split, _fail_build cleanup
# ═══════════════════════════════════════════════════════════════════════════


class TestGatePersistenceDesign:
    """Verify the gate persistence design invariants at the code level.

    These are pure unit tests — no DB needed. They check that the code paths
    exist and that the data structures are correct.
    """

    def test_set_build_gate_function_exists(self):
        """build_repo must expose set_build_gate."""
        from app.repos import build_repo as br
        assert hasattr(br, "set_build_gate")
        assert callable(br.set_build_gate)

    def test_clear_build_gate_function_exists(self):
        """build_repo must expose clear_build_gate."""
        from app.repos import build_repo as br
        assert hasattr(br, "clear_build_gate")
        assert callable(br.clear_build_gate)

    def test_fail_build_calls_clear_gate(self):
        """_fail_build source should contain clear_build_gate call."""
        import inspect
        from app.services.build._state import _fail_build
        src = inspect.getsource(_fail_build)
        assert "clear_build_gate" in src, "_fail_build must clear persisted gate"

    def test_interrupt_stale_preserves_gated(self):
        """interrupt_stale_builds should pause (not fail) gated builds."""
        import inspect
        from app.repos.build_repo import interrupt_stale_builds
        src = inspect.getsource(interrupt_stale_builds)
        # Must contain BOTH a paused path and a failed path
        assert "paused" in src.lower(), "Should pause gated builds"
        assert "failed" in src.lower(), "Should fail ungated builds"
        assert "pending_gate IS NOT NULL" in src, "Must check for gate presence"
        assert "pending_gate IS NULL" in src, "Must check for gate absence"

    def test_main_lifespan_applies_gate_migration(self):
        """app/main.py lifespan must apply the gate columns."""
        import inspect
        from app.main import lifespan
        src = inspect.getsource(lifespan)
        assert "pending_gate" in src, "lifespan must add pending_gate column"
        assert "gate_payload" in src, "lifespan must add gate_payload column"
        assert "gate_registered_at" in src, "lifespan must add gate_registered_at column"


class TestGateRegistrationPoints:
    """Verify that each gate registration also persists to DB."""

    def _get_build_service_source(self):
        import inspect
        import app.services.build_service as bs
        return inspect.getsource(bs)

    def test_ide_ready_persists(self):
        src = self._get_build_service_source()
        # After register_ide_ready there should be a set_build_gate call
        idx = src.index("register_ide_ready(")
        nearby = src[idx:idx+300]
        assert "set_build_gate" in nearby, "ide_ready registration must persist gate"

    def test_plan_review_persists(self):
        src = self._get_build_service_source()
        idx = src.index("register_plan_review(")
        nearby = src[idx:idx+300]
        assert "set_build_gate" in nearby, "plan_review registration must persist gate"

    def test_phase_review_persists(self):
        src = self._get_build_service_source()
        idx = src.index("register_phase_review(")
        nearby = src[idx:idx+600]  # wider window: payload is constructed between register + persist
        assert "set_build_gate" in nearby, "phase_review registration must persist gate"

    def test_clarification_persists(self):
        src = self._get_build_service_source()
        idx = src.index("register_clarification(")
        nearby = src[idx:idx+300]
        assert "set_build_gate" in nearby, "clarification registration must persist gate"


class TestGateResolutionClear:
    """Verify that each gate resolution function clears the DB gate."""

    def _get_func_source(self, func_name):
        import inspect
        import app.services.build_service as bs
        func = getattr(bs, func_name)
        return inspect.getsource(func)

    def test_approve_plan_clears(self):
        src = self._get_func_source("approve_plan")
        assert "clear_build_gate" in src, "approve_plan must clear DB gate"

    def test_respond_phase_review_clears(self):
        src = self._get_func_source("respond_phase_review")
        assert "clear_build_gate" in src, "respond_phase_review must clear DB gate"

    def test_commence_build_clears(self):
        src = self._get_func_source("commence_build")
        assert "clear_build_gate" in src, "commence_build must clear DB gate"

    def test_resume_clarification_clears(self):
        src = self._get_func_source("resume_clarification")
        assert "clear_build_gate" in src, "resume_clarification must clear DB gate"


class TestGatePostRestartHandling:
    """Verify post-restart fallback paths exist in resolution functions."""

    def _get_func_source(self, func_name):
        import inspect
        import app.services.build_service as bs
        func = getattr(bs, func_name)
        return inspect.getsource(func)

    def test_approve_plan_handles_restart(self):
        src = self._get_func_source("approve_plan")
        assert 'pending_gate' in src, "approve_plan must check for persisted gate"
        assert 'post-restart' in src.lower(), "approve_plan must have post-restart path"

    def test_respond_phase_review_handles_restart(self):
        src = self._get_func_source("respond_phase_review")
        assert 'pending_gate' in src, "respond_phase_review must check for persisted gate"
        assert 'post-restart' in src.lower(), "respond_phase_review must have post-restart path"

    def test_commence_build_handles_restart(self):
        src = self._get_func_source("commence_build")
        assert 'pending_gate' in src, "commence_build must check for persisted gate"
        assert 'post-restart' in src.lower(), "commence_build must have post-restart path"

    def test_resume_clarification_handles_restart(self):
        src = self._get_func_source("resume_clarification")
        assert 'pending_gate' in src, "resume_clarification must check for persisted gate"
        assert 'context lost' in src.lower() or 'cannot resume' in src.lower(), \
            "resume_clarification must explain why clarification can't resume"


class TestGetBuildStatusGateFields:
    """Verify get_build_status includes gate information."""

    def test_includes_pending_gate_in_source(self):
        import inspect
        import app.services.build_service as bs
        src = inspect.getsource(bs.get_build_status)
        assert "pending_gate" in src, "get_build_status must include pending_gate"
        assert "gate_payload" in src, "get_build_status must include gate_payload"

    def test_sets_ide_gate_pending_for_paused_ide_gate(self):
        import inspect
        import app.services.build_service as bs
        src = inspect.getsource(bs.get_build_status)
        # Should set ide_gate_pending when pending_gate is ide_ready
        assert "ide_gate_pending" in src

    def test_sets_plan_review_pending_for_paused_plan_gate(self):
        import inspect
        import app.services.build_service as bs
        src = inspect.getsource(bs.get_build_status)
        assert "plan_review_pending" in src
