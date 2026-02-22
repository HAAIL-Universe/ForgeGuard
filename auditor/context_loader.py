"""Load audit context and build system prompts."""

from typing import Callable, Optional


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

Your audit mandate:
1. Audit the plan for consistency — do all contracts agree with each other?
   - Does schema match blueprint features?
   - Do phases cover all schema tables?
   - Do physics endpoints match schema?
2. Audit for completeness — is nothing missing?
   - All MVP features scheduled?
   - All endpoints defined?
   - All migrations planned?
3. Audit for feasibility — is this actually buildable?
   - Tech stack is consistent?
   - Dependencies are realistic?
   - Timeline is achievable?

Do NOT build. Do NOT suggest features. Only verify the plan is sound."""

    elif mode == "phase":
        mandate = """\
You are the BUILDER AUDITOR — an independent agent that audits phase outputs
from the Builder after each phase completes.

Your audit mandate:
1. Check boundary violations — do code layers respect separation rules?
   - Routers not importing repos directly?
   - Services not importing routers?
   - No circular imports?
2. Check test coverage — does it meet threshold?
   - Coverage percentage acceptable?
3. Check code quality — does it follow standards?
   - Naming conventions followed?
   - Docstrings present?
   - Following architectural patterns?
4. Check against builder contract — does it match expectations?

Do NOT build. Do NOT fix code. Only verify it is sound."""

    else:
        raise ValueError(f"Unknown audit mode: {mode}")

    blocks.append({"type": "text", "text": mandate})

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
