"""Certificate renderer — produce JSON, HTML, and plain text certificates.

Takes CertificateScores and renders into different output formats.
Includes HMAC-SHA256 integrity hash for verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.config import Settings

# ---------------------------------------------------------------------------
# Integrity hash
# ---------------------------------------------------------------------------


def _compute_integrity_hash(payload: str) -> str:
    """HMAC-SHA256 of the JSON payload using the server JWT secret."""
    secret = Settings.JWT_SECRET.encode("utf-8")
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# JSON renderer
# ---------------------------------------------------------------------------


def render_json(scores: dict) -> dict:
    """Render a machine-readable JSON certificate with integrity hash.

    Returns a dict that can be serialised directly to JSON.
    """
    payload_for_hash = json.dumps(scores, sort_keys=True, default=str)
    integrity_hash = _compute_integrity_hash(payload_for_hash)

    return {
        "forge_seal": {
            "version": "1.0",
            "type": "forge-build-certificate",
        },
        "certificate": scores,
        "integrity": {
            "algorithm": "HMAC-SHA256",
            "hash": integrity_hash,
        },
    }


# ---------------------------------------------------------------------------
# Plain-text renderer
# ---------------------------------------------------------------------------


def render_text(scores: dict) -> str:
    """Render a compact plain-text certificate summary."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  FORGE SEAL — BUILD CERTIFICATE")
    lines.append("=" * 60)

    project = scores.get("project") or {}
    lines.append(f"  Project: {project.get('name', 'Unknown')}")
    lines.append(f"  Generated: {scores.get('generated_at', 'N/A')}")
    lines.append("")

    verdict = scores.get("verdict", "UNKNOWN")
    overall = scores.get("overall_score", 0)
    lines.append(f"  VERDICT: {verdict}")
    lines.append(f"  OVERALL SCORE: {overall}/100")
    lines.append("-" * 60)

    dims = scores.get("dimensions", {})
    for dim_key, dim_data in dims.items():
        label = dim_key.replace("_", " ").title()
        s = dim_data.get("score", 0)
        w = dim_data.get("weight", 0)
        lines.append(f"  {label:<25} {s:>3}/100  (weight: {w:.0%})")
        for detail in dim_data.get("details", []):
            lines.append(f"    • {detail}")

    lines.append("-" * 60)

    build_summary = scores.get("build_summary")
    if build_summary:
        lines.append(f"  Build: {build_summary.get('status', 'N/A')} | "
                      f"Phase: {build_summary.get('phase', 'N/A')} | "
                      f"Cost: ${build_summary.get('cost_usd', 0):.2f}")
        lines.append(f"  Files: {build_summary.get('files_written', 0)} | "
                      f"Commits: {build_summary.get('git_commits', 0)} | "
                      f"Loops: {build_summary.get('loop_count', 0)}")

    lines.append("")

    payload_for_hash = json.dumps(scores, sort_keys=True, default=str)
    integrity_hash = _compute_integrity_hash(payload_for_hash)
    lines.append(f"  Integrity: {integrity_hash[:16]}...")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------


