"""Tests for forge_ide.test_scope — changed-file → test-file mapping."""

from __future__ import annotations

import pytest

from forge_ide.test_scope import (
    _is_ignorable,
    _is_test_file,
    _js_candidates,
    _python_candidates,
    filter_existing,
    format_scoped_command,
    infer_test_candidates,
    scope_tests_for_changes,
)


# -----------------------------------------------------------------------
# _is_ignorable
# -----------------------------------------------------------------------

class TestIsIgnorable:
    def test_conftest(self):
        assert _is_ignorable("tests/conftest.py") is True

    def test_init(self):
        assert _is_ignorable("app/__init__.py") is True

    def test_pyproject(self):
        assert _is_ignorable("pyproject.toml") is True

    def test_package_json(self):
        assert _is_ignorable("web/package.json") is True

    def test_normal_source(self):
        assert _is_ignorable("app/services/user.py") is False

    def test_tsx_component(self):
        assert _is_ignorable("src/Auth.tsx") is False

    def test_setup_py(self):
        assert _is_ignorable("setup.py") is True

    def test_tsconfig(self):
        assert _is_ignorable("web/tsconfig.json") is True


# -----------------------------------------------------------------------
# _is_test_file
# -----------------------------------------------------------------------

class TestIsTestFile:
    def test_python_test_prefix(self):
        assert _is_test_file("tests/test_auth.py") is True

    def test_python_test_suffix(self):
        assert _is_test_file("auth_test.py") is True

    def test_js_dot_test(self):
        assert _is_test_file("Auth.test.tsx") is True

    def test_js_dot_spec(self):
        assert _is_test_file("Auth.spec.ts") is True

    def test_normal_source(self):
        assert _is_test_file("auth.py") is False

    def test_normal_tsx(self):
        assert _is_test_file("Auth.tsx") is False


# -----------------------------------------------------------------------
# _python_candidates
# -----------------------------------------------------------------------

class TestPythonCandidates:
    def test_simple(self):
        result = _python_candidates("app/auth.py")
        assert "tests/test_auth.py" in result
        assert "test/test_auth.py" in result

    def test_nested(self):
        result = _python_candidates("app/services/user.py")
        assert "tests/test_user.py" in result
        # Co-located candidate
        assert "app/services/test_user.py" in result

    def test_top_level(self):
        result = _python_candidates("config.py")
        assert "tests/test_config.py" in result

    def test_preserves_order(self):
        result = _python_candidates("app/main.py")
        # tests/ comes first, then test/, then co-located
        assert result[0] == "tests/test_main.py"
        assert result[1] == "test/test_main.py"


# -----------------------------------------------------------------------
# _js_candidates
# -----------------------------------------------------------------------

class TestJsCandidates:
    def test_tsx_component(self):
        result = _js_candidates("src/components/Auth.tsx")
        assert "src/components/Auth.test.tsx" in result
        assert "src/components/Auth.spec.tsx" in result
        assert "src/components/__tests__/Auth.test.tsx" in result

    def test_ts_util(self):
        result = _js_candidates("src/utils/parse.ts")
        assert "src/utils/parse.test.ts" in result
        assert "src/utils/parse.spec.ts" in result

    def test_top_level_js(self):
        result = _js_candidates("index.js")
        assert "index.test.js" in result
        assert "index.spec.js" in result

    def test_preserves_extension(self):
        result = _js_candidates("src/hook.mjs")
        assert "src/hook.test.mjs" in result


# -----------------------------------------------------------------------
# infer_test_candidates
# -----------------------------------------------------------------------

class TestInferTestCandidates:
    def test_python_source(self):
        result = infer_test_candidates(["app/auth.py"])
        assert "tests/test_auth.py" in result

    def test_ts_source(self):
        result = infer_test_candidates(["src/Auth.tsx"])
        assert "src/Auth.test.tsx" in result

    def test_already_test_file_passthrough(self):
        result = infer_test_candidates(["tests/test_auth.py"])
        assert result == ["tests/test_auth.py"]

    def test_js_test_file_passthrough(self):
        result = infer_test_candidates(["src/Auth.test.tsx"])
        assert result == ["src/Auth.test.tsx"]

    def test_ignorable_skipped(self):
        result = infer_test_candidates(["conftest.py", "__init__.py"])
        assert result == []

    def test_mixed_languages(self):
        result = infer_test_candidates(["app/user.py", "src/App.tsx"])
        assert "tests/test_user.py" in result
        assert "src/App.test.tsx" in result

    def test_dedup(self):
        # Two files in same dir with same stem shouldn't produce dupes
        result = infer_test_candidates(["app/auth.py", "lib/auth.py"])
        assert result.count("tests/test_auth.py") == 1

    def test_backslash_normalised(self):
        result = infer_test_candidates(["app\\services\\user.py"])
        assert "tests/test_user.py" in result

    def test_empty_input(self):
        assert infer_test_candidates([]) == []

    def test_blank_strings_ignored(self):
        assert infer_test_candidates(["", "  "]) == []

    def test_unknown_extension_ignored(self):
        # .md, .json (non-ignorable) — no candidates
        result = infer_test_candidates(["README.md"])
        assert result == []


# -----------------------------------------------------------------------
# filter_existing
# -----------------------------------------------------------------------

