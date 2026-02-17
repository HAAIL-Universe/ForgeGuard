"""Tests for forge_ide.redactor — secret detection and redaction."""

from __future__ import annotations

import re

import pytest

from forge_ide.redactor import (
    DEFAULT_PATTERNS,
    REDACTED,
    SecretMatch,
    find_secrets,
    has_secrets,
    redact,
)


# ── Model tests ──────────────────────────────────────────────────────────

class TestSecretMatchModel:
    """SecretMatch is frozen and has correct fields."""

    def test_frozen(self) -> None:
        sm = SecretMatch(pattern_name="test", start=0, end=5)
        with pytest.raises(Exception):
            sm.pattern_name = "other"  # type: ignore[misc]

    def test_fields(self) -> None:
        sm = SecretMatch(pattern_name="api_key_sk", start=10, end=40)
        assert sm.pattern_name == "api_key_sk"
        assert sm.start == 10
        assert sm.end == 40


# ── redact() — individual pattern tests ─────────────────────────────────

class TestRedactPatterns:
    """Each default pattern category is detected and redacted."""

    def test_sk_key(self) -> None:
        text = "token: sk-abcdefghij0123456789extra"
        result = redact(text)
        assert REDACTED in result
        assert "sk-" not in result

    def test_key_key(self) -> None:
        text = "api: key-ABCDEFghij0123456789xyz"
        result = redact(text)
        assert REDACTED in result
        assert "key-" not in result

    def test_github_pat_ghp(self) -> None:
        pat = "ghp_" + "A" * 36
        text = f"token={pat}"
        result = redact(text)
        assert REDACTED in result
        assert "ghp_" not in result

    def test_github_pat_gho(self) -> None:
        pat = "gho_" + "B" * 36
        text = f"oauth={pat}"
        result = redact(text)
        assert REDACTED in result
        assert "gho_" not in result

    def test_aws_key(self) -> None:
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = redact(text)
        assert REDACTED in result
        assert "AKIA" not in result

    def test_jwt(self) -> None:
        # Minimal valid JWT structure: header.payload.signature
        header = "eyJ" + "a" * 20
        payload = "eyJ" + "b" * 20
        sig = "c" * 22
        jwt_token = f"{header}.{payload}.{sig}"
        text = f"Authorization: Bearer {jwt_token}"
        result = redact(text)
        assert "eyJ" not in result

    def test_postgresql_conn(self) -> None:
        text = "dsn=postgresql://user:pass@host:5432/db"
        result = redact(text)
        assert REDACTED in result
        assert "postgresql://" not in result

    def test_mysql_conn(self) -> None:
        text = "dsn=mysql://user:pass@host:3306/db"
        result = redact(text)
        assert REDACTED in result
        assert "mysql://" not in result

    def test_password_at(self) -> None:
        text = "url=http://user:s3cret@host.com"
        result = redact(text)
        assert REDACTED in result
        assert "s3cret" not in result

    def test_password_eq(self) -> None:
        text = "PASSWORD=supersecret123"
        result = redact(text)
        assert REDACTED in result
        assert "supersecret123" not in result

    def test_passwd_eq(self) -> None:
        text = "passwd = mypass"
        result = redact(text)
        assert REDACTED in result
        assert "mypass" not in result

    def test_pwd_eq(self) -> None:
        text = "pwd=hidden"
        result = redact(text)
        assert REDACTED in result
        assert "hidden" not in result

    def test_secret_eq(self) -> None:
        text = "SECRET=mysecretvalue"
        result = redact(text)
        assert REDACTED in result
        assert "mysecretvalue" not in result

    def test_bearer_token(self) -> None:
        token = "A" * 30
        text = f"Authorization: Bearer {token}"
        result = redact(text)
        assert REDACTED in result
        assert token not in result

    def test_pem_block(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        result = redact(text)
        assert REDACTED in result
        assert "-----BEGIN" not in result


# ── redact() — edge cases ───────────────────────────────────────────────

class TestRedactEdgeCases:
    """Edge-case behaviour for redact()."""

    def test_no_secrets_unchanged(self) -> None:
        text = "This is perfectly clean text with no secrets."
        assert redact(text) == text

    def test_empty_string(self) -> None:
        assert redact("") == ""

    def test_multiple_secrets_in_one_string(self) -> None:
        sk = "sk-" + "A" * 20
        aws = "AKIA" + "B" * 16
        text = f"key1={sk} key2={aws}"
        result = redact(text)
        assert result.count(REDACTED) == 2
        assert sk not in result
        assert aws not in result

    def test_extra_patterns(self) -> None:
        custom = [("custom_token", re.compile(r"tok_[a-z]{10}"))]
        text = "auth: tok_abcdefghij"
        result = redact(text, extra_patterns=custom)
        assert REDACTED in result
        assert "tok_" not in result

    def test_extra_patterns_combined_with_defaults(self) -> None:
        custom = [("custom_token", re.compile(r"tok_[a-z]{10}"))]
        sk = "sk-" + "C" * 20
        text = f"a={sk} b=tok_abcdefghij"
        result = redact(text, extra_patterns=custom)
        assert result.count(REDACTED) == 2


# ── False positive avoidance ────────────────────────────────────────────

class TestFalsePositives:
    """Short values that look like patterns but shouldn't match."""

    def test_short_sk_not_matched(self) -> None:
        text = "sk-abc"  # Only 3 chars after prefix, need 20+
        assert redact(text) == text

    def test_short_key_not_matched(self) -> None:
        text = "key-short"  # Only 5 chars, need 20+
        assert redact(text) == text

    def test_short_ghp_not_matched(self) -> None:
        text = "ghp_short"  # Only 5 chars, need 36+
        assert redact(text) == text

    def test_password_at_short(self) -> None:
        # Less than 4 chars between : and @ — shouldn't match
        text = "http://u:ab@host"
        assert redact(text) == text


# ── has_secrets() ────────────────────────────────────────────────────────

class TestHasSecrets:
    """Boolean detection of secrets."""

    def test_true_when_present(self) -> None:
        text = f"key=sk-{'A' * 20}"
        assert has_secrets(text) is True

    def test_false_when_clean(self) -> None:
        assert has_secrets("nothing sensitive here") is False

    def test_with_extra_patterns(self) -> None:
        custom = [("x", re.compile(r"xtoken_\w{10}"))]
        assert has_secrets("xtoken_abcdefghij", extra_patterns=custom) is True


# ── find_secrets() ──────────────────────────────────────────────────────

class TestFindSecrets:
    """Detailed match listing."""

    def test_returns_matches_with_correct_fields(self) -> None:
        sk = "sk-" + "D" * 20
        text = f"val={sk}"
        matches = find_secrets(text)
        assert len(matches) >= 1
        m = matches[0]
        assert m.pattern_name == "api_key_sk"
        assert m.start == 4
        assert m.end == 4 + len(sk)

    def test_multiple_matches_in_order(self) -> None:
        sk = "sk-" + "E" * 20
        aws = "AKIA" + "F" * 16
        text = f"{sk} {aws}"
        matches = find_secrets(text)
        assert len(matches) >= 2
        assert matches[0].start < matches[1].start

    def test_empty_on_clean_text(self) -> None:
        assert find_secrets("clean text") == []


# ── Constants ────────────────────────────────────────────────────────────

class TestConstants:
    """DEFAULT_PATTERNS and REDACTED are sensible."""

    def test_default_patterns_count(self) -> None:
        assert len(DEFAULT_PATTERNS) == 12

    def test_redacted_value(self) -> None:
        assert REDACTED == "[REDACTED]"

    def test_all_patterns_are_compiled(self) -> None:
        for name, pat in DEFAULT_PATTERNS:
            assert isinstance(name, str)
            assert isinstance(pat, re.Pattern)
