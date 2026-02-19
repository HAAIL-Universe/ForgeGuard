"""MCP session context — scopes tool calls to a project/build/user.

The build orchestrator calls ``set_session()`` once before spawning
sub-agents.  Project-scoped MCP tools then resolve ``project_id`` and
``build_id`` from the session when the caller omits them, avoiding
repetition on every tool call.

Usage::

    from forge_ide.mcp.session import set_session, get_session

    # Orchestrator sets context at build start
    set_session(project_id="abc-123", build_id="build-7", user_id="u-1")

    # Tools resolve missing params from session
    session = get_session()
    pid = arguments.get("project_id") or session.project_id
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MCPSession:
    """Holds session-level defaults for the running MCP server instance."""

    project_id: str | None = None
    build_id: str | None = None
    user_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "build_id": self.build_id,
            "user_id": self.user_id,
        }

    def clear(self) -> None:
        self.project_id = None
        self.build_id = None
        self.user_id = None


# Module-level singleton
_session = MCPSession()


def set_session(
    project_id: str,
    build_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Set session-level defaults.  Returns confirmation dict."""
    _session.project_id = project_id
    _session.build_id = build_id
    _session.user_id = user_id
    logger.info(
        "[mcp:session] SET  project=%s  build=%s  user=%s",
        project_id, build_id, user_id,
    )
    return {
        "ok": True,
        **_session.as_dict(),
    }


def get_session() -> MCPSession:
    """Return the current session singleton (read-only in practice)."""
    return _session


def clear_session() -> dict[str, Any]:
    """Reset session to blank.  Returns confirmation."""
    logger.info("[mcp:session] CLEAR")
    _session.clear()
    return {"ok": True, "cleared": True}


def resolve_project_id(arguments: dict[str, Any]) -> str | None:
    """Return project_id from arguments, falling back to session default."""
    return arguments.get("project_id") or _session.project_id


def resolve_build_id(arguments: dict[str, Any]) -> str | None:
    """Return build_id from arguments, falling back to session default."""
    return arguments.get("build_id") or _session.build_id