class TestFilterExisting:
    def test_match(self):
        candidates = ["tests/test_auth.py", "tests/test_user.py"]
        existing = ["tests/test_auth.py", "tests/test_config.py"]
        assert filter_existing(candidates, existing) == ["tests/test_auth.py"]

    def test_no_match(self):
        assert filter_existing(["tests/test_foo.py"], ["tests/test_bar.py"]) == []

    def test_case_insensitive(self):
        # Windows paths can have different casing
        result = filter_existing(
            ["tests/test_auth.py"],
            ["Tests/Test_Auth.py"],
        )
        assert result == ["tests/test_auth.py"]

    def test_backslash_normalised(self):
        result = filter_existing(
            ["tests/test_auth.py"],
            ["tests\\test_auth.py"],
        )
        assert result == ["tests/test_auth.py"]

    def test_empty(self):
        assert filter_existing([], ["a.py"]) == []
        assert filter_existing(["a.py"], []) == []


# -----------------------------------------------------------------------
# format_scoped_command
# -----------------------------------------------------------------------

class TestFormatScopedCommand:
    def test_single_file(self):
        cmd = format_scoped_command(["tests/test_auth.py"])
        assert cmd == "pytest tests/test_auth.py -x -v"

    def test_multiple_files(self):
        cmd = format_scoped_command(["tests/test_auth.py", "tests/test_user.py"])
        assert cmd == "pytest tests/test_auth.py tests/test_user.py -x -v"

    def test_custom_runner(self):
        cmd = format_scoped_command(
            ["src/Auth.test.tsx"], runner="npx vitest", extra_args="--run"
        )
        assert cmd == "npx vitest src/Auth.test.tsx --run"

    def test_no_extra_args(self):
        cmd = format_scoped_command(["tests/test_auth.py"], extra_args="")
        assert cmd == "pytest tests/test_auth.py"

    def test_empty_paths_returns_fallback(self):
        cmd = format_scoped_command([], fallback_full="pytest")
        assert cmd == "pytest"

    def test_empty_paths_no_fallback(self):
        assert format_scoped_command([]) == ""


# -----------------------------------------------------------------------
# scope_tests_for_changes (integration / one-shot)
# -----------------------------------------------------------------------

class TestScopeTestsForChanges:
    def test_full_pipeline(self):
        changed = ["app/auth.py", "app/user.py"]
        existing = [
            "tests/test_auth.py",
            "tests/test_config.py",
            "tests/test_user.py",
        ]
        matched, cmd = scope_tests_for_changes(changed, existing)
        assert "tests/test_auth.py" in matched
        assert "tests/test_user.py" in matched
        assert "pytest" in cmd
        assert "tests/test_auth.py" in cmd
        assert "tests/test_user.py" in cmd

    def test_no_existing_matches(self):
        changed = ["app/brand_new.py"]
        existing = ["tests/test_auth.py"]
        matched, cmd = scope_tests_for_changes(changed, existing)
        assert matched == []
        assert cmd == ""

    def test_no_existing_with_fallback(self):
        changed = ["app/brand_new.py"]
        existing = ["tests/test_auth.py"]
        matched, cmd = scope_tests_for_changes(
            changed, existing, fallback_full="pytest"
        )
        assert matched == []
        assert cmd == "pytest"

    def test_js_project(self):
        changed = ["src/components/Auth.tsx"]
        existing = ["src/components/Auth.test.tsx"]
        matched, cmd = scope_tests_for_changes(
            changed, existing, runner="npx vitest", extra_args="--run"
        )
        assert matched == ["src/components/Auth.test.tsx"]
        assert cmd == "npx vitest src/components/Auth.test.tsx --run"

    def test_mixed_source_types(self):
        changed = ["app/auth.py", "src/Login.tsx"]
        existing = ["tests/test_auth.py", "src/Login.test.tsx"]
        matched, cmd = scope_tests_for_changes(changed, existing)
        assert "tests/test_auth.py" in matched
        assert "src/Login.test.tsx" in matched

    def test_already_test_file(self):
        """When the changed file IS a test file, include it directly."""
        changed = ["tests/test_auth.py"]
        existing = ["tests/test_auth.py"]
        matched, cmd = scope_tests_for_changes(changed, existing)
        assert matched == ["tests/test_auth.py"]
        assert "tests/test_auth.py" in cmd


# -----------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------

class TestEdgeCases:
    def test_spec_dir_convention(self):
        """Python 'spec' dir is also checked."""
        result = _python_candidates("app/auth.py")
        assert "spec/test_auth.py" in result

    def test_dunder_tests_folder(self):
        """JS __tests__ subfolder convention."""
        cands = _js_candidates("src/utils/hook.ts")
        assert "src/utils/__tests__/hook.test.ts" in cands

    def test_multiple_changes_same_module(self):
        """Two files mapping to the same test don't duplicate."""
        changed = ["app/auth.py", "lib/auth.py"]
        existing = ["tests/test_auth.py"]
        matched, cmd = scope_tests_for_changes(changed, existing)
        assert matched.count("tests/test_auth.py") == 1

    def test_deeply_nested_python(self):
        result = _python_candidates("app/services/project/contract_utils.py")
        assert "tests/test_contract_utils.py" in result
        assert "app/services/project/test_contract_utils.py" in result

    def test_deeply_nested_js(self):
        result = _js_candidates("src/features/auth/hooks/useAuth.ts")
        assert "src/features/auth/hooks/useAuth.test.ts" in result
        assert "src/features/auth/hooks/__tests__/useAuth.test.ts" in result
