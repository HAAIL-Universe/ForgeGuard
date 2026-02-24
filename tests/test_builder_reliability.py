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
