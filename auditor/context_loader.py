"""Load audit context and build system prompts."""

import sys
from pathlib import Path
from typing import Callable, Optional

# Ensure ForgeGuard root is importable so forge_constitution can be found
_forgeguard_root = str(Path(__file__).resolve().parent.parent)
if _forgeguard_root not in sys.path:
    sys.path.insert(0, _forgeguard_root)

from forge_constitution import CONSTITUTION


def build_audit_system_prompt(
    mode: str,
    contract_fetcher: Optional[Callable] = None,
) -> list[dict]:
    """
    Build the system prompt for auditor in specified mode.

    Args:
        mode: "plan" for planner audits, "phase" for builder audits
        contract_fetcher: optional function to fetch contracts by name

    Returns:
        List of content blocks for prompt (for prompt caching support)
    """
    blocks = []

    # Mode-specific mandate
    if mode == "plan":
        mandate = """\
You are the PLANNER AUDITOR — an independent agent that audits plan outputs
from the Planner before the plan goes to the Builder.

# AUDIT MANDATE
1. CONSISTENCY — do all contracts agree with each other?
   - Does schema match blueprint features?
   - Do phases cover ALL schema tables (each claimed in exactly one phase)?
   - Do physics endpoints match schema entities?
   - Do file layers match boundaries contract?
2. COMPLETENESS — is anything missing?
   - All MVP features from blueprint scheduled in at least one phase?
   - All endpoints from physics assigned to a file?
   - All database tables from schema assigned to a migration?
   - wires_from_phase set for any phase importing from a prior phase?
3. FEASIBILITY — is this actually buildable?
   - Tech stack choices are compatible (e.g. not mixing SQLAlchemy and Prisma)?
   - Dependencies exist and are maintained?
   - Phase ordering respects dependency graph (no forward references)?

# OUTPUT FORMAT
{
  "verdict": "PASS|FAIL",
  "issues": [
    {"category": "consistency|completeness|feasibility", "severity": "error|warn", "message": "..."}
  ]
}

# CONSTRAINTS
- Do NOT build. Do NOT suggest features. Only verify the plan is sound.
- PASS means the plan can go to the Builder as-is.
- FAIL means specific issues must be fixed before building.
- Only severity "error" issues cause FAIL. Warnings are informational."""

    elif mode == "phase":
        mandate = """\
You are the BUILDER AUDITOR — an independent agent that audits phase outputs
from the Builder after each phase completes.

# AUDIT MANDATE
1. BOUNDARY VIOLATIONS — do code layers respect separation rules?
   - Routers must NOT import repos or DB sessions directly
   - Services must NOT import routers
   - No circular imports between modules
2. CONTRACT COMPLIANCE — does output match what was planned?
   - All files in the phase manifest actually created?
   - API endpoints match physics contract shapes?
   - Database models match schema contract?
3. STRUCTURAL INTEGRITY — is the code sound?
   - All imports resolve to real modules (workspace or stack)?
   - No undefined references (functions/classes used but not imported)?
   - No syntax errors?

# DO NOT CHECK (these are not your concern):
- Naming conventions or style preferences
- Missing docstrings or comments
- Code elegance or alternative implementations
- Test coverage percentage (separate step handles this)

# OUTPUT FORMAT
{
  "verdict": "PASS|FAIL",
  "files_reviewed": 5,
  "issues": [
    {"file": "path.py", "severity": "error|warn", "message": "..."}
  ]
}

# CONSTRAINTS
- Do NOT build. Do NOT fix code. Only verify it is sound.
- Only severity "error" issues cause FAIL verdict.
- If the phase is partially complete (some files missing): FAIL with clear
  list of which manifest files are missing."""

    else:
        raise ValueError(f"Unknown audit mode: {mode}")

    blocks.append({"type": "text", "text": f"{CONSTITUTION}\n\n{mandate}"})

    # Load governance contracts (if fetcher provided)
    if contract_fetcher:
        if mode == "plan":
            try:
                auditor_prompt = contract_fetcher("auditor_prompt")
                blocks.append(
                    {
                        "type": "text",
                        "text": f"## AUDITOR PROMPT\n\n{auditor_prompt}",
                    }
                )
            except Exception:
                pass

            try:
                audit_reference = contract_fetcher("audit_reference")
                blocks.append(
                    {
                        "type": "text",
                        "text": f"## AUDIT REFERENCE STANDARDS\n\n{audit_reference}",
                    }
                )
            except Exception:
                pass

        elif mode == "phase":
            try:
                builder_contract = contract_fetcher("builder_contract")
                blocks.append(
                    {
                        "type": "text",
                        "text": f"## BUILDER CONTRACT\n\n{builder_contract}",
                    }
                )
            except Exception:
                pass

            try:
                boundaries = contract_fetcher("boundaries")
                blocks.append(
                    {
                        "type": "text",
                        "text": f"## ARCHITECTURAL BOUNDARIES\n\n{boundaries}",
                    }
                )
            except Exception:
                pass

    return blocks
