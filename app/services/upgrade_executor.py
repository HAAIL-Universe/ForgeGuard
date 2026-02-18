"""Upgrade executor — role-based LLM upgrade with live streaming.

Accepts a renovation plan (from upgrade_service) and executes migration
tasks using a role-based architecture:

- **Sonnet (Key 1)** — ``anthropic_api_key`` + ``LLM_PLANNER_MODEL``
  Analyses migration tasks (planning/proposing changes).  Tokens
  tracked under the ``sonnet`` bucket.
- **Opus (Key 1)** — ``LLM_BUILDER_MODEL`` (reserved for ``build_service``)
  Not used directly here; heavy code generation lives in the build flow.
- **Haiku (Key 2)** — ``anthropic_api_key_2`` + ``LLM_NARRATOR_MODEL``
  Non-blocking plain-English narrator (fires after key events).
If only one key is available, narration is disabled but execution works.

WS event types emitted
-----------------------
- ``upgrade_started``      – session opened (includes task list + worker info)
- ``upgrade_log``          – timestamped log line
- ``upgrade_task_start``   – beginning a specific migration task
- ``upgrade_task_complete``– task finished (includes token delta)
- ``upgrade_file_diff``    – proposed file change
- ``upgrade_token_tick``   – cumulative token usage update
- ``upgrade_narration``    – plain-English narrator commentary (Haiku)
- ``upgrade_complete``     – all tasks done
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.clients import git_client
from app.clients.llm_client import chat
from app.config import settings
from app.repos.scout_repo import get_scout_run, update_scout_run
from app.ws_manager import manager as ws_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker descriptor
# ---------------------------------------------------------------------------

@dataclass
class _WorkerSlot:
    """Defines a concurrent LLM worker."""
    label: str          # "opus" or "sonnet"
    api_key: str
    model: str
    display: str        # Human label for logs — e.g. "Opus 4.6"


# ---------------------------------------------------------------------------
# In-memory execution state
# ---------------------------------------------------------------------------

_active_upgrades: dict[str, dict] = {}  # run_id -> state dict


def get_upgrade_status(run_id: str) -> dict | None:
    """Return the current upgrade execution state (for polling fallback)."""
    return _active_upgrades.get(run_id)


def set_narrator_watching(run_id: str, watching: bool) -> bool:
    """Toggle the narrator on/off.  Returns True if state was found."""
    state = _active_upgrades.get(run_id)
    if state is None:
        return False
    state["narrator_watching"] = watching
    return True


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------

_SLASH_COMMANDS = {
    "/start":  "Begin the upgrade execution.",
    "/pause":  "Pause execution after the current task finishes.",
    "/resume": "Resume a paused execution.",
    "/stop":   "Abort execution entirely (current task will complete first).",
    "/push":   "Apply proposed changes, commit, and push to GitHub.",
    "/status": "Print a summary of current progress.",
    "/help":   "Show available slash commands.",
    "/clear":  "Clear the activity log.",
}


def get_available_commands() -> dict:
    """Return the command catalog for autocomplete."""
    return dict(_SLASH_COMMANDS)


async def send_command(user_id: str, run_id: str, command: str) -> dict:
    """Process a slash command for a running upgrade session.

    Returns ``{"ok": True/False, "message": str}``.
    """
    cmd = command.strip().lower()
    state = _active_upgrades.get(run_id)

    # /help and /clear don't need an active session
    if cmd == "/help":
        lines = ["Available commands:"]
        for c, desc in _SLASH_COMMANDS.items():
            lines.append(f"  {c:10s} {desc}")
        msg = "\n".join(lines)
        await _log(user_id, run_id, msg, "system", "command")
        return {"ok": True, "message": msg}

    if cmd == "/clear":
        if state:
            state["logs"] = []
        await _emit(user_id, "upgrade_clear_logs", {"run_id": run_id})
        return {"ok": True, "message": "Log cleared."}

    if state is None:
        return {"ok": False, "message": "No active upgrade session for this run."}

    if cmd == "/start":
        # Normally intercepted by the frontend; backend just acknowledges
        await _log(user_id, run_id, "▶️  Starting upgrade…", "system", "command")
        return {"ok": True, "message": "Starting execution."}

    if cmd == "/pause":
        if state["status"] != "running":
            return {"ok": False, "message": f"Cannot pause — status is '{state['status']}'."}
        pause_ev: asyncio.Event = state["_pause_event"]
        if not pause_ev.is_set():
            return {"ok": False, "message": "Already paused."}
        pause_ev.clear()  # workers will block on this
        state["status"] = "paused"
        await _log(user_id, run_id, "⏸️  Execution paused. Type /resume to continue.", "system", "command")
        await _emit(user_id, "upgrade_paused", {"run_id": run_id})
        return {"ok": True, "message": "Paused."}

    if cmd == "/resume":
        if state["status"] != "paused":
            return {"ok": False, "message": f"Cannot resume — status is '{state['status']}'."}
        pause_ev = state["_pause_event"]
        pause_ev.set()
        state["status"] = "running"
        await _log(user_id, run_id, "▶️  Execution resumed.", "system", "command")
        await _emit(user_id, "upgrade_resumed", {"run_id": run_id})
        return {"ok": True, "message": "Resumed."}

    if cmd == "/stop":
        if state["status"] not in ("running", "paused"):
            return {"ok": False, "message": f"Cannot stop — status is '{state['status']}'."}
        state["_stop_flag"].set()
        # Also unblock pause if paused
        state["_pause_event"].set()
        state["status"] = "stopping"
        await _log(user_id, run_id, "🛑 Stopping… current task will finish first.", "system", "command")
        await _emit(user_id, "upgrade_stopping", {"run_id": run_id})
        return {"ok": True, "message": "Stopping after current task."}

    if cmd == "/push":
        task_results = state.get("task_results", [])
        all_changes: list[dict] = []
        for tr in task_results:
            llm_result = tr.get("llm_result") or {}
            for change in llm_result.get("changes", []):
                all_changes.append(change)

        if not all_changes:
            await _log(user_id, run_id, "📌 No file changes to push yet.", "warn", "command")
            return {"ok": True, "message": "No changes to push."}

        # Build manifest
        await _log(user_id, run_id,
                    f"📦 Push manifest — {len(all_changes)} file change(s):",
                    "system", "command")
        files_by_action: dict[str, list[str]] = {}
        for c in all_changes:
            act = c.get("action", "modify")
            files_by_action.setdefault(act, []).append(c.get("file", "?"))
        for act, files in files_by_action.items():
            icon = {"modify": "✏️", "create": "➕", "delete": "🗑️"}.get(act, "📄")
            for f in files:
                await _log(user_id, run_id, f"  {icon} {act}: {f}", "info", "command")

        return await _push_changes(user_id, run_id, state, all_changes, task_results)

    if cmd == "/status":
        completed = state.get("completed_tasks", 0)
        total = state.get("total_tasks", 0)
        tok = state.get("tokens", {})
        tok_total = tok.get("total", 0) if isinstance(tok, dict) else 0
        msg = (
            f"Status: {state['status']} | "
            f"{completed}/{total} tasks | "
            f"{tok_total} tokens used"
        )
        await _log(user_id, run_id, f"📊 {msg}", "system", "command")
        return {"ok": True, "message": msg}

    # Unknown command — echo back
    await _log(user_id, run_id, f"Unknown command: {cmd}. Type /help for options.", "warn", "command")
    return {"ok": False, "message": f"Unknown command: {cmd}"}


# ---------------------------------------------------------------------------
# WS helpers
# ---------------------------------------------------------------------------


async def _emit(user_id: str, event_type: str, payload: dict) -> None:
    """Send a typed WS event to the user."""
    await ws_manager.send_to_user(user_id, {
        "type": event_type,
        "payload": payload,
    })


async def _log(
    user_id: str,
    run_id: str,
    message: str,
    level: str = "info",
    source: str = "forge-ide",
) -> None:
    """Emit a single log line and record it in state."""
    ts = datetime.now(timezone.utc).isoformat()
    entry = {"timestamp": ts, "source": source, "level": level, "message": message}
    state = _active_upgrades.get(run_id)
    if state:
        state["logs"].append(entry)
    await _emit(user_id, "upgrade_log", {"run_id": run_id, **entry})


# ---------------------------------------------------------------------------
# File change application
# ---------------------------------------------------------------------------


def _apply_file_change(working_dir: str, change: dict) -> None:
    """Apply a single LLM-proposed file change to the working directory."""
    rel_path = change.get("file", "")
    if not rel_path:
        raise ValueError("Change has no file path")
    file_path = Path(working_dir) / rel_path
    action = change.get("action", "modify")

    if action == "create":
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(change.get("after_snippet", ""), encoding="utf-8")
    elif action == "delete":
        if file_path.exists():
            file_path.unlink()
    elif action == "modify":
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        content = file_path.read_text(encoding="utf-8")
        before = change.get("before_snippet", "")
        after = change.get("after_snippet", "")
        if before and before in content:
            content = content.replace(before, after, 1)
        elif after:
            # Fallback: overwrite with after_snippet if before not found
            content = after
        file_path.write_text(content, encoding="utf-8")
    else:
        raise ValueError(f"Unknown action: {action}")


async def _push_changes(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    task_results: list[dict],
) -> dict:
    """Apply file changes to the cloned workspace, commit, and push."""
    working_dir = state.get("working_dir")
    access_token = state.get("access_token", "")
    repo_name = state.get("repo_name", "unknown")

    if not working_dir or not Path(working_dir).exists():
        await _log(user_id, run_id,
                   "❌ No working directory — repository was not cloned.",
                   "error", "command")
        await _log(user_id, run_id,
                   "💡 Close the IDE and re-open to trigger cloning.",
                   "info", "command")
        return {"ok": False, "message": "No working directory available."}

    # ── Apply file changes ──────────────────────────────────────────
    await _log(user_id, run_id,
               f"📝 Applying {len(all_changes)} file change(s)…",
               "system", "command")
    applied, failed = 0, 0
    for c in all_changes:
        try:
            _apply_file_change(working_dir, c)
            applied += 1
            await _log(user_id, run_id,
                       f"  ✅ {c.get('action', 'modify')}: {c.get('file', '?')}",
                       "info", "command")
        except Exception as exc:
            failed += 1
            await _log(user_id, run_id,
                       f"  ⚠ {c.get('file', '?')}: {exc}",
                       "warn", "command")

    summary = f"Applied {applied} change(s)"
    if failed:
        summary += f" ({failed} failed)"
    await _log(user_id, run_id, summary, "system", "command")

    if applied == 0:
        return {"ok": False, "message": "No changes could be applied."}

    if not access_token:
        await _log(user_id, run_id,
                   "❌ No GitHub access token — connect GitHub in Settings.",
                   "error", "command")
        await _log(user_id, run_id,
                   "💡 Changes applied locally. Connect GitHub to push.",
                   "info", "command")
        return {"ok": False, "message": "No GitHub access token."}

    # ── Build commit message ────────────────────────────────────────
    task_lines = []
    for tr in task_results:
        if tr.get("status") == "proposed":
            task_lines.append(
                f"- {tr['task_name']}: {tr.get('changes_count', 0)} change(s)")
    commit_msg = (
        f"forge: upgrade — {repo_name}\n\n"
        f"{applied} file(s) changed across "
        f"{len(task_results)} migration task(s):\n"
        + "\n".join(task_lines)
        + "\n\nGenerated by ForgeGuard Upgrade IDE"
    )

    # ── Git add → commit → push ─────────────────────────────────────
    try:
        await _log(user_id, run_id, "📋 Staging changes…", "system", "command")
        await git_client.add_all(working_dir)

        await _log(user_id, run_id, "💾 Committing…", "system", "command")
        sha = await git_client.commit(working_dir, commit_msg)
        if sha:
            await _log(user_id, run_id,
                       f"  Commit {sha[:8]}", "info", "command")

        # Detect branch
        try:
            branch = (await git_client._run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"], cwd=working_dir,
            )).strip() or "main"
        except Exception:
            branch = state.get("branch", "main")

        remote_url = f"https://github.com/{repo_name}.git"
        await git_client.set_remote(working_dir, remote_url)

        # Pull rebase first
        force = False
        try:
            await _log(user_id, run_id,
                       "🔄 Pulling latest changes…", "system", "command")
            await git_client.pull_rebase(
                working_dir, branch=branch, access_token=access_token)
        except RuntimeError:
            force = True
            await _log(user_id, run_id,
                       "  ⚠ Rebase failed — will force-push",
                       "warn", "command")

        await _log(user_id, run_id,
                   f"🚀 Pushing to {repo_name} (branch: {branch})…",
                   "system", "command")
        await git_client.push(
            working_dir, branch=branch, access_token=access_token,
            force_with_lease=force)

        commit_part = f" (commit {sha[:8]})" if sha else ""
        await _log(user_id, run_id,
                   f"✅ Pushed to github.com/{repo_name}{commit_part}",
                   "system", "command")

        await _emit(user_id, "upgrade_pushed", {
            "run_id": run_id,
            "repo_name": repo_name,
            "commit_sha": sha or "",
            "changes_count": applied,
            "branch": branch,
        })
        return {"ok": True,
                "message": f"Pushed {applied} changes to {repo_name}"}
    except Exception as exc:
        logger.exception("Git push failed for run %s", run_id)
        await _log(user_id, run_id,
                   f"❌ Push failed: {exc}", "error", "command")
        return {"ok": False, "message": f"Push failed: {exc}"}


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------

@dataclass
class _TokenAccumulator:
    """Cumulative token counter — Opus (coding) + Sonnet (planning) + Haiku (narration)."""
    opus_in: int = 0
    opus_out: int = 0
    sonnet_in: int = 0
    sonnet_out: int = 0
    haiku_in: int = 0
    haiku_out: int = 0

    @property
    def total(self) -> int:
        return (self.opus_in + self.opus_out
                + self.sonnet_in + self.sonnet_out
                + self.haiku_in + self.haiku_out)

    def add(self, worker_label: str, input_tokens: int, output_tokens: int) -> None:
        if worker_label == "opus":
            self.opus_in += input_tokens
            self.opus_out += output_tokens
        elif worker_label == "sonnet":
            self.sonnet_in += input_tokens
            self.sonnet_out += output_tokens
        else:  # haiku
            self.haiku_in += input_tokens
            self.haiku_out += output_tokens

    def snapshot(self) -> dict:
        return {
            "opus": {"input": self.opus_in, "output": self.opus_out,
                     "total": self.opus_in + self.opus_out},
            "sonnet": {"input": self.sonnet_in, "output": self.sonnet_out,
                       "total": self.sonnet_in + self.sonnet_out},
            "haiku": {"input": self.haiku_in, "output": self.haiku_out,
                       "total": self.haiku_in + self.haiku_out},
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_TASK_SYSTEM_PROMPT = """\
You are ForgeGuard's Upgrade IDE assistant. You are executing a specific \
migration task on a repository. You will receive the task details including \
current state, target state, and step-by-step instructions.

