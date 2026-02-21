"""README lifecycle generator — greenfield README creation at key milestones.

Phase 58.6 — Three README stages:
  v1: Stub at project creation (handled elsewhere)
  v2: Contract-aware README after contracts are finalised
  v3: Full project README after build completes (before Seal)

Only applies to greenfield projects (dossier baseline has 0 source files
or only boilerplate).  Non-greenfield repos keep their existing README.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.llm_client import chat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Greenfield detection
# ---------------------------------------------------------------------------

def is_greenfield(dossier_results: dict | None) -> bool:
    """Determine if a project is greenfield based on its dossier data.

    A project is greenfield if:
    - No dossier exists, or
    - The tree has 0 source files, or
    - The tree has ≤5 total files (likely just boilerplate)
    """
    if dossier_results is None:
        return True
    metrics = dossier_results.get("metrics", {})
    file_stats = metrics.get("file_stats", {})
    source_files = file_stats.get("source_files", 0)
    total_files = file_stats.get("total_files", 0)
    if source_files == 0:
        return True
    if total_files <= 5:
        return True
    return False


# ---------------------------------------------------------------------------
# README v2 — Contract-aware
# ---------------------------------------------------------------------------

_CONTRACT_README_SYSTEM = """You are a technical writer generating a project README.md.
You will receive the project name, description, technology stack, architecture overview,
and a list of contracts (phases/tasks). Generate a clear, professional README that:

1. Starts with the project name as an H1
2. Includes a brief description (2-3 sentences)
3. Lists the technology stack
4. Describes the architecture at a high level
5. Includes a "Roadmap" or "Phases" section listing the contracts
6. Adds placeholder sections for Setup, Usage, and Contributing

Output ONLY the Markdown content. No code fences wrapping the entire output."""


async def generate_contract_readme(
    project_name: str,
    project_description: str | None,
    stack_profile: dict | None,
    architecture: dict | None,
    contracts: list[dict],
) -> str | None:
    """Generate README v2 from contract knowledge.

    Called after contracts are finalised. Returns Markdown string or None
    if LLM is unavailable.
    """
    from app.config import settings, get_model_for_role

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.info("No API key configured — skipping README generation")
        return None

    stack_text = json.dumps(stack_profile, indent=2, default=str) if stack_profile else "Not yet determined"
    arch_text = json.dumps(architecture, indent=2, default=str) if architecture else "Not yet determined"

    contract_lines = []
    for i, c in enumerate(contracts, 1):
        name = c.get("name", c.get("contract_type", f"Phase {i}"))
        desc = c.get("description", "")
        contract_lines.append(f"{i}. **{name}** — {desc}")

    user_msg = (
        f"Project: {project_name}\n"
        f"Description: {project_description or 'No description provided'}\n\n"
        f"Technology Stack:\n{stack_text}\n\n"
        f"Architecture:\n{arch_text}\n\n"
        f"Contracts / Phases:\n" + "\n".join(contract_lines)
    )

    try:
        model = get_model_for_role("planner")
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_CONTRACT_README_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=2048,
            provider="anthropic",
        )
        text = result["text"] if isinstance(result, dict) else result
        return text.strip()
    except Exception:
        logger.exception("Failed to generate contract README for %s", project_name)
        return None


# ---------------------------------------------------------------------------
# README v3 — Full project README after build
# ---------------------------------------------------------------------------

_PROJECT_README_SYSTEM = """You are a technical writer generating a comprehensive project README.md.
You will receive the project name, description, real technology stack, architecture map,
file structure, and build output summary. Generate a production-quality README that:

1. Project name as H1 with a brief tagline
2. Overview section (what it does, who it's for)
3. Technology Stack section with specific versions where known
4. Architecture section with component descriptions
5. Project Structure section showing key directories
6. Setup & Installation instructions
7. Usage / Running instructions
8. Environment Variables section (list .env keys without real values)
9. API Routes section (if applicable)
10. Testing section
11. Contributing guidelines placeholder
12. License placeholder

Output ONLY the Markdown content. No code fences wrapping the entire output."""


async def generate_project_readme(
    project_name: str,
    project_description: str | None,
    stack_profile: dict | None,
    architecture: dict | None,
    file_structure: list[str] | None,
    build_summary: dict | None,
) -> str | None:
    """Generate README v3 from actual build output.

    Called after build completes, before Seal. Returns Markdown string or
    None if LLM is unavailable.
    """
    from app.config import settings, get_model_for_role

    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        logger.info("No API key configured — skipping README generation")
        return None

    stack_text = json.dumps(stack_profile, indent=2, default=str) if stack_profile else "Unknown"
    arch_text = json.dumps(architecture, indent=2, default=str) if architecture else "Unknown"

    # Show a trimmed file tree
    tree_text = "Not available"
    if file_structure:
        # Show up to 50 files, sorted, grouped by directory
        trimmed = sorted(file_structure)[:50]
        tree_text = "\n".join(trimmed)
        if len(file_structure) > 50:
            tree_text += f"\n... and {len(file_structure) - 50} more files"

    build_text = "No build data"
    if build_summary:
        build_text = json.dumps(build_summary, indent=2, default=str)

    user_msg = (
        f"Project: {project_name}\n"
        f"Description: {project_description or 'No description provided'}\n\n"
        f"Technology Stack:\n{stack_text}\n\n"
        f"Architecture:\n{arch_text}\n\n"
        f"File Structure:\n{tree_text}\n\n"
        f"Build Summary:\n{build_text}"
    )

    try:
        model = get_model_for_role("planner")
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_PROJECT_README_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=3072,
            provider="anthropic",
        )
        text = result["text"] if isinstance(result, dict) else result
        return text.strip()
    except Exception:
        logger.exception("Failed to generate project README for %s", project_name)
        return None
