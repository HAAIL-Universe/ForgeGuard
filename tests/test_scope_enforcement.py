"""Tests for strict scope enforcement — builder must adhere to planner's file list.

Covers:
- _audit_file_change returns REJECT for out-of-scope files
- _audit_file_change returns PASS for in-scope files
- _audit_file_change still returns FAIL for content issues (syntax, secrets)
- REJECT takes priority over content-level FAIL
- No planned_files (None) means no scope check
- Builder prompt includes strict scope rules and objections field
"""

from __future__ import annotations

import pytest

from app.services.upgrade_executor import (
    _audit_file_change,
    _BUILDER_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Scope compliance — REJECT verdict
# ---------------------------------------------------------------------------


class TestScopeEnforcement:
    """Out-of-scope files must be hard-rejected (REJECT), not just warned."""

    def test_out_of_scope_file_is_rejected(self):
        """A file not in planned_files must return REJECT."""
        change = {
            "file": "src/extra_helper.py",
            "action": "create",
            "after_snippet": "# helper\n",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py", "src/config.py"]
        )
        assert verdict == "REJECT"
        assert any("Scope deviation" in f for f in findings)

    def test_in_scope_file_passes(self):
        """A file in planned_files with valid content should PASS."""
        change = {
            "file": "src/main.py",
            "action": "modify",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py", "src/config.py"]
        )
        assert verdict == "PASS"
        assert findings == []

    def test_no_planned_files_skips_scope_check(self):
        """When planned_files is None, scope check is skipped."""
        change = {
            "file": "anything.py",
            "action": "create",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(change, planned_files=None)
        assert verdict == "PASS"
        assert findings == []

    def test_reject_takes_priority_over_syntax_error(self):
        """Out-of-scope file with syntax errors still gets REJECT
        (scope check short-circuits after adding the finding)."""
        change = {
            "file": "src/rogue.py",
            "action": "create",
            "after_snippet": "def broken(\n",  # syntax error
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py"]
        )
        assert verdict == "REJECT"
        assert any("Scope deviation" in f for f in findings)

    def test_syntax_error_still_fails_in_scope(self):
        """In-scope file with syntax error returns FAIL (not REJECT)."""
        change = {
            "file": "src/main.py",
            "action": "modify",
            "after_snippet": "def broken(\n",  # syntax error
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py"]
        )
        assert verdict == "FAIL"
        assert any("Syntax error" in f for f in findings)

    def test_delete_action_always_passes(self):
        """Deletions are always safe even if out of scope
        (content is empty for deletes)."""
        change = {
            "file": "src/rogue.py",
            "action": "delete",
            "after_snippet": "",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py"]
        )
        assert verdict == "PASS"
        assert findings == []

    def test_empty_planned_files_list_rejects_everything(self):
        """An empty list (not None) means every file is out of scope."""
        change = {
            "file": "src/main.py",
            "action": "create",
            "after_snippet": "x = 1\n",
        }
        verdict, findings = _audit_file_change(change, planned_files=[])
        assert verdict == "REJECT"

    def test_wildcard_import_still_caught_in_scope(self):
        """In-scope file with wildcard import returns FAIL."""
        change = {
            "file": "src/main.py",
            "action": "modify",
            "after_snippet": "from os import *\nx = 1\n",
        }
        verdict, findings = _audit_file_change(
            change, planned_files=["src/main.py"]
        )
        assert verdict == "FAIL"
        assert any("Wildcard import" in f for f in findings)


# ---------------------------------------------------------------------------
# Builder prompt — strict scope rules + objections field
# ---------------------------------------------------------------------------


class TestBuilderPrompt:
    """Verify the builder prompt enforces scope constraints."""

    def test_prompt_has_strict_scope_rules(self):
        assert "STRICT SCOPE RULES" in _BUILDER_SYSTEM_PROMPT

    def test_prompt_forbids_extra_files(self):
        assert "Do NOT" in _BUILDER_SYSTEM_PROMPT
        assert "helper files" in _BUILDER_SYSTEM_PROMPT

    def test_prompt_has_objections_field(self):
        assert '"objections"' in _BUILDER_SYSTEM_PROMPT

    def test_prompt_explains_objection_purpose(self):
        assert "human operator" in _BUILDER_SYSTEM_PROMPT

    def test_prompt_forbids_silent_additions(self):
        assert "Do NOT silently add files" in _BUILDER_SYSTEM_PROMPT
