"""Project service package — CRUD operations and re-exports."""

import logging
from uuid import UUID

__all__ = [
    # questionnaire
    "QUESTIONNAIRE_SECTIONS",
    "_SYSTEM_PROMPT",
    "_parse_llm_response",
    "_questionnaire_progress",
    "process_questionnaire_message",
    "get_questionnaire_state",
    "reset_questionnaire",
    # contract_generator
    "CONTRACT_TYPES",
    "ContractCancelled",
    "FORGE_CONTRACTS_DIR",
    "TEMPLATES_DIR",
    "_active_generations",
    "_generate_contract_content",
    "_format_answers_for_prompt",
    "_load_forge_example",
    "_CONTRACT_INSTRUCTIONS",
    "generate_contracts",
    "cancel_contract_generation",
    "push_contracts_to_git",
    # CRUD (defined in this file)
    "create_new_project",
    "list_user_projects",
    "get_project_detail",
    "delete_user_project",
    "list_contracts",
    "list_contract_versions",
    "get_contract_version",
    "get_contract",
    "update_contract",
]

from app.repos import build_repo
from app.repos.project_repo import (
    create_project as repo_create_project,
    delete_project as repo_delete_project,
    get_contract_by_type,
    get_contracts_by_project,
    get_project_by_id,
    get_projects_by_user,
    get_snapshot_batches,
    get_snapshot_contracts,
    update_contract_content as repo_update_contract_content,
)

# Re-export sub-module public API so callers can import from the package root
from .contract_generator import (  # noqa: F401
    CONTRACT_TYPES,
    ContractCancelled,
    FORGE_CONTRACTS_DIR,
    TEMPLATES_DIR,
    _CONTRACT_INSTRUCTIONS,
    _active_generations,
    _format_answers_for_prompt,
    _generate_contract_content,
    _load_forge_example,
    cancel_contract_generation,
    generate_contracts,
    push_contracts_to_git,
)
from .questionnaire import (  # noqa: F401
    QUESTIONNAIRE_SECTIONS,
    _SYSTEM_PROMPT,
    _parse_llm_response,
    _questionnaire_progress,
    get_questionnaire_state,
    process_questionnaire_message,
    reset_questionnaire,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


async def create_new_project(
    user_id: UUID,
    name: str,
    description: str | None = None,
    repo_id: UUID | None = None,
    local_path: str | None = None,
) -> dict:
    """Create a new project, optionally linked to a repo or local path."""
    project = await repo_create_project(
        user_id, name, description, repo_id=repo_id, local_path=local_path
    )
    return project


async def list_user_projects(user_id: UUID) -> list[dict]:
    """List all projects for a user."""
    return await get_projects_by_user(user_id)


async def get_project_detail(user_id: UUID, project_id: UUID) -> dict:
    """Get full project detail. Raises ValueError if not found or not owned."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contracts = await get_contracts_by_project(project_id)
    qs = project.get("questionnaire_state", {})

    project["questionnaire_progress"] = _questionnaire_progress(qs)
    project["contracts"] = [
        {
            "id": str(c["id"]),
            "project_id": str(c["project_id"]),
            "contract_type": c["contract_type"],
            "version": c["version"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
        }
        for c in contracts
    ]

    # Attach latest build info
    latest = await build_repo.get_latest_build_for_project(project_id)
    if latest:
        project["latest_build"] = {
            "id": str(latest["id"]),
            "phase": latest["phase"],
            "status": latest["status"],
            "branch": latest.get("branch", "main"),
            "loop_count": latest["loop_count"],
            "started_at": latest["started_at"],
            "completed_at": latest["completed_at"],
        }
    else:
        project["latest_build"] = None

    return project


async def delete_user_project(user_id: UUID, project_id: UUID) -> bool:
    """Delete a project if owned by user.  Returns True if deleted.

    Raises ValueError if the project has active builds (pending/running/paused).
    """
    project = await get_project_by_id(project_id)
    if not project or str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    # Prevent deletion while builds are still active.
    from app.repos.build_repo import has_active_builds
    if await has_active_builds(project_id):
        raise ValueError("Cannot delete project with active builds")

    return await repo_delete_project(project_id)


async def list_contracts(
    user_id: UUID,
    project_id: UUID,
) -> list[dict]:
    """List all contracts for a project."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    return await get_contracts_by_project(project_id)


async def list_contract_versions(
    user_id: UUID,
    project_id: UUID,
) -> list[dict]:
    """List all snapshot batches for a project's contracts."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    batches = await get_snapshot_batches(project_id)
    return [
        {
            "batch": b["batch"],
            "created_at": b["created_at"],
            "count": b["count"],
        }
        for b in batches
    ]


async def get_contract_version(
    user_id: UUID,
    project_id: UUID,
    batch: int,
) -> list[dict]:
    """Get all contracts for a specific snapshot batch."""
    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contracts = await get_snapshot_contracts(project_id, batch)
    if not contracts:
        raise ValueError(f"Snapshot batch {batch} not found")
    return contracts


async def get_contract(
    user_id: UUID,
    project_id: UUID,
    contract_type: str,
) -> dict:
    """Get a single contract. Raises ValueError if not found."""
    if contract_type not in CONTRACT_TYPES:
        raise ValueError(f"Invalid contract type: {contract_type}")

    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    contract = await get_contract_by_type(project_id, contract_type)
    if not contract:
        raise ValueError(f"Contract '{contract_type}' not found")
    return contract


async def update_contract(
    user_id: UUID,
    project_id: UUID,
    contract_type: str,
    content: str,
) -> dict:
    """Update a contract's content. Raises ValueError if not found."""
    if contract_type not in CONTRACT_TYPES:
        raise ValueError(f"Invalid contract type: {contract_type}")

    project = await get_project_by_id(project_id)
    if not project:
        raise ValueError("Project not found")
    if str(project["user_id"]) != str(user_id):
        raise ValueError("Project not found")

    result = await repo_update_contract_content(project_id, contract_type, content)
    if not result:
        raise ValueError(f"Contract '{contract_type}' not found")
    return result
