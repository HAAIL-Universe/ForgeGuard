"""Tests for scoped-package dependency checking in integration audit."""

import json
import tempfile
from pathlib import Path

import pytest

from app.services.build.integration_audit import _check_ts_imports_regex


@pytest.fixture()
def workspace(tmp_path: Path):
    """Workspace with a package.json containing scoped and unscoped deps."""
    pkg = {
        "name": "test-app",
        "dependencies": {"react": "^18.2.0"},
        "devDependencies": {
            "@vitejs/plugin-react": "^4.2.1",
            "@testing-library/react": "^14.1.2",
            "vite": "^5.0.8",
            "typescript": "^5.3.3",
        },
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    return tmp_path


class TestScopedPackageMatching:
    """Scoped npm packages (@scope/name) should match correctly."""

    def test_scoped_package_no_false_positive(self, workspace: Path):
        """@vitejs/plugin-react is listed — should NOT produce an issue."""
        chunk = {
            "vite.config.ts": "import react from '@vitejs/plugin-react';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        assert len(dep_issues) == 0, f"False positive: {dep_issues[0].message}"

    def test_scoped_package_subpath_no_false_positive(self, workspace: Path):
        """@testing-library/react/pure is a subpath — should still match."""
        chunk = {
            "test.tsx": "import { render } from '@testing-library/react/pure';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        assert len(dep_issues) == 0, f"False positive: {dep_issues[0].message}"

    def test_scoped_package_missing_detected(self, workspace: Path):
        """@unknown/package is NOT listed — should produce an issue."""
        chunk = {
            "app.tsx": "import foo from '@unknown/package';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        assert len(dep_issues) == 1
        assert "@unknown/package" in dep_issues[0].message

    def test_unscoped_package_still_works(self, workspace: Path):
        """Regular packages like 'lodash' should still match by first segment."""
        chunk = {
            "util.ts": "import merge from 'lodash/merge';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        # lodash is NOT in our package.json — should be detected
        assert len(dep_issues) == 1
        assert "lodash" in dep_issues[0].message

    def test_unscoped_package_present_no_issue(self, workspace: Path):
        """react is listed — should NOT produce an issue."""
        chunk = {
            "app.tsx": "import React from 'react';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        assert len(dep_issues) == 0

    def test_scoped_namespace_only_not_matched(self, workspace: Path):
        """Bare @vitejs (no subpackage) — edge case, should be detected as missing."""
        chunk = {
            "app.ts": "import x from '@vitejs';\n",
        }
        issues = _check_ts_imports_regex(workspace, chunk, chunk, str(workspace))
        dep_issues = [i for i in issues if i.check_name == "missing_js_dependency"]
        # "@vitejs" alone is NOT a real package — should be flagged
        assert len(dep_issues) == 1