def render_html(scores: dict) -> str:
    """Render a styled HTML certificate page."""
    project = scores.get("project") or {}
    verdict = scores.get("verdict", "UNKNOWN")
    overall = scores.get("overall_score", 0)
    dims = scores.get("dimensions", {})
    build_summary = scores.get("build_summary")
    generated_at = scores.get("generated_at", "N/A")

    # Verdict colours
    verdict_colours = {
        "CERTIFIED": ("#14532D", "#22C55E"),
        "CONDITIONAL": ("#78350F", "#F59E0B"),
        "FLAGGED": ("#7F1D1D", "#EF4444"),
    }
    bg, fg = verdict_colours.get(verdict, ("#1E293B", "#94A3B8"))

    # Dimension cards
    dim_cards = ""
    for dim_key, dim_data in dims.items():
        label = dim_key.replace("_", " ").title()
        s = dim_data.get("score", 0)
        w = dim_data.get("weight", 0)
        bar_colour = "#22C55E" if s >= 80 else "#F59E0B" if s >= 60 else "#EF4444"
        details_html = "".join(
            f'<div style="font-size:0.75rem;color:#94A3B8;padding:1px 0">• {_esc(d)}</div>'
            for d in dim_data.get("details", [])
        )
        dim_cards += f"""
        <div style="background:#1E293B;border-radius:8px;padding:14px;
                     border:1px solid #334155;margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-weight:600;font-size:0.85rem">{_esc(label)}</span>
            <span style="font-size:0.8rem;font-weight:700;color:{bar_colour}">{s}/100</span>
          </div>
          <div style="background:#0F172A;border-radius:4px;height:8px;margin-bottom:6px;overflow:hidden">
            <div style="width:{s}%;height:100%;background:{bar_colour};border-radius:4px"></div>
          </div>
          <div style="font-size:0.65rem;color:#64748B;margin-bottom:4px">Weight: {w:.0%}</div>
          {details_html}
        </div>"""

    # Build summary section
    build_html = ""
    if build_summary:
        build_html = f"""
        <div style="background:#1E293B;border-radius:8px;padding:14px;
                     border:1px solid #334155;margin-bottom:16px">
          <div style="font-size:0.7rem;color:#64748B;text-transform:uppercase;margin-bottom:8px">
            Build Summary
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;font-size:0.8rem">
            <div>Status: <strong>{_esc(str(build_summary.get('status','N/A')))}</strong></div>
            <div>Phase: <strong>{_esc(str(build_summary.get('phase','N/A')))}</strong></div>
            <div>Cost: <strong>${build_summary.get('cost_usd',0):.2f}</strong></div>
            <div>Files: <strong>{build_summary.get('files_written',0)}</strong></div>
            <div>Commits: <strong>{build_summary.get('git_commits',0)}</strong></div>
            <div>Loops: <strong>{build_summary.get('loop_count',0)}</strong></div>
          </div>
        </div>"""

    # Integrity hash
    payload_for_hash = json.dumps(scores, sort_keys=True, default=str)
    integrity_hash = _compute_integrity_hash(payload_for_hash)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forge Seal — {_esc(project.get('name','Certificate'))}</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0F172A;color:#F8FAFC;font-family:system-ui,-apple-system,sans-serif;padding:24px}}
  .container{{max-width:720px;margin:0 auto}}
  a{{color:#3B82F6}}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div style="text-align:center;margin-bottom:24px">
    <div style="font-size:0.7rem;color:#64748B;text-transform:uppercase;letter-spacing:2px;margin-bottom:4px">
      Forge Seal — Build Certificate
    </div>
    <div style="font-size:1.5rem;font-weight:700;margin-bottom:4px">
      {_esc(project.get('name','Unknown Project'))}
    </div>
    <div style="font-size:0.8rem;color:#94A3B8">
      {_esc(project.get('repo_full_name',''))} • {_esc(generated_at)}
    </div>
  </div>

  <!-- Verdict -->
  <div style="text-align:center;margin-bottom:24px">
    <div style="display:inline-flex;align-items:center;gap:16px;
                background:{bg};border-radius:12px;padding:16px 32px">
      <div style="font-size:2.5rem;font-weight:800;color:{fg}">{overall}</div>
      <div>
        <div style="font-size:1.2rem;font-weight:700;color:{fg}">{verdict}</div>
        <div style="font-size:0.7rem;color:#94A3B8">/100 overall quality score</div>
      </div>
    </div>
  </div>

  {build_html}

  <!-- Dimensions -->
  <div style="margin-bottom:16px">
    <div style="font-size:0.7rem;color:#64748B;text-transform:uppercase;margin-bottom:8px;letter-spacing:0.5px">
      Quality Dimensions
    </div>
    {dim_cards}
  </div>

  <!-- Integrity -->
  <div style="text-align:center;font-size:0.65rem;color:#475569;margin-top:24px">
    <div>Builds total: {scores.get('builds_total',0)} • Contracts: {scores.get('contracts_count',0)}</div>
    <div style="margin-top:4px">
      Integrity: <code style="color:#64748B">{integrity_hash}</code>
    </div>
    <div style="margin-top:2px">Algorithm: HMAC-SHA256</div>
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def render_certificate(scores: dict, fmt: str = "json") -> str | dict:
    """Render a certificate in the requested format.

    Parameters
    ----------
    scores : CertificateScores from compute_certificate_scores.
    fmt : "json" | "html" | "text"

    Returns
    -------
    dict (for JSON) or str (for HTML/text).
    """
    if fmt == "html":
        return render_html(scores)
    if fmt == "text":
        return render_text(scores)
    return render_json(scores)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
