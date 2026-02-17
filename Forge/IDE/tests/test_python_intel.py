"""Tests for forge_ide.lang.python_intel — Python parsers and extractors."""

from __future__ import annotations

import json

import pytest

from forge_ide.contracts import Diagnostic
from forge_ide.errors import ParseError
from forge_ide.lang import ImportInfo, Symbol
from forge_ide.lang.python_intel import (
    PYTHON_STDLIB_MODULES,
    extract_symbols,
    parse_pyright_json,
    parse_python_ast_errors,
    parse_ruff_json,
    resolve_imports,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — representative tool outputs
# ═══════════════════════════════════════════════════════════════════════════

RUFF_SINGLE_ERROR = json.dumps([
    {
        "code": "F821",
        "message": "Undefined name 'foo'",
        "filename": "app/main.py",
        "location": {"row": 10, "column": 5},
        "end_location": {"row": 10, "column": 8},
    },
])

RUFF_MULTI = json.dumps([
    {
        "code": "E501",
        "message": "Line too long (120 > 88)",
        "filename": "app/main.py",
        "location": {"row": 5, "column": 89},
        "end_location": {"row": 5, "column": 120},
    },
    {
        "code": "I001",
        "message": "Import block is un-sorted",
        "filename": "app/main.py",
        "location": {"row": 1, "column": 1},
        "end_location": {"row": 3, "column": 1},
    },
])

PYRIGHT_OUTPUT = json.dumps({
    "generalDiagnostics": [
        {
            "file": "app/config.py",
            "severity": "error",
            "message": "Cannot find module 'missing_pkg'",
            "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 15}},
            "rule": "reportMissingImports",
        },
        {
            "file": "app/config.py",
            "severity": "warning",
            "message": "Variable 'x' is not accessed",
            "range": {"start": {"line": 10, "character": 4}, "end": {"line": 10, "character": 5}},
            "rule": "reportUnusedVariable",
        },
        {
            "file": "app/config.py",
            "severity": "information",
            "message": "Type of 'y' is 'int'",
            "range": {"start": {"line": 15, "character": 0}, "end": {"line": 15, "character": 1}},
            "rule": "reportGeneralIssues",
        },
    ],
})

SAMPLE_PYTHON_SOURCE = '''\
"""Module docstring."""

import os
from collections import OrderedDict

MAX_SIZE = 100
name: str = "hello"

class MyClass:
    """A sample class."""

    def method_one(self):
        pass

    async def method_two(self):
        pass

    class InnerClass:
        pass

def standalone_func():
    return 42

async def async_func():
    return True

_private = "hidden"
'''

IMPORT_SOURCE = '''\
import os
import json
from collections import OrderedDict
from app.config import Settings
from . import utils
from ..shared import helpers
from typing import Optional
'''


# ═══════════════════════════════════════════════════════════════════════════
# parse_ruff_json
# ═══════════════════════════════════════════════════════════════════════════


class TestParseRuffJson:
    def test_empty_input(self):
        assert parse_ruff_json("") == []

    def test_whitespace(self):
        assert parse_ruff_json("  \n  ") == []

    def test_single_error(self):
        diags = parse_ruff_json(RUFF_SINGLE_ERROR)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "app/main.py"
        assert d.line == 10
        assert d.column == 5
        assert d.severity == "error"  # F-codes → error
        assert d.code == "F821"

    def test_multi_entries(self):
        diags = parse_ruff_json(RUFF_MULTI)
        assert len(diags) == 2
        assert diags[0].severity == "warning"  # E501
        assert diags[1].severity == "info"      # I001

    def test_empty_array(self):
        assert parse_ruff_json("[]") == []

    def test_invalid_json(self):
        with pytest.raises(ParseError):
            parse_ruff_json("{broken")

    def test_non_array(self):
        with pytest.raises(ParseError):
            parse_ruff_json('{"not": "an array"}')

    def test_w_code_severity(self):
        raw = json.dumps([{
            "code": "W291", "message": "trailing whitespace",
            "filename": "f.py", "location": {"row": 1, "column": 1},
        }])
        diags = parse_ruff_json(raw)
        assert diags[0].severity == "warning"


# ═══════════════════════════════════════════════════════════════════════════
# parse_pyright_json
# ═══════════════════════════════════════════════════════════════════════════


class TestParsePyrightJson:
    def test_empty_input(self):
        assert parse_pyright_json("") == []

    def test_errors_and_warnings(self):
        diags = parse_pyright_json(PYRIGHT_OUTPUT)
        assert len(diags) == 3
        assert diags[0].severity == "error"
        assert diags[0].line == 3  # 0-based line 2 → 1-based 3
        assert diags[0].code == "reportMissingImports"
        assert diags[1].severity == "warning"
        assert diags[2].severity == "info"

    def test_empty_diagnostics(self):
        raw = json.dumps({"generalDiagnostics": []})
        assert parse_pyright_json(raw) == []

    def test_missing_general_diagnostics(self):
        raw = json.dumps({"version": "1.0"})
        assert parse_pyright_json(raw) == []

    def test_invalid_json(self):
        with pytest.raises(ParseError):
            parse_pyright_json("not json")

    def test_non_dict(self):
        with pytest.raises(ParseError):
            parse_pyright_json("[1, 2, 3]")

    def test_hint_severity(self):
        raw = json.dumps({"generalDiagnostics": [{
            "file": "f.py", "severity": "hint", "message": "hint msg",
            "range": {"start": {"line": 0, "character": 0}},
        }]})
        diags = parse_pyright_json(raw)
        assert diags[0].severity == "hint"


# ═══════════════════════════════════════════════════════════════════════════
# parse_python_ast_errors
# ═══════════════════════════════════════════════════════════════════════════


class TestParsePythonAstErrors:
    def test_valid_code(self):
        assert parse_python_ast_errors("x = 1\n") == []

    def test_syntax_error(self):
        diags = parse_python_ast_errors("def f(\n", path="broken.py")
        assert len(diags) == 1
        assert diags[0].severity == "error"
        assert diags[0].code == "SyntaxError"
        assert diags[0].file == "broken.py"

    def test_indentation_error(self):
        diags = parse_python_ast_errors("def f():\nx = 1\n")
        assert len(diags) == 1
        assert diags[0].severity == "error"

    def test_empty_source(self):
        assert parse_python_ast_errors("") == []

    def test_whitespace_only(self):
        assert parse_python_ast_errors("  \n  ") == []


# ═══════════════════════════════════════════════════════════════════════════
# extract_symbols
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractSymbols:
    def test_empty_source(self):
        assert extract_symbols("") == []

    def test_syntax_error_returns_empty(self):
        assert extract_symbols("def (broken") == []

    def test_function(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        funcs = [s for s in symbols if s.kind == "function"]
        names = {s.name for s in funcs}
        assert "standalone_func" in names
        assert "async_func" in names

    def test_class(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        classes = [s for s in symbols if s.kind == "class" and s.parent is None]
        assert any(s.name == "MyClass" for s in classes)

    def test_method_with_parent(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        methods = [s for s in symbols if s.kind == "method"]
        assert any(s.name == "method_one" and s.parent == "MyClass" for s in methods)
        assert any(s.name == "method_two" and s.parent == "MyClass" for s in methods)

    def test_nested_class(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        nested = [s for s in symbols if s.name == "InnerClass"]
        assert len(nested) == 1
        assert nested[0].parent == "MyClass"

    def test_module_variable(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        vars_ = [s for s in symbols if s.kind == "variable"]
        assert any(s.name == "name" for s in vars_)

    def test_constant(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        consts = [s for s in symbols if s.kind == "constant"]
        assert any(s.name == "MAX_SIZE" for s in consts)

    def test_private_excluded(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        names = {s.name for s in symbols}
        assert "_private" not in names

    def test_line_numbers(self):
        symbols = extract_symbols(SAMPLE_PYTHON_SOURCE)
        cls = next(s for s in symbols if s.name == "MyClass")
        assert cls.start_line > 0
        assert cls.end_line >= cls.start_line


# ═══════════════════════════════════════════════════════════════════════════
# resolve_imports
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveImports:
    WORKSPACE_FILES = [
        "app/__init__.py",
        "app/config.py",
        "app/main.py",
        "app/utils.py",
        "app/shared/__init__.py",
        "app/shared/helpers.py",
    ]

    def test_empty_source(self):
        assert resolve_imports("", "f.py", []) == []

    def test_stdlib_import(self):
        infos = resolve_imports("import os\n", "app/main.py", self.WORKSPACE_FILES)
        assert len(infos) == 1
        assert infos[0].module == "os"
        assert infos[0].is_stdlib is True

    def test_from_stdlib(self):
        infos = resolve_imports(
            "from collections import OrderedDict\n",
            "app/main.py",
            self.WORKSPACE_FILES,
        )
        assert len(infos) == 1
        assert infos[0].is_stdlib is True
        assert infos[0].names == ["OrderedDict"]

    def test_workspace_import(self):
        infos = resolve_imports(
            "from app.config import Settings\n",
            "app/main.py",
            self.WORKSPACE_FILES,
        )
        assert len(infos) == 1
        assert infos[0].is_stdlib is False
        assert infos[0].resolved_path == "app/config.py"

    def test_relative_import(self):
        infos = resolve_imports(
            "from . import utils\n",
            "app/main.py",
            self.WORKSPACE_FILES,
        )
        assert len(infos) == 1
        assert infos[0].is_stdlib is False
        # Should resolve "." in "app/main.py" → "app" package → look for "app.utils"
        assert infos[0].resolved_path == "app/utils.py"

    def test_relative_parent_import(self):
        infos = resolve_imports(
            "from ..shared import helpers\n",
            "app/api/deps.py",
            self.WORKSPACE_FILES,
        )
        assert len(infos) == 1
        assert infos[0].module == "..shared"

    def test_third_party(self):
        infos = resolve_imports(
            "import fastapi\n",
            "app/main.py",
            self.WORKSPACE_FILES,
        )
        assert len(infos) == 1
        assert infos[0].is_stdlib is False
        assert infos[0].resolved_path is None

    def test_multiple_imports(self):
        infos = resolve_imports(IMPORT_SOURCE, "app/main.py", self.WORKSPACE_FILES)
        assert len(infos) >= 5
        modules = {i.module for i in infos}
        assert "os" in modules
        assert "json" in modules

    def test_syntax_error_returns_empty(self):
        assert resolve_imports("def (broken", "f.py", []) == []

    def test_stdlib_set_exists(self):
        assert "os" in PYTHON_STDLIB_MODULES
        assert "sys" in PYTHON_STDLIB_MODULES
        assert "json" in PYTHON_STDLIB_MODULES
        assert "pathlib" in PYTHON_STDLIB_MODULES
        assert len(PYTHON_STDLIB_MODULES) > 100
