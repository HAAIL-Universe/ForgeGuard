"""Project-scoped artifact store — MCP temp pattern.

Sub-agents store generated artifacts (contracts, scout data, directives,
phase outputs, etc.) here and retrieve them on-demand.  Large content stays
out of agent system prompts; agents receive only a project_id and load what
they need via forge_get_artifact.

Key format:  project:{project_id}:{artifact_type}:{key}

Artifact types
--------------
contract    Generated contract files (manifesto, stack, physics, boundaries …)
scout       Scout analysis outputs (dossier, stack_profile, architecture)
renovation  Renovation plan sections (executive_brief, forge_spec, …)
directive   Builder directive generated from scout
phase       Build phase outputs (summaries, diffs, logs)
seal        ForgeSeal / integrity envelopes

Storage
-------
Primary  : in-process dict (survives for the lifetime of the MCP server process).
Secondary: optional disk persistence under .forge_artifacts/ for cross-session
           durability.  Disk writes are best-effort; failures are swallowed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import FORGEGUARD_ROOT

# ── Constants ─────────────────────────────────────────────────────────────

_ARTIFACTS_DIR: Path = FORGEGUARD_ROOT / ".forge_artifacts"
_DEFAULT_TTL_HOURS: float = 24.0

# In-memory store: store_key -> (stored_at_monotonic, ttl_seconds, content)
_store: dict[str, tuple[float, float, Any]] = {}

# Known artifact types (for documentation / validation hints)
ARTIFACT_TYPES = frozenset(
    {"contract", "scout", "renovation", "directive", "phase", "seal", "diff"}
)


# ── Public API ────────────────────────────────────────────────────────────


def store_artifact(
    project_id: str,
    artifact_type: str,
    key: str,
    content: Any,
    ttl_hours: float = _DEFAULT_TTL_HOURS,
    persist: bool = True,
) -> dict:
    """Store an artifact in memory (and optionally on disk).

    Parameters
    ----------
    project_id    : Project identifier.
    artifact_type : Category — contract | scout | renovation | directive | phase | seal | diff.
    key           : Artifact key within type, e.g. 'manifesto', 'dossier'.
    content       : String or JSON-serialisable value.
    ttl_hours     : Memory TTL in hours (default 24).  Disk copies are permanent
                    until explicitly cleared.
    persist       : Also write to .forge_artifacts/ for cross-session durability.
    """
    store_key = _make_key(project_id, artifact_type, key)
    ttl_secs = ttl_hours * 3600
    _store[store_key] = (time.monotonic(), ttl_secs, content)

    size_chars = _measure(content)
    if persist:
        _persist_to_disk(project_id, artifact_type, key, content)

    return {
        "stored": True,
        "store_key": store_key,
        "artifact_type": artifact_type,
        "key": key,
        "ttl_hours": ttl_hours,
        "size_chars": size_chars,
    }


def get_artifact(project_id: str, artifact_type: str, key: str) -> dict:
    """Retrieve a stored artifact.

    Checks memory first; falls back to disk and warms the memory cache.
    """
    store_key = _make_key(project_id, artifact_type, key)
    entry = _store.get(store_key)

    if entry is not None:
        stored_at, ttl_secs, content = entry
        age = time.monotonic() - stored_at
        if age <= ttl_secs:
            return {
                "project_id": project_id,
                "artifact_type": artifact_type,
                "key": key,
                "content": content,
                "source": "memory",
                "age_seconds": round(age),
                "ttl_remaining_seconds": round(ttl_secs - age),
                "size_chars": _measure(content),
            }
        # Expired — evict and try disk
        del _store[store_key]

    # Disk fallback
    disk_content = _load_from_disk(project_id, artifact_type, key)
    if disk_content is not None:
        # Warm back into memory for fast subsequent reads
        _store[store_key] = (
            time.monotonic(),
            _DEFAULT_TTL_HOURS * 3600,
            disk_content,
        )
        return {
            "project_id": project_id,
            "artifact_type": artifact_type,
            "key": key,
            "content": disk_content,
            "source": "disk",
            "age_seconds": None,
            "ttl_remaining_seconds": None,
            "size_chars": _measure(disk_content),
        }

    return {"error": f"Artifact not found: {store_key}"}


def list_artifacts(
    project_id: str,
    artifact_type: str | None = None,
) -> dict:
    """List all live artifacts for a project, optionally filtered by type."""
    now = time.monotonic()
    prefix = f"project:{project_id}:"
    if artifact_type:
        prefix += f"{artifact_type}:"

    expired: list[str] = []
    results: list[dict] = []

    for store_key, (stored_at, ttl_secs, content) in list(_store.items()):
        if not store_key.startswith(prefix):
            continue
        age = now - stored_at
        if age > ttl_secs:
            expired.append(store_key)
            continue
        parts = store_key.split(":", 3)  # ["project", id, type, key]
        results.append(
            {
                "store_key": store_key,
                "artifact_type": parts[2],
                "key": parts[3],
                "source": "memory",
                "age_seconds": round(age),
                "ttl_remaining_seconds": round(ttl_secs - age),
                "size_chars": _measure(content),
            }
        )

    for k in expired:
        del _store[k]

    # Supplement with disk-only entries
    memory_keys = {r["store_key"] for r in results}
    for dk in _list_disk_keys(project_id, artifact_type):
        if dk not in memory_keys:
            parts = dk.split(":", 3)
            results.append(
                {
                    "store_key": dk,
                    "artifact_type": parts[2],
                    "key": parts[3],
                    "source": "disk",
                    "age_seconds": None,
                    "ttl_remaining_seconds": None,
                    "size_chars": None,
                }
            )

    return {
        "project_id": project_id,
        "artifact_type_filter": artifact_type,
        "count": len(results),
        "artifacts": sorted(results, key=lambda r: (r["artifact_type"], r["key"])),
    }


def clear_artifacts(
    project_id: str,
    artifact_type: str | None = None,
) -> dict:
    """Remove all artifacts for a project (or a specific type)."""
    prefix = f"project:{project_id}:"
    if artifact_type:
        prefix += f"{artifact_type}:"

    mem_keys = [k for k in list(_store) if k.startswith(prefix)]
    for k in mem_keys:
        del _store[k]

    disk_cleared = _clear_disk(project_id, artifact_type)

    return {
        "cleared_memory": len(mem_keys),
        "cleared_disk": disk_cleared,
        "project_id": project_id,
        "artifact_type": artifact_type,
    }


# ── Internal helpers ──────────────────────────────────────────────────────


def _make_key(project_id: str, artifact_type: str, key: str) -> str:
    return f"project:{project_id}:{artifact_type}:{key}"


def _measure(content: Any) -> int:
    """Return approximate character size of content."""
    try:
        return len(json.dumps(content, default=str))
    except Exception:
        return len(str(content))


# ── Disk helpers ──────────────────────────────────────────────────────────


def _artifact_path(project_id: str, artifact_type: str, key: str) -> Path:
    return _ARTIFACTS_DIR / project_id / artifact_type / f"{key}.json"


def _persist_to_disk(
    project_id: str, artifact_type: str, key: str, content: Any
) -> None:
    try:
        path = _artifact_path(project_id, artifact_type, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(content, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass  # Non-fatal


def _load_from_disk(
    project_id: str, artifact_type: str, key: str
) -> Any | None:
    try:
        path = _artifact_path(project_id, artifact_type, key)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _list_disk_keys(
    project_id: str, artifact_type: str | None
) -> list[str]:
    try:
        base = _ARTIFACTS_DIR / project_id
        if artifact_type:
            base = base / artifact_type
        if not base.exists():
            return []
        keys = []
        for f in base.rglob("*.json"):
            rel = f.relative_to(_ARTIFACTS_DIR)
            parts = rel.parts  # (project_id, artifact_type, key.json)
            if len(parts) == 3:
                keys.append(
                    f"project:{parts[0]}:{parts[1]}:{parts[2][:-5]}"
                )
        return keys
    except Exception:
        return []


def _clear_disk(project_id: str, artifact_type: str | None) -> int:
    try:
        base = _ARTIFACTS_DIR / project_id
        if artifact_type:
            base = base / artifact_type
        if not base.exists():
            return 0
        files = list(base.rglob("*.json"))
        for f in files:
            f.unlink(missing_ok=True)
        return len(files)
    except Exception:
        return 0
