"""Tests for certificate_renderer -- JSON / HTML / text rendering."""

import json
from unittest.mock import patch

import pytest

from app.services.certificate_renderer import (
    render_json,
    render_html,
    render_text,
    render_certificate,
    _compute_integrity_hash,
    _esc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_scores():
    """Return realistic CertificateScores for rendering."""
    return {
        "dimensions": {
            "build_integrity": {"score": 89, "weight": 0.20, "details": ["Build completed"]},
            "test_coverage": {"score": 95, "weight": 0.20, "details": ["10/10 checks passed"]},
            "audit_compliance": {"score": 100, "weight": 0.20, "details": ["All audits passed"]},
            "governance": {"score": 100, "weight": 0.15, "details": ["Clean sweep"]},
            "security": {"score": 100, "weight": 0.15, "details": ["Secrets scan clean"]},
            "cost_efficiency": {"score": 95, "weight": 0.10, "details": ["$1.50 total"]},
        },
        "overall_score": 96.3,
        "verdict": "CERTIFIED",
        "project": {"id": "p-1", "name": "TestProject", "description": None, "repo_full_name": "user/repo"},
        "build_summary": {
            "id": "b-1",
            "status": "completed",
            "phase": "plan_execute",
            "loop_count": 1,
            "files_written": 5,
            "git_commits": 2,
            "cost_usd": 1.50,
            "total_tokens": 60000,
        },
        "builds_total": 3,
        "contracts_count": 9,
        "generated_at": "2025-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _esc
# ---------------------------------------------------------------------------

def test_esc_basic():
    assert _esc("<b>hello</b>") == "&lt;b&gt;hello&lt;/b&gt;"

def test_esc_ampersand():
    assert _esc("a & b") == "a &amp; b"

def test_esc_quote():
    assert _esc('"test"') == "&quot;test&quot;"


# ---------------------------------------------------------------------------
# _compute_integrity_hash
# ---------------------------------------------------------------------------

@patch("app.services.certificate_renderer.Settings")
def test_integrity_hash_deterministic(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    h1 = _compute_integrity_hash("payload")
    h2 = _compute_integrity_hash("payload")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


@patch("app.services.certificate_renderer.Settings")
def test_integrity_hash_different_payloads(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    h1 = _compute_integrity_hash("payload1")
    h2 = _compute_integrity_hash("payload2")
    assert h1 != h2


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------

@patch("app.services.certificate_renderer.Settings")
def test_render_json_structure(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_json(_sample_scores())
    assert "forge_seal" in result
    assert result["forge_seal"]["version"] == "1.0"
    assert "certificate" in result
    assert "integrity" in result
    assert result["integrity"]["algorithm"] == "HMAC-SHA256"
    assert len(result["integrity"]["hash"]) == 64


@patch("app.services.certificate_renderer.Settings")
def test_render_json_forge_seal_has_integrity_hash(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_json(_sample_scores())
    seal = result["forge_seal"]
    assert "integrity_hash" in seal
    assert len(seal["integrity_hash"]) == 64
    assert seal["integrity_hash"] == result["integrity"]["hash"]


@patch("app.services.certificate_renderer.Settings")
def test_render_json_forge_seal_has_generated_at(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_json(_sample_scores())
    assert result["forge_seal"]["generated_at"] == "2025-01-01T00:00:00+00:00"


@patch("app.services.certificate_renderer.Settings")
def test_render_json_certificate_content(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_json(_sample_scores())
    cert = result["certificate"]
    assert cert["verdict"] == "CERTIFIED"
    assert cert["overall_score"] == 96.3
    assert cert["project"]["name"] == "TestProject"


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------

@patch("app.services.certificate_renderer.Settings")
def test_render_text_contains_verdict(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    text = render_text(_sample_scores())
    assert "CERTIFIED" in text
    assert "FORGE SEAL" in text
    assert "TestProject" in text


@patch("app.services.certificate_renderer.Settings")
def test_render_text_contains_dimensions(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    text = render_text(_sample_scores())
    assert "Build Integrity" in text
    assert "Test Coverage" in text
    assert "Integrity:" in text


@patch("app.services.certificate_renderer.Settings")
def test_render_text_contains_build_summary(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    text = render_text(_sample_scores())
    assert "completed" in text
    assert "$1.50" in text


@patch("app.services.certificate_renderer.Settings")
def test_render_text_no_build_summary(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    scores = _sample_scores()
    scores["build_summary"] = None
    text = render_text(scores)
    assert "FORGE SEAL" in text
    # Should not crash


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

@patch("app.services.certificate_renderer.Settings")
def test_render_html_is_html(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    html = render_html(_sample_scores())
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html


@patch("app.services.certificate_renderer.Settings")
def test_render_html_contains_verdict(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    html = render_html(_sample_scores())
    assert "CERTIFIED" in html
    assert "TestProject" in html


@patch("app.services.certificate_renderer.Settings")
def test_render_html_contains_dimensions(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    html = render_html(_sample_scores())
    assert "Build Integrity" in html
    assert "Test Coverage" in html
    assert "HMAC-SHA256" in html


@patch("app.services.certificate_renderer.Settings")
def test_render_html_escapes_xss(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    scores = _sample_scores()
    scores["project"]["name"] = '<script>alert("xss")</script>'
    html = render_html(scores)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# render_certificate dispatcher
# ---------------------------------------------------------------------------

@patch("app.services.certificate_renderer.Settings")
def test_render_certificate_json(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_certificate(_sample_scores(), "json")
    assert isinstance(result, dict)
    assert "forge_seal" in result


@patch("app.services.certificate_renderer.Settings")
def test_render_certificate_html(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_certificate(_sample_scores(), "html")
    assert isinstance(result, str)
    assert "<!DOCTYPE html>" in result


@patch("app.services.certificate_renderer.Settings")
def test_render_certificate_text(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_certificate(_sample_scores(), "text")
    assert isinstance(result, str)
    assert "FORGE SEAL" in result


@patch("app.services.certificate_renderer.Settings")
def test_render_certificate_default_json(mock_settings):
    mock_settings.JWT_SECRET = "test-secret"
    result = render_certificate(_sample_scores())
    assert isinstance(result, dict)
