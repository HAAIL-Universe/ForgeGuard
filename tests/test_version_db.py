"""Tests for version_db — version currency registry and checks."""

import pytest

from app.services.version_db import (
    LATEST_VERSIONS,
    check_all_dependencies,
    check_version_currency,
    get_all_version_info,
)


# ---------------------------------------------------------------------------
# check_version_currency
# ---------------------------------------------------------------------------


class TestCheckVersionCurrency:
    """Unit tests for individual version checks."""

    def test_known_current_version(self):
        result = check_version_currency("fastapi", "0.115")
        assert result["status"] == "current"
        assert result["package"] == "fastapi"
        assert "latest" in result

    def test_known_outdated_version(self):
        result = check_version_currency("django", "3.1")
        assert result["status"] == "outdated"
        assert "outdated" in result["detail"].lower()

    def test_eol_version(self):
        result = check_version_currency("python", "3.7.5")
        assert result["status"] == "eol"
        assert "end-of-life" in result["detail"].lower()

    def test_unknown_package(self):
        result = check_version_currency("some-obscure-package", "1.0")
        assert result["status"] == "unknown"
        assert result["package"] == "some-obscure-package"

    def test_none_version(self):
        result = check_version_currency("fastapi", None)
        assert result["status"] == "unknown"
        assert result["current"] == "unknown"

    def test_alias_resolution(self):
        """psycopg2 should resolve to postgresql."""
        result = check_version_currency("psycopg2", "15")
        assert result["package"] == "psycopg2"
        # Should be resolved against postgresql registry
        assert result["latest"] is not None

    def test_react_current(self):
        result = check_version_currency("react", "18.3")
        assert result["status"] == "current"

    def test_react_eol(self):
        """React 16.x is EOL per the version registry."""
        result = check_version_currency("react", "16.8")
        assert result["status"] == "eol"

    def test_node_eol(self):
        result = check_version_currency("node", "16.0")
        assert result["status"] == "eol"


# ---------------------------------------------------------------------------
# check_all_dependencies
# ---------------------------------------------------------------------------


class TestCheckAllDependencies:
    """Tests for batch dependency checking."""

    def test_empty_inputs(self):
        result = check_all_dependencies()
        assert result == []

    def test_none_inputs(self):
        result = check_all_dependencies(None, None)
        assert result == []

    def test_python_deps_only(self):
        result = check_all_dependencies(
            py_deps={"fastapi": "0.115", "python": "3.12"},
        )
        assert len(result) == 2
        assert all(r["status"] == "current" for r in result)

    def test_mixed_deps(self):
        result = check_all_dependencies(
            py_deps={"python": "3.7.5"},
            node_deps={"react": "18.3"},
        )
        assert len(result) == 2
        statuses = {r["package"]: r["status"] for r in result}
        assert statuses["python"] == "eol"
        assert statuses["react"] == "current"

    def test_sort_order(self):
        """EOL items should sort before outdated, which sorts before current."""
        result = check_all_dependencies(
            py_deps={"python": "3.7.5", "fastapi": "0.115", "django": "3.1"},
        )
        statuses = [r["status"] for r in result]
        assert statuses.index("eol") < statuses.index("current")

    def test_unknown_packages(self):
        result = check_all_dependencies(
            py_deps={"unknown-lib": "1.0"},
        )
        assert len(result) == 1
        assert result[0]["status"] == "unknown"


# ---------------------------------------------------------------------------
# get_all_version_info
# ---------------------------------------------------------------------------


class TestGetAllVersionInfo:
    """Tests for registry dump."""

    def test_returns_dict(self):
        info = get_all_version_info()
        assert isinstance(info, dict)
        assert len(info) > 20  # We have ~35 entries

    def test_all_entries_have_latest(self):
        info = get_all_version_info()
        for name, data in info.items():
            assert "latest" in data, f"{name} missing 'latest'"
            assert "category" in data, f"{name} missing 'category'"

    def test_registry_not_empty(self):
        assert len(LATEST_VERSIONS) > 0
