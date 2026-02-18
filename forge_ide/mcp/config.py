"""MCP server configuration and constants."""

from __future__ import annotations

import os
from pathlib import Path

# ── Mode & paths ──────────────────────────────────────────────────────────

LOCAL_MODE: bool = os.getenv("FORGEGUARD_LOCAL", "").strip() in ("1", "true", "yes")

FORGEGUARD_ROOT: Path = Path(
    os.getenv("FORGEGUARD_ROOT", Path(__file__).resolve().parent.parent.parent)
)

FORGEGUARD_URL: str = os.getenv("FORGEGUARD_URL", "http://localhost:8000").rstrip("/")
FORGEGUARD_API_KEY: str = os.getenv("FORGEGUARD_API_KEY", "")

# ── Contract registry ────────────────────────────────────────────────────

CONTRACTS_DIR: Path = FORGEGUARD_ROOT / "Forge" / "Contracts"

CONTRACT_MAP: dict[str, tuple[str, str]] = {
    "boundaries": ("boundaries.json", "json"),
    "physics": ("physics.yaml", "yaml"),
    "blueprint": ("blueprint.md", "markdown"),
    "builder_contract": ("builder_contract.md", "markdown"),
    "builder_directive": ("builder_directive.md", "markdown"),
    "manifesto": ("manifesto.md", "markdown"),
    "phases": ("phases.md", "markdown"),
    "schema": ("schema.md", "markdown"),
    "stack": ("stack.md", "markdown"),
    "system_prompt": ("system_prompt.md", "markdown"),
    "ui": ("ui.md", "markdown"),
    "auditor_prompt": ("auditor_prompt.md", "markdown"),
    "recovery_planner_prompt": ("recovery_planner_prompt.md", "markdown"),
    "remediation": ("Remediation.md", "markdown"),
    "desktop_distribution_plan": ("Desktop_Distribution_Plan.md", "markdown"),
}

INVARIANT_DESCRIPTIONS: dict[str, str] = {
    "backend_test_count": "Backend test count must never decrease between phases",
    "frontend_test_count": "Frontend test count must never decrease between phases",
    "backend_test_failures": "Backend test failures must remain at zero",
    "frontend_test_failures": "Frontend test failures must remain at zero",
    "total_files": "Total tracked files must never decrease (no accidental deletions)",
    "migration_count": "Database migration count must never decrease",
    "syntax_errors": "Syntax errors must remain at zero",
}