For each step, explain what you're doing, then describe the changes needed. \
Think through implications carefully.

Respond with valid JSON matching this schema:
{
  "thinking": ["step-by-step reasoning about this migration..."],
  "changes": [
    {
      "file": "path/to/file",
      "action": "modify" | "create" | "delete",
      "description": "what this change does",
      "before_snippet": "relevant code before (for modify)",
      "after_snippet": "code after the change"
    }
  ],
  "warnings": ["any risks or things to watch out for"],
  "verification_steps": ["how to verify this migration worked"],
  "status": "proposed"
}

Be thorough but concise. Focus on concrete, actionable changes.

IMPORTANT: Return ONLY the JSON object. Do NOT wrap it in markdown code fences."""

_CODEBLOCK_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_codeblock(text: str) -> str:
    """Remove optional ```json ... ``` wrapper and whitespace."""
    text = text.strip()
    m = _CODEBLOCK_RE.match(text)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


# ---------------------------------------------------------------------------
# Narrator system (Haiku — lightweight plain-English commentary)
# ---------------------------------------------------------------------------

_NARRATOR_SYSTEM_PROMPT = """\
You are a friendly technical narrator for ForgeGuard's Upgrade IDE. \
Your job is to explain what's happening during a code migration in simple, \
clear language that anyone can understand — even non-developers.

