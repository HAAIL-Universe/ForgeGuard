"""Tests for forge_ide.lang.ts_intel — TypeScript/JS parsers and extractors."""

from __future__ import annotations

import json

import pytest

from forge_ide.contracts import Diagnostic
from forge_ide.errors import ParseError
from forge_ide.lang import Symbol
from forge_ide.lang.ts_intel import (
    extract_symbols,
    parse_eslint_json,
    parse_tsc_output,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — representative tool outputs
# ═══════════════════════════════════════════════════════════════════════════

TSC_SINGLE_ERROR = (
    "src/app.ts(10,5): error TS2304: Cannot find name 'foo'."
)

TSC_MULTI = """\
src/app.ts(10,5): error TS2304: Cannot find name 'foo'.
src/utils.ts(3,1): error TS1005: ';' expected.
src/app.ts(25,10): warning TS6133: 'bar' is declared but its value is never read."""

ESLINT_OUTPUT = json.dumps([
    {
        "filePath": "src/app.ts",
        "messages": [
            {
                "ruleId": "no-unused-vars",
                "severity": 2,
                "message": "'x' is defined but never used",
                "line": 5,
                "column": 7,
            },
            {
                "ruleId": "semi",
                "severity": 1,
                "message": "Missing semicolon",
                "line": 10,
                "column": 20,
            },
        ],
    },
    {
        "filePath": "src/utils.ts",
        "messages": [],
    },
])

TS_SOURCE = """\
// TypeScript sample

export function handleRequest(req: Request): Response {
    return { status: 200 };
}

export class UserService {
    private db: Database;

    constructor(db: Database) {
        this.db = db;
    }

    async getUser(id: string): Promise<User> {
        return this.db.find(id);
    }
}

export interface User {
    id: string;
    name: string;
    email: string;
}

export type UserId = string;

export enum Role {
    Admin = 'admin',
    User = 'user',
    Guest = 'guest',
}

export const MAX_RETRIES = 3;

function internalHelper(): void {
    // not exported
}

export default class App {
    start() {}
}
"""


# ═══════════════════════════════════════════════════════════════════════════
# parse_tsc_output
# ═══════════════════════════════════════════════════════════════════════════


class TestParseTscOutput:
    def test_empty_input(self):
        assert parse_tsc_output("") == []

    def test_whitespace(self):
        assert parse_tsc_output("  \n  ") == []

    def test_single_error(self):
        diags = parse_tsc_output(TSC_SINGLE_ERROR)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "src/app.ts"
        assert d.line == 10
        assert d.column == 5
        assert d.severity == "error"
        assert d.code == "TS2304"
        assert "foo" in d.message

    def test_multi_errors(self):
        diags = parse_tsc_output(TSC_MULTI)
        assert len(diags) == 3
        assert diags[0].file == "src/app.ts"
        assert diags[1].file == "src/utils.ts"
        assert diags[2].severity == "warning"

    def test_no_match_lines_skipped(self):
        raw = "Some info line\n" + TSC_SINGLE_ERROR + "\nAnother info line"
        diags = parse_tsc_output(raw)
        assert len(diags) == 1

    def test_clean_output(self):
        """tsc with no errors produces no diagnostics."""
        assert parse_tsc_output("Found 0 errors.\n") == []


# ═══════════════════════════════════════════════════════════════════════════
# parse_eslint_json
# ═══════════════════════════════════════════════════════════════════════════


class TestParseEslintJson:
    def test_empty_input(self):
        assert parse_eslint_json("") == []

    def test_whitespace(self):
        assert parse_eslint_json("  \n  ") == []

    def test_errors_and_warnings(self):
        diags = parse_eslint_json(ESLINT_OUTPUT)
        assert len(diags) == 2
        assert diags[0].severity == "error"
        assert diags[0].code == "no-unused-vars"
        assert diags[0].file == "src/app.ts"
        assert diags[1].severity == "warning"
        assert diags[1].code == "semi"

    def test_empty_messages(self):
        raw = json.dumps([{"filePath": "clean.ts", "messages": []}])
        assert parse_eslint_json(raw) == []

    def test_empty_array(self):
        assert parse_eslint_json("[]") == []

    def test_invalid_json(self):
        with pytest.raises(ParseError):
            parse_eslint_json("{broken")

    def test_non_array(self):
        with pytest.raises(ParseError):
            parse_eslint_json('{"not": "array"}')

    def test_severity_1_is_warning(self):
        raw = json.dumps([{
            "filePath": "f.ts",
            "messages": [{"ruleId": "r1", "severity": 1, "message": "m", "line": 1, "column": 1}],
        }])
        diags = parse_eslint_json(raw)
        assert diags[0].severity == "warning"

    def test_severity_2_is_error(self):
        raw = json.dumps([{
            "filePath": "f.ts",
            "messages": [{"ruleId": "r1", "severity": 2, "message": "m", "line": 1, "column": 1}],
        }])
        diags = parse_eslint_json(raw)
        assert diags[0].severity == "error"

    def test_no_rule_id(self):
        raw = json.dumps([{
            "filePath": "f.ts",
            "messages": [{"severity": 2, "message": "m", "line": 1, "column": 1}],
        }])
        diags = parse_eslint_json(raw)
        assert diags[0].code is None


# ═══════════════════════════════════════════════════════════════════════════
# extract_symbols
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractTsSymbols:
    def test_empty_source(self):
        assert extract_symbols("") == []

    def test_whitespace(self):
        assert extract_symbols("  \n  ") == []

    def test_export_function(self):
        symbols = extract_symbols(TS_SOURCE)
        funcs = [s for s in symbols if s.kind == "function"]
        names = {s.name for s in funcs}
        assert "handleRequest" in names

    def test_export_class(self):
        symbols = extract_symbols(TS_SOURCE)
        classes = [s for s in symbols if s.kind == "class"]
        names = {s.name for s in classes}
        assert "UserService" in names
        assert "App" in names

    def test_interface(self):
        symbols = extract_symbols(TS_SOURCE)
        ifaces = [s for s in symbols if s.kind == "interface"]
        assert any(s.name == "User" for s in ifaces)

    def test_type_alias(self):
        symbols = extract_symbols(TS_SOURCE)
        types = [s for s in symbols if s.kind == "type_alias"]
        assert any(s.name == "UserId" for s in types)

    def test_enum(self):
        symbols = extract_symbols(TS_SOURCE)
        enums = [s for s in symbols if s.kind == "enum"]
        assert any(s.name == "Role" for s in enums)

    def test_constant(self):
        symbols = extract_symbols(TS_SOURCE)
        consts = [s for s in symbols if s.kind == "constant"]
        assert any(s.name == "MAX_RETRIES" for s in consts)

    def test_non_export_function(self):
        symbols = extract_symbols(TS_SOURCE)
        names = {s.name for s in symbols}
        assert "internalHelper" in names  # regex catches non-export functions too

    def test_line_numbers(self):
        symbols = extract_symbols(TS_SOURCE)
        svc = next(s for s in symbols if s.name == "UserService")
        assert svc.start_line > 0
        assert svc.end_line > svc.start_line  # multi-line class

    def test_single_line_const(self):
        symbols = extract_symbols(TS_SOURCE)
        c = next(s for s in symbols if s.name == "MAX_RETRIES")
        assert c.start_line == c.end_line
