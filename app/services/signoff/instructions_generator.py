"""USER_INSTRUCTIONS.md generator — per builder_contract §9.7.

Hybrid approach: template structure (8 mandatory sections) with
LLM filling project-specific details from actual stack info.
"""

from __future__ import annotations

import json
import logging

from app.clients.llm_client import chat

from .stack_resolver import StackInfo

logger = logging.getLogger(__name__)


_INSTRUCTIONS_SYSTEM = """You are a technical writer generating a USER_INSTRUCTIONS.md file.
You will receive project details including the technology stack, environment variables,
install commands, run commands, and file structure.

Generate instructions for a NON-DEVELOPER end user. No jargon without explanation.

The document MUST have these 8 sections in this exact order:

1. **Prerequisites** — What software must be installed before starting. Include minimum
   versions and download links.

2. **Install** — Step-by-step dependency installation commands. If there are multiple
   services (backend/frontend), show commands for each clearly.

3. **Credential / API Setup** — If the project uses API keys, tokens, or external
   services, explain where to get each one. If none needed, say "No external
   credentials required."

4. **Configure .env** — List every environment variable, what it does, whether it's
   required or optional, and what a sensible default looks like. Include the command
   to create .env from the example file.

5. **Run** — Exact commands to start the application. Include the boot script command
   for the user's platform. Show both dev and production modes if applicable.

6. **Stop** — How to shut the application down gracefully (Ctrl+C, etc.).

7. **Key Settings Explained** — What tunable parameters do in plain language
   (e.g., PORT, NODE_ENV, DB_PATH).

8. **Troubleshooting** — A markdown table of 4-6 common errors and their fixes.

Output ONLY the Markdown content. No code fences wrapping the entire output.
Start with `# User Instructions` as the H1."""


async def generate_user_instructions(
    project_name: str,
    stack: StackInfo,
    file_structure: list[str] | None,
    boot_script_name: str,
    api_key: str,
) -> str | None:
    """Generate USER_INSTRUCTIONS.md content using LLM.

    Returns Markdown string or None if LLM unavailable.
    """
    from app.config import get_model_for_role

    if not api_key:
        logger.info("No API key — skipping USER_INSTRUCTIONS generation")
        return None

    # Build context from StackInfo
    env_table = ""
    if stack.env_vars:
        env_table = "Environment Variables:\n"
        for v in stack.env_vars:
            req = "required" if v.get("required") else "optional"
            default = v.get("default") or "(none)"
            env_table += f"  - {v['name']}: {req}, default={default}, dir={v.get('dir', 'root')}\n"
    else:
        env_table = "Environment Variables: None detected\n"

    install_text = ""
    if stack.install_commands:
        install_text = "Install Commands:\n"
        for cmd_info in stack.install_commands:
            install_text += f"  - In {cmd_info['dir']}/: {cmd_info['cmd']}\n"

    run_text = ""
    if stack.run_commands:
        run_text = "Run Commands:\n"
        for svc, cmd in stack.run_commands.items():
            run_text += f"  - {svc}: {cmd}\n"

    tree_text = ""
    if file_structure:
        trimmed = sorted(file_structure)[:40]
        tree_text = "File Structure:\n" + "\n".join(f"  {f}" for f in trimmed)
        if len(file_structure) > 40:
            tree_text += f"\n  ... and {len(file_structure) - 40} more files"

    user_msg = (
        f"Project: {project_name}\n"
        f"Description: {stack.project_description or 'Not specified'}\n\n"
        f"Stack: {stack.primary_language} / {stack.backend_framework or 'N/A'}"
        f" + {stack.frontend_framework or 'no frontend'}\n"
        f"Database: {stack.database or 'none'}\n"
        f"Has Docker: {stack.has_docker}\n\n"
        f"{install_text}\n"
        f"{run_text}\n"
        f"Test Command: {stack.test_command or 'none'}\n\n"
        f"{env_table}\n"
        f"Boot Script: {boot_script_name}\n"
        f"User Platform: {stack.dev_platform}\n\n"
        f"{tree_text}"
    )

    try:
        model = get_model_for_role("planner")
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_INSTRUCTIONS_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=3072,
            provider="anthropic",
        )
        text = result["text"] if isinstance(result, dict) else result
        return text.strip()
    except Exception:
        logger.exception("Failed to generate USER_INSTRUCTIONS for %s", project_name)
        return None