Rules:
- Keep it to 1-2 sentences MAX.
- Be warm, clear, and reassuring.
- Use analogies when helpful (renovating a building, upgrading wiring, etc.).
- Avoid code jargon, function names, or technical specifics.
- Focus on the *why* and *what it means*, not the *how*."""


async def _narrate(
    user_id: str,
    run_id: str,
    event_summary: str,
    *,
    narrator_key: str,
    narrator_model: str,
    tokens: _TokenAccumulator,
) -> None:
    """Fire a Haiku narration call (non-blocking). Failures are silently logged.

    Skips silently when the user is not viewing the Narrator tab
    (``narrator_watching`` flag in state).
    """
    # Respect the watching flag — don't waste tokens if nobody is looking
    state = _active_upgrades.get(run_id)
    if state and not state.get("narrator_watching", False):
        return
    try:
        result = await chat(
            api_key=narrator_key,
            model=narrator_model,
            system_prompt=_NARRATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": event_summary}],
            max_tokens=150,
            provider="anthropic",
        )
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        tokens.add("haiku", usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        text: str = result["text"] if isinstance(result, dict) else str(result)
        ts = datetime.now(timezone.utc).isoformat()
        state = _active_upgrades.get(run_id)
        if state:
            state["tokens"] = tokens.snapshot()
        await _emit(user_id, "upgrade_narration", {
            "run_id": run_id,
            "text": text.strip(),
            "timestamp": ts,
        })
        # Also emit token tick so header counter stays updated
        await _emit(user_id, "upgrade_token_tick", {
            "run_id": run_id,
            **tokens.snapshot(),
        })
    except Exception:
        logger.debug("Narration failed (non-critical)", exc_info=True)


# ---------------------------------------------------------------------------
# Workspace preparation (clone on IDE open)
# ---------------------------------------------------------------------------


async def prepare_upgrade_workspace(
    user_id: UUID,
    run_id: UUID,
    *,
    access_token: str = "",
) -> dict[str, Any]:
    """Clone the target repo into a temp working directory.

    Called when the Forge IDE modal opens (before ``/start``).  Emits WS
    events so the user sees clone progress in the activity log.  Stores
    ``working_dir`` and ``access_token`` in ``_active_upgrades`` for
    later use by ``/push``.
    """
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")

    uid = str(user_id)
    rid = str(run_id)
    repo_name = run.get("repo_name", "")
    if not repo_name:
        raise ValueError("No repository name on this run")

    # Create pre-execution state so _log can append
    _active_upgrades[rid] = {
        "status": "preparing",
        "run_id": rid,
        "repo_name": repo_name,
        "working_dir": None,
        "access_token": access_token,
        "total_tasks": 0,
        "completed_tasks": 0,
        "current_task": None,
        "task_results": [],
        "logs": [],
        "tokens": {"opus": {"input": 0, "output": 0, "total": 0},
                    "sonnet": {"input": 0, "output": 0, "total": 0},
                    "haiku": {"input": 0, "output": 0, "total": 0},
                    "total": 0},
        "narrator_enabled": False,
        "narrator_watching": False,
    }

    await _log(uid, rid, f"📡 Preparing workspace for {repo_name}…", "system")

    # Clone the repository
    clone_url = f"https://github.com/{repo_name}.git"
    tmp_root = tempfile.mkdtemp(prefix="forgeguard_upgrade_")
    clone_dest = str(Path(tmp_root) / repo_name.split("/")[-1])

    await _log(uid, rid, f"📦 Cloning {repo_name}…", "system")
    try:
        await git_client.clone_repo(
            clone_url,
            clone_dest,
            shallow=True,
            access_token=access_token if access_token else None,
        )
    except Exception as exc:
        await _log(uid, rid, f"❌ Clone failed: {exc}", "error")
        shutil.rmtree(tmp_root, ignore_errors=True)
        # Keep state so IDE still opens — analysis works without clone
        _active_upgrades[rid]["status"] = "ready"
        await _log(uid, rid,
                   "⚠ Workspace unavailable — /push won't work, but "
                   "analysis (/start) will still run.", "warn")
        await _log(uid, rid, "", "system")
        await _log(uid, rid,
                   "🟢 Ready — type /start and press Enter to begin",
                   "system")
        return {
            "run_id": rid,
            "status": "ready",
            "repo_name": repo_name,
            "file_count": 0,
            "branch": "main",
            "clone_ok": False,
        }

    # Count files
    try:
        files = await git_client.get_file_list(clone_dest)
        file_count = len(files)
    except Exception:
        file_count = 0

    # Detect default branch
    try:
        branch = (await git_client._run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=clone_dest,
        )).strip() or "main"
    except Exception:
        branch = "main"

    _active_upgrades[rid]["working_dir"] = clone_dest
    _active_upgrades[rid]["status"] = "ready"
    _active_upgrades[rid]["branch"] = branch

    await _log(uid, rid,
               f"✅ Repository cloned — {file_count} files ({branch})",
               "system")
    await _log(uid, rid, "", "system")
    await _log(uid, rid,
               "🟢 Ready — type /start and press Enter to begin upgrade",
               "system")

    return {
        "run_id": rid,
        "status": "ready",
        "repo_name": repo_name,
        "file_count": file_count,
        "branch": branch,
        "clone_ok": True,
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def execute_upgrade(
    user_id: UUID,
    run_id: UUID,
    *,
    api_key: str = "",
    api_key_2: str = "",
) -> dict[str, Any]:
    """Begin upgrade execution for a deep-scan run that has a renovation plan.

    Parameters
    ----------
    api_key : str
        User's primary Anthropic BYOK key (Sonnet for analysis).
    api_key_2 : str
        User's second Anthropic BYOK key (Haiku for narration).

    Returns immediately with session info; heavy work runs in background.
    """
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(user_id):
        raise ValueError("Scout run not found")
    if run.get("scan_type") != "deep":
        raise ValueError("Upgrade execution requires a deep scan run")

    results = run.get("results")
    if results is None:
        raise ValueError("No results available")
    if isinstance(results, str):
        results = json.loads(results)

    plan = results.get("renovation_plan")
    if plan is None:
        raise ValueError("No renovation plan found. Generate one first.")

    uid = str(user_id)
    rid = str(run_id)

    # Prevent duplicate execution
    existing = _active_upgrades.get(rid)
    if existing and existing.get("status") == "running":
        raise ValueError("Upgrade is already in progress for this run")

    # Preserve workspace from prepare phase
    prepared_dir = existing.get("working_dir") if existing else None
    prepared_token = existing.get("access_token", "") if existing else ""
    prepared_branch = existing.get("branch", "main") if existing else "main"
    prepared_logs = existing.get("logs", []) if existing else []

    # Resolve API keys — prefer user BYOK, fall back to server env
    key1 = (api_key or "").strip() or settings.ANTHROPIC_API_KEY
    key2 = (api_key_2 or "").strip()

    if not key1:
        raise ValueError(
            "No Anthropic API key available. Add your key in Settings."
        )

    # Build Sonnet worker — upgrade analysis/planning goes through Sonnet
    # (Opus is reserved for heavy code generation in build_service)
    sonnet_worker = _WorkerSlot(
        label="sonnet",
        api_key=key1,
        model=settings.LLM_PLANNER_MODEL,
        display="Sonnet",
    )

    # Narrator config — Key 2 powers Haiku narration (if available)
    narrator_key = key2 if key2 else ""
    narrator_model = settings.LLM_NARRATOR_MODEL if key2 else ""
    narrator_enabled = bool(narrator_key)

    tasks = plan.get("migration_recommendations", [])

    # Pause/stop coordination events
    pause_event = asyncio.Event()
    pause_event.set()  # starts unpaused (set = running)
    stop_flag = asyncio.Event()  # set = please stop

    _active_upgrades[rid] = {
        "status": "running",
        "run_id": rid,
        "repo_name": run.get("repo_name", ""),
        "total_tasks": len(tasks),
        "completed_tasks": 0,
        "current_task": None,
        "task_results": [],
        "logs": prepared_logs,
        "tokens": {"opus": {"input": 0, "output": 0, "total": 0},
                    "sonnet": {"input": 0, "output": 0, "total": 0},
                    "haiku": {"input": 0, "output": 0, "total": 0},
                    "total": 0},
        "narrator_enabled": narrator_enabled,
        "narrator_watching": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "working_dir": prepared_dir,
        "access_token": prepared_token,
        "branch": prepared_branch,
        # private control handles (not serialised)
        "_pause_event": pause_event,
        "_stop_flag": stop_flag,
    }

    asyncio.create_task(
        _run_upgrade(uid, rid, run, results, plan, tasks, sonnet_worker,
                     narrator_key=narrator_key, narrator_model=narrator_model)
    )

    return {
        "run_id": rid,
        "status": "running",
        "total_tasks": len(tasks),
        "repo_name": run.get("repo_name", ""),
        "narrator_enabled": narrator_enabled,
        "workers": ["sonnet"],
    }


# ---------------------------------------------------------------------------
# Background orchestrator
# ---------------------------------------------------------------------------


async def _run_upgrade(
    user_id: str,
    run_id: str,
    run: dict,
    results: dict,
    plan: dict,
    tasks: list[dict],
    worker: _WorkerSlot,
    narrator_key: str = "",
    narrator_model: str = "",
) -> None:
    """Background coroutine: Sonnet analyses tasks, Haiku narrates."""
    state = _active_upgrades[run_id]
    repo_name = run.get("repo_name", "unknown")
    stack_profile = results.get("stack_profile", {})
    tokens = _TokenAccumulator()
    narrator_enabled = bool(narrator_key)

    try:
        # ── Startup announcements ────────────────────────────────
        task_descriptors = [
            {
                "id": t.get("id", f"TASK-{i}"),
                "name": f"{t.get('from_state', '?')} → {t.get('to_state', '?')}",
                "priority": t.get("priority", "medium"),
                "effort": t.get("effort", "medium"),
                "forge_automatable": t.get("forge_automatable", False),
                "category": t.get("category", ""),
                "worker": "sonnet",
            }
            for i, t in enumerate(tasks)
        ]

        await _emit(user_id, "upgrade_started", {
            "run_id": run_id,
            "repo_name": repo_name,
            "total_tasks": len(tasks),
            "narrator_enabled": narrator_enabled,
            "workers": [{"label": worker.label, "model": worker.model, "display": worker.display}],
            "tasks": task_descriptors,
        })

        await _log(user_id, run_id, f"🚀 Forge IDE — Starting upgrade for {repo_name}", "system")
        await _log(user_id, run_id, f"🤖 Sonnet analysing migration tasks", "system")
        if narrator_enabled:
            await _log(user_id, run_id, "🎙️ Haiku narrator active — plain-English commentary enabled", "system")
        await _log(user_id, run_id, f"📋 {len(tasks)} migration task(s) queued", "system")

        executive = plan.get("executive_brief") or {}
        if executive.get("headline"):
            await _log(user_id, run_id, f"📊 Assessment: {executive['headline']}", "info")
        if executive.get("health_grade"):
            await _log(user_id, run_id, f"🏥 Health grade: {executive['health_grade']}", "info")

        await asyncio.sleep(0.5)

        # Fire opening narration (non-blocking)
        if narrator_enabled:
            brief = executive.get("headline", "")
            asyncio.create_task(_narrate(
                user_id, run_id,
                f"Starting upgrade for repository '{repo_name}'. "
                f"{len(tasks)} migration tasks queued. {brief}",
                narrator_key=narrator_key, narrator_model=narrator_model,
                tokens=tokens,
            ))

        async def _process_task(
            task_index: int,
            task: dict,
        ) -> None:
            """Process one migration task with the given worker."""
            task_id = task.get("id", f"TASK-{task_index}")
            task_name = f"{task.get('from_state', '?')} → {task.get('to_state', '?')}"
            worker_tag = f"[{worker.display}]"

            await _emit(user_id, "upgrade_task_start", {
                "run_id": run_id,
                "task_id": task_id,
                "task_index": task_index,
                "task_name": task_name,
                "priority": task.get("priority", "medium"),
                "category": task.get("category", ""),
                "steps": task.get("steps", []),
                "worker": worker.label,
            })

            await _log(user_id, run_id, "", "system")
            await _log(
                user_id, run_id,
                f"━━━ {worker_tag} Task {task_index + 1}/{len(tasks)}: {task_name} ━━━",
                "system",
            )
            await _log(user_id, run_id,
                        f"Priority: {task.get('priority', '?')} | "
                        f"Effort: {task.get('effort', '?')} | "
                        f"Risk: {task.get('risk', '?')}", "info")
            if task.get("rationale"):
                await _log(user_id, run_id, f"Rationale: {task['rationale']}", "info")

            steps = task.get("steps", [])
            if steps:
                await _log(user_id, run_id, "Planned steps:", "info")
                for si, step in enumerate(steps):
                    await _log(user_id, run_id, f"  {si + 1}. {step}", "info")

            await asyncio.sleep(0.3)

            await _log(user_id, run_id,
                        f"🤖 {worker_tag} Analyzing with {worker.display}…", "thinking")

            task_result, usage = await _analyze_task_with_llm(
                user_id, run_id, repo_name, stack_profile, task,
                api_key=worker.api_key,
                model=worker.model,
            )

            # Accumulate tokens
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            tokens.add(worker.label, inp, out)

            # Emit token tick
            snap = tokens.snapshot()
            await _emit(user_id, "upgrade_token_tick", {
                "run_id": run_id,
                **snap,
            })

            if task_result:
                thinking = task_result.get("thinking", [])
                for thought in thinking:
                    await _log(user_id, run_id, f"💭 {worker_tag} {thought}", "thinking")
                    await asyncio.sleep(0.12)

                changes = task_result.get("changes", [])
                if changes:
                    await _log(user_id, run_id,
                                f"📝 {worker_tag} {len(changes)} file change(s) proposed:", "system")
                    for change in changes:
                        action_icon = {"modify": "✏️", "create": "➕", "delete": "🗑️"}.get(
                            change.get("action", "modify"), "📄"
                        )
                        await _log(
                            user_id, run_id,
                            f"  {action_icon} {change.get('file', '?')} — {change.get('description', '')}",
                            "info",
                        )
                        await _emit(user_id, "upgrade_file_diff", {
                            "run_id": run_id,
                            "task_id": task_id,
                            "worker": worker.label,
                            **change,
                        })
                        await asyncio.sleep(0.08)

                for warn in task_result.get("warnings", []):
                    await _log(user_id, run_id, f"⚠️ {worker_tag} {warn}", "warn")

                verifications = task_result.get("verification_steps", [])
                if verifications:
                    await _log(user_id, run_id, f"✅ {worker_tag} Verification steps:", "info")
                    for v in verifications:
                        await _log(user_id, run_id, f"  → {v}", "info")

                result_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "proposed",
                    "changes_count": len(changes),
                    "worker": worker.label,
                    "tokens": {"input": inp, "output": out},
                    "llm_result": task_result,
                }
                await _log(user_id, run_id,
                            f"✅ {worker_tag} Task {task_id} complete — {len(changes)} changes proposed",
                            "system")
            else:
                result_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "skipped",
                    "changes_count": 0,
                    "worker": worker.label,
                    "tokens": {"input": inp, "output": out},
                }
                await _log(user_id, run_id,
                            f"⏭️ {worker_tag} Task {task_id} skipped (no LLM result)", "warn")

            # State update (sequential — no lock needed)
            state["task_results"].append(result_entry)
            state["completed_tasks"] += 1
            state["tokens"] = snap

            await _emit(user_id, "upgrade_task_complete", {
                "run_id": run_id,
                "task_id": task_id,
                "task_index": task_index,
                "status": result_entry["status"],
                "changes_count": result_entry["changes_count"],
                "worker": worker.label,
                "token_delta": {"input": inp, "output": out},
                "token_cumulative": snap,
            })

        # ── Sequential task execution (Opus) ─────────────────────
        for task_index, task in enumerate(tasks):
            # Check /stop
            if state["_stop_flag"].is_set():
                await _log(user_id, run_id,
                           f"🛑 [{worker.display}] Stopped by user.", "system")
                break
            # Honour /pause — block until resumed
            if not state["_pause_event"].is_set():
                await _log(user_id, run_id,
                           f"⏸️  [{worker.display}] Paused — waiting for /resume…",
                           "system")
                await state["_pause_event"].wait()
                # After resume, re-check stop
                if state["_stop_flag"].is_set():
                    break
            await _process_task(task_index, task)
            # Fire narration after each task (non-blocking)
            if narrator_enabled:
                task_name = f"{task.get('from_state', '?')} → {task.get('to_state', '?')}"
                asyncio.create_task(_narrate(
                    user_id, run_id,
                    f"Just finished task {task_index + 1}/{len(tasks)}: '{task_name}'. "
                    f"{state['completed_tasks']}/{len(tasks)} tasks done so far.",
                    narrator_key=narrator_key, narrator_model=narrator_model,
                    tokens=tokens,
                ))
            await asyncio.sleep(0.2)

        # ── Wrap up ──────────────────────────────────────────────
        was_stopped = state["_stop_flag"].is_set()
        total_changes = sum(r.get("changes_count", 0) for r in state["task_results"])
        proposed = sum(1 for r in state["task_results"] if r["status"] == "proposed")
        skipped = sum(1 for r in state["task_results"] if r["status"] == "skipped")
        final_tokens = tokens.snapshot()

        await _log(user_id, run_id, "", "system")
        await _log(user_id, run_id, "━━━ Upgrade Analysis Complete ━━━", "system")
        await _log(user_id, run_id,
                    f"📊 {proposed} task(s) analysed, {skipped} skipped", "system")
        await _log(user_id, run_id,
                    f"📝 {total_changes} total file changes proposed", "system")
        await _log(user_id, run_id,
                    f"⚡ Tokens used — Opus: {_fmt_tokens(final_tokens['opus']['total'])} | "
                    f"Haiku: {_fmt_tokens(final_tokens['haiku']['total'])} | "
                    f"Total: {_fmt_tokens(final_tokens['total'])}",
                    "system")
        if was_stopped:
            await _log(user_id, run_id,
                        "🛑 Execution was stopped by user before all tasks completed.", "system")
        await _log(
            user_id, run_id,
            "💡 Review the proposed changes above. In a future release, "
            "Forge will apply these changes automatically.",
            "system",
        )

        final_status = "stopped" if was_stopped else "completed"
        state["status"] = final_status
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["tokens"] = final_tokens

        results["upgrade_execution"] = {
            "status": final_status,
            "task_results": state["task_results"],
            "total_changes": total_changes,
            "tokens": final_tokens,
            "completed_at": state["completed_at"],
        }
        await _update_run_results(run.get("id") or run_id, results, run)

        await _emit(user_id, "upgrade_complete", {
            "run_id": run_id,
            "status": final_status,
            "total_tasks": len(tasks),
            "proposed": proposed,
            "skipped": skipped,
            "total_changes": total_changes,
            "tokens": final_tokens,
        })

    except Exception:
        logger.exception("Upgrade execution %s failed", run_id)
        state["status"] = "error"
        await _log(user_id, run_id, "❌ Upgrade execution failed with an unexpected error", "error")
        await _emit(user_id, "upgrade_complete", {
            "run_id": run_id,
            "status": "error",
            "tokens": tokens.snapshot(),
        })
    finally:
        state["current_task"] = None
        # Clean up cloned workspace
        wd = state.get("working_dir")
        if wd:
            parent = str(Path(wd).parent)
            shutil.rmtree(parent, ignore_errors=True)
        await asyncio.sleep(300)
        _active_upgrades.pop(run_id, None)


def _fmt_tokens(n: int) -> str:
    """Format token count: 142300 → '142.3k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ---------------------------------------------------------------------------
