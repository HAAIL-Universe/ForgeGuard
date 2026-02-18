"""Tests for _strip_codeblock JSON extraction."""
import json
import pytest
from app.services.upgrade_executor import _strip_codeblock


def test_clean_json_passthrough():
    t = '{"changes": [], "status": "proposed"}'
    assert json.loads(_strip_codeblock(t))["status"] == "proposed"


def test_code_fence():
    t = '```json\n{"ok": true}\n```'
    assert json.loads(_strip_codeblock(t))["ok"] is True


def test_prose_preamble_then_json():
    t = (
        "I'll analyze the repository structure carefully and produce "
        "precise, production-quality Docker containerization changes.\n\n"
        '{"changes": [{"file": "Dockerfile"}], "status": "proposed"}'
    )
    parsed = json.loads(_strip_codeblock(t))
    assert parsed["status"] == "proposed"
    assert parsed["changes"][0]["file"] == "Dockerfile"


def test_prose_preamble_then_code_fence():
    t = 'Here are the changes:\n```json\n{"ok": true}\n```'
    assert json.loads(_strip_codeblock(t))["ok"] is True


def test_array_extraction():
    t = 'Some preamble\n[{"a": 1}]'
    assert json.loads(_strip_codeblock(t))[0]["a"] == 1


def test_whitespace_only_json():
    t = '  \n  {"x": 42}  \n  '
    assert json.loads(_strip_codeblock(t))["x"] == 42
