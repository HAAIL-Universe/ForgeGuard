"""Tests for the audit engine -- pure analysis checks."""

import pytest

from app.audit.engine import (
    check_boundary_compliance,
    check_dependency_gate,
    check_secrets_scan,
    check_python_syntax,
    run_all_checks,
)


class TestBoundaryCompliance:
    def test_pass_when_no_boundaries(self):
        result = check_boundary_compliance({"app/main.py": "import os"}, None)
        assert result["result"] == "PASS"
        assert result["check_code"] == "A4"

    def test_pass_when_no_violations(self):
        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "DB in repos"}
                    ],
                }
            ]
        }
        files = {"app/api/routers/health.py": "from fastapi import APIRouter"}
        result = check_boundary_compliance(files, boundaries)
        assert result["result"] == "PASS"

    def test_fail_on_violation(self):
        boundaries = {
            "layers": [
                {
                    "name": "routers",
                    "glob": "app/api/routers/*.py",
                    "forbidden": [
                        {"pattern": "asyncpg", "reason": "DB in repos"}
                    ],
                }
            ]
        }
        files = {"app/api/routers/bad.py": "import asyncpg"}
        result = check_boundary_compliance(files, boundaries)
        assert result["result"] == "FAIL"
        assert "asyncpg" in result["detail"]


class TestDependencyGate:
    def test_pass_normal_imports(self):
        files = {"app/main.py": "from fastapi import FastAPI\nimport os"}
        result = check_dependency_gate(files)
        assert result["result"] == "PASS"
        assert result["check_code"] == "A9"

    def test_fail_wildcard_import(self):
        files = {"app/bad.py": "from os import *"}
        result = check_dependency_gate(files)
        assert result["result"] == "FAIL"
        assert "wildcard" in result["detail"].lower()


class TestSecretsScan:
    def test_pass_clean_files(self):
        files = {"app/main.py": "x = 42\nprint('hello')"}
        result = check_secrets_scan(files)
        assert result["result"] == "PASS"
        assert result["check_code"] == "W1"

    def test_warn_on_hardcoded_password(self):
        files = {"config.py": 'password = "hunter2"'}
        result = check_secrets_scan(files)
        assert result["result"] == "WARN"
        assert "password" in result["detail"].lower()

    def test_warn_on_aws_key(self):
        files = {"creds.py": "key = 'AKIAIOSFODNN7EXAMPLE'"}
        result = check_secrets_scan(files)
        assert result["result"] == "WARN"
        assert "AWS" in result["detail"]

    def test_skip_lockfiles(self):
        files = {"package-lock.json": 'AKIAIOSFODNN7EXAMPLE'}
        result = check_secrets_scan(files)
        assert result["result"] == "PASS"


class TestPythonSyntax:
    def test_pass_clean_python(self):
        files = {"app/main.py": "def ok():\n    return 1"}
        result = check_python_syntax(files)
        assert result["result"] == "PASS"
        assert result["check_code"] == "A0"

    def test_fail_bad_python(self):
        files = {"app/bad.py": "def oops(:\n    pass"}
        result = check_python_syntax(files)
        assert result["result"] == "FAIL"
        assert "bad.py" in result["detail"]


class TestRunAllChecks:
    def test_returns_four_results(self):
        results = run_all_checks({"app/main.py": "import os"}, None)
        assert len(results) == 4
        codes = {r["check_code"] for r in results}
        assert codes == {"A0", "A4", "A9", "W1"}