# LLM task analysis
# ---------------------------------------------------------------------------


async def _analyze_task_with_llm(
    user_id: str,
    run_id: str,
    repo_name: str,
    stack_profile: dict,
    task: dict,
    *,
    api_key: str,
    model: str,
) -> tuple[dict | None, dict]:
    """Use LLM to analyze a migration task and propose concrete changes.

    Returns ``(parsed_result | None, usage_dict)``.
    """
    if not api_key:
        await _log(user_id, run_id, "No API key configured — skipping LLM analysis", "warn")
        return None, {"input_tokens": 0, "output_tokens": 0}

    user_msg = json.dumps({
        "repository": repo_name,
        "stack": stack_profile,
        "migration": {
            "id": task.get("id"),
            "from_state": task.get("from_state"),
            "to_state": task.get("to_state"),
            "category": task.get("category"),
            "rationale": task.get("rationale"),
            "steps": task.get("steps", []),
            "effort": task.get("effort"),
            "risk": task.get("risk"),
        },
    }, indent=2)

    try:
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_TASK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=4096,
            provider="anthropic",
        )

        # Extract usage
        usage = (result.get("usage", {}) if isinstance(result, dict)
                 else {"input_tokens": 0, "output_tokens": 0})

        text: str = result["text"] if isinstance(result, dict) else str(result)  # type: ignore[index]
        text = _strip_codeblock(text)

        return json.loads(text), usage
    except Exception as exc:
        logger.exception("LLM task analysis failed for %s", task.get("id"))
        short = str(exc)[:200]
        await _log(user_id, run_id, f"LLM analysis failed for task {task.get('id')}: {short}", "error")
        return None, {"input_tokens": 0, "output_tokens": 0}


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------


async def _update_run_results(run_id: Any, results: dict, run: dict) -> None:
    """Persist updated results without changing status or counts."""
    await update_scout_run(
        run_id if isinstance(run_id, UUID) else UUID(str(run_id)),
        status=run.get("status", "completed"),
        results=results,
        checks_passed=run.get("checks_passed", 0),
        checks_failed=run.get("checks_failed", 0),
        checks_warned=run.get("checks_warned", 0),
    )
