"""Sign-off orchestrator — generates all post-build deliverables.

Runs after all builder phases complete but before the forge seal.
Non-fatal: if any deliverable fails, the others still proceed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine
from uuid import UUID

from app.clients import git_client
from app.config import settings
from app.services.build.context import inject_forge_gitignore

from .instructions_generator import generate_user_instructions
from .stack_resolver import StackInfo, resolve_stack
from .startup_generator import generate_boot_script

logger = logging.getLogger(__name__)


@dataclass
class SignoffResult:
    """Result of the sign-off phase."""

    readme_generated: bool = False
    instructions_generated: bool = False
    boot_script_generated: bool = False
    boot_script_name: str = ""
    files_written: list[str] = field(default_factory=list)
    commit_sha: str | None = None
    push_ok: bool = False
    errors: list[str] = field(default_factory=list)


async def run_signoff(
    build_id: UUID,
    project_id: UUID,
    user_id: UUID,
    contracts: list[dict],
    working_dir: str,
    access_token: str = "",
    branch: str = "main",
    target_type: str | None = None,
    api_key: str = "",
    dev_platform: str = "unix",
    broadcast_fn: Callable[..., Coroutine] | None = None,
    log_fn: Callable[..., Coroutine] | None = None,
) -> SignoffResult:
    """Run the full sign-off phase: generate deliverables, commit, push.

    This is a SERVICE function (no HTTP, no raw SQL). It:
    1. Resolves the project stack from contracts + filesystem
    2. Generates README.md (v3) via LLM
    3. Generates USER_INSTRUCTIONS.md via LLM (hybrid template)
    4. Generates ONE platform-specific boot script via template
    5. Writes all files to disk
    6. Git add + commit + push

    All steps are non-fatal: if any single deliverable fails, the others
    still proceed. Errors are collected in SignoffResult.errors.
    """
    result = SignoffResult()

    if broadcast_fn is None:
        broadcast_fn = _noop
    if log_fn is None:
        log_fn = _noop

    await log_fn(build_id, "Starting build sign-off...", "system", "info")
    await broadcast_fn(user_id, build_id, "signoff_start", {})

    # 1. Resolve stack
    stack = resolve_stack(contracts, working_dir, dev_platform=dev_platform)
    logger.info(
        "Sign-off stack resolved: %s/%s, frontend=%s, platform=%s",
        stack.primary_language, stack.backend_framework,
        stack.frontend_framework, stack.dev_platform,
    )

    # 2. Get file structure for README/instructions context
    try:
        file_structure = await git_client.get_file_list(working_dir)
    except Exception:
        file_structure = []

    # Filesystem fallback when git ls-files returns empty (e.g. .git/ missing)
    if not file_structure:
        ws = Path(working_dir)
        file_structure = sorted(
            str(p.relative_to(ws)).replace("\\", "/")
            for p in ws.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".forge" not in p.parts
        )[:50]

    # 3. Generate README.md (v3)
    try:
        from app.services.readme_generator import generate_project_readme

        readme_content = await generate_project_readme(
            project_name=stack.project_name,
            project_description=stack.project_description,
            stack_profile=_stack_to_profile(stack),
            architecture=None,
            file_structure=file_structure,
            build_summary=None,
        )
        if readme_content:
            _write_file(working_dir, "README.md", readme_content)
            result.readme_generated = True
            result.files_written.append("README.md")
            await log_fn(build_id, "Sign-off: README.md generated", "system", "info")
            # Update project description from README's first paragraph
            desc = _extract_readme_description(readme_content)
            if desc:
                try:
                    from app.repos import project_repo
                    await project_repo.update_project(project_id, description=desc)
                    logger.info("Sign-off: updated project description from README")
                except Exception as exc:
                    logger.warning("Sign-off: failed to update project description: %s", exc)
    except Exception as exc:
        logger.warning("Sign-off README generation failed: %s", exc)
        result.errors.append(f"README: {exc}")

    # 4. Generate boot script (template-driven, no LLM)
    boot_name = ""
    try:
        boot_name, boot_content = generate_boot_script(stack.project_name, stack)
        _write_file(working_dir, boot_name, boot_content)
        result.boot_script_generated = True
        result.boot_script_name = boot_name
        result.files_written.append(boot_name)
        await log_fn(build_id, f"Sign-off: {boot_name} generated", "system", "info")
    except Exception as exc:
        logger.warning("Sign-off boot script generation failed: %s", exc)
        result.errors.append(f"boot script: {exc}")

    # 5. Generate USER_INSTRUCTIONS.md (LLM hybrid)
    try:
        instructions = await generate_user_instructions(
            project_name=stack.project_name,
            stack=stack,
            file_structure=file_structure,
            boot_script_name=boot_name or "boot.sh",
            api_key=api_key,
        )
        if instructions:
            _write_file(working_dir, "USER_INSTRUCTIONS.md", instructions)
            result.instructions_generated = True
            result.files_written.append("USER_INSTRUCTIONS.md")
            await log_fn(
                build_id, "Sign-off: USER_INSTRUCTIONS.md generated",
                "system", "info",
            )
    except Exception as exc:
        logger.warning("Sign-off USER_INSTRUCTIONS generation failed: %s", exc)
        result.errors.append(f"USER_INSTRUCTIONS: {exc}")

    # 6. Ensure .gitignore excludes Forge artifacts before commit
    try:
        if inject_forge_gitignore(working_dir):
            result.files_written.append(".gitignore")
    except Exception as exc:
        logger.warning("Sign-off .gitignore injection failed: %s", exc)

    # 7. Git commit + push
    if result.files_written:
        try:
            sha = await git_client.commit(
                working_dir,
                f"forge: build sign-off — {', '.join(result.files_written)}",
            )
            if sha:
                result.commit_sha = sha.strip()
                await log_fn(
                    build_id,
                    f"Sign-off committed: {result.commit_sha[:8]} "
                    f"({len(result.files_written)} files)",
                    "system", "info",
                )
        except Exception as exc:
            logger.warning("Sign-off git commit failed: %s", exc)
            result.errors.append(f"git commit: {exc}")

        # Push (with retry, same pattern as phase commits)
        if (
            result.commit_sha
            and target_type in ("github_new", "github_existing")
            and access_token
        ):
            max_retries = getattr(settings, "GIT_PUSH_MAX_RETRIES", 3)
            for attempt in range(1, max_retries + 1):
                try:
                    await git_client.push(
                        working_dir, branch=branch, access_token=access_token,
                    )
                    result.push_ok = True
                    await log_fn(
                        build_id, "Sign-off pushed to remote", "system", "info",
                    )
                    break
                except Exception as exc:
                    logger.warning(
                        "Sign-off push attempt %d/%d failed: %s",
                        attempt, max_retries, exc,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)

    summary_parts = [f"{len(result.files_written)} files generated"]
    if result.errors:
        summary_parts.append(f"{len(result.errors)} errors")
    await log_fn(
        build_id, f"Sign-off complete: {', '.join(summary_parts)}",
        "system", "info",
    )
    await broadcast_fn(user_id, build_id, "signoff_complete", {
        "files": result.files_written,
        "errors": result.errors,
    })

    return result


def _write_file(working_dir: str, filename: str, content: str) -> None:
    """Write a file to the project root."""
    path = Path(working_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _stack_to_profile(stack: StackInfo) -> dict:
    """Convert StackInfo to the dict format expected by generate_project_readme."""
    return {
        "primary_language": stack.primary_language,
        "backend": {
            "framework": stack.backend_framework,
        } if stack.has_backend else None,
        "frontend": {
            "framework": stack.frontend_framework,
        } if stack.has_frontend else None,
        "database": stack.database,
        "containerized": stack.has_docker,
    }


def _extract_readme_description(readme: str) -> str | None:
    """Extract the first non-heading paragraph after the H1 title.

    Returns the paragraph text (stripped, max 2000 chars) or None.
    """
    lines = readme.split("\n")
    past_h1 = False
    para_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not past_h1:
            if stripped.startswith("# "):
                past_h1 = True
            continue
        # Skip blank lines before the first paragraph
        if not para_lines and not stripped:
            continue
        # Skip any sub-headings, badges, or image lines
        if stripped.startswith("#") or stripped.startswith("![") or stripped.startswith("[!["):
            if para_lines:
                break
            continue
        # End of paragraph on blank line
        if not stripped and para_lines:
            break
        para_lines.append(stripped)
    if not para_lines:
        return None
    desc = " ".join(para_lines).strip()
    return desc[:2000] if desc else None


async def _noop(*args: Any, **kwargs: Any) -> None:
    pass
