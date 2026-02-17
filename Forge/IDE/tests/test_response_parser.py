"""Tests for forge_ide.response_parser — LLM response classification and cleaning."""

from __future__ import annotations

import pytest

from forge_ide.response_parser import (
    ParsedResponse,
    classify_response,
    ensure_trailing_newline,
    parse_response,
    strip_fences,
)


# ===================================================================
# Fixtures — sample responses
# ===================================================================

SIMPLE_DIFF = """\
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+import sys
 
 def main():
"""

FENCED_DIFF = """\
```diff
--- a/src/main.py
+++ b/src/main.py
@@ -1,3 +1,4 @@
 import os
+import sys
 
 def main():
```
"""

MULTI_HUNK_DIFF = """\
--- a/app.py
+++ b/app.py
@@ -1,4 +1,5 @@
 import os
+import sys
 
 x = 1
 y = 2
@@ -10,3 +11,4 @@
 def foo():
     pass
+    return 42
"""

FULL_CONTENT = """\
import os
import sys

def main():
    print("hello")
"""

FENCED_CONTENT = """\
```python
import os
import sys

def main():
    print("hello")
```
"""

MARKDOWN_HR = """\
Some text

---

More text
"""

CODE_WITH_AT_SIGN = """\
# This has @@ in a comment but is not a diff
x = "@@ -1,3 +1,4 @@"
print(x)
"""


# ===================================================================
# classify_response
# ===================================================================


class TestClassifyResponse:
    def test_simple_diff(self):
        assert classify_response(SIMPLE_DIFF) == "diff"

    def test_multi_hunk_diff(self):
        assert classify_response(MULTI_HUNK_DIFF) == "diff"

    def test_full_content(self):
        assert classify_response(FULL_CONTENT) == "full_content"

    def test_empty_string(self):
        assert classify_response("") == "full_content"

    def test_markdown_hr_not_diff(self):
        """A standalone --- (horizontal rule) should not classify as diff."""
        assert classify_response(MARKDOWN_HR) == "full_content"

    def test_only_hunk_header(self):
        """Only @@ without --- / +++ is not a diff."""
        text = "@@ -1,3 +1,4 @@\n+new line\n"
        assert classify_response(text) == "full_content"

    def test_only_file_headers(self):
        """--- and +++ without @@ hunk header is not a diff."""
        text = "--- a/file.py\n+++ b/file.py\n"
        assert classify_response(text) == "full_content"

    def test_diff_in_code_comment(self):
        """@@ in a string literal without proper headers is full_content."""
        assert classify_response(CODE_WITH_AT_SIGN) == "full_content"

    def test_diff_with_whitespace(self):
        """Diff with extra whitespace around markers still detected."""
        text = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n"
        assert classify_response(text) == "diff"


# ===================================================================
# strip_fences
# ===================================================================


class TestStripFences:
    def test_fenced_python(self):
        result = strip_fences(FENCED_CONTENT)
        assert "```" not in result
        assert "import os" in result

    def test_fenced_diff(self):
        result = strip_fences(FENCED_DIFF)
        assert "```diff" not in result
        assert "--- a/src/main.py" in result

    def test_no_fences(self):
        assert strip_fences(FULL_CONTENT) == FULL_CONTENT

    def test_empty_string(self):
        assert strip_fences("") == ""

    def test_only_opening_fence(self):
        text = "```python\nimport os\n"
        assert strip_fences(text) == text  # no closing fence → unchanged

    def test_nested_fences(self):
        """Only outermost fences removed."""
        text = "```\nouter\n```inner```\nmore\n```"
        result = strip_fences(text)
        # Outer fences removed, inner backticks preserved
        assert "outer" in result
        assert "inner" in result

    def test_fence_with_trailing_whitespace(self):
        text = "```python  \ncode\n```  \n"
        result = strip_fences(text)
        assert "```" not in result
        assert "code" in result

    def test_content_before_fence(self):
        text = "prefix\n```\ncode\n```\nsuffix"
        result = strip_fences(text)
        assert "prefix" in result
        assert "code" in result
        assert "suffix" in result
        assert "```" not in result

    def test_plain_backticks_in_content(self):
        """Triple backticks on non-start-of-line preserved."""
        text = "some ``` text ``` here"
        assert strip_fences(text) == text


# ===================================================================
# ensure_trailing_newline
# ===================================================================


class TestEnsureTrailingNewline:
    def test_already_has_newline(self):
        assert ensure_trailing_newline("hello\n") == "hello\n"

    def test_missing_newline(self):
        assert ensure_trailing_newline("hello") == "hello\n"

    def test_empty_string(self):
        assert ensure_trailing_newline("") == ""

    def test_only_newline(self):
        assert ensure_trailing_newline("\n") == "\n"

    def test_multiple_trailing_newlines(self):
        assert ensure_trailing_newline("x\n\n") == "x\n\n"


# ===================================================================
# parse_response
# ===================================================================


class TestParseResponse:
    def test_full_content(self):
        result = parse_response(FULL_CONTENT)
        assert result.kind == "full_content"
        assert result.raw == FULL_CONTENT
        assert result.cleaned.endswith("\n")

    def test_diff(self):
        result = parse_response(SIMPLE_DIFF)
        assert result.kind == "diff"
        assert result.raw == SIMPLE_DIFF
        assert "--- a/src/main.py" in result.cleaned

    def test_fenced_diff(self):
        """Fences stripped before classification."""
        result = parse_response(FENCED_DIFF)
        assert result.kind == "diff"
        assert "```" not in result.cleaned

    def test_fenced_content(self):
        """Fenced full content → fences stripped, trailing newline ensured."""
        result = parse_response(FENCED_CONTENT)
        assert result.kind == "full_content"
        assert "```" not in result.cleaned
        assert result.cleaned.endswith("\n")

    def test_empty_input(self):
        result = parse_response("")
        assert result.kind == "full_content"
        assert result.cleaned == ""

    def test_model_frozen(self):
        result = parse_response("hello")
        with pytest.raises(Exception):
            result.kind = "diff"  # type: ignore[misc]

    def test_preserves_raw(self):
        result = parse_response(FENCED_CONTENT)
        assert result.raw == FENCED_CONTENT
        assert result.raw != result.cleaned
