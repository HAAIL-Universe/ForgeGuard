"""Upgrade executor — role-based LLM upgrade with live streaming.

Accepts a renovation plan (from upgrade_service) and executes migration
tasks using a role-based architecture:

- **Sonnet (Key 1)** — ``anthropic_api_key`` + ``LLM_PLANNER_MODEL``
  Plans and analyses migration tasks.  Tokens tracked under the
  ``sonnet`` bucket.
- **Opus (Key 1)** — ``anthropic_api_key`` + ``LLM_BUILDER_MODEL``
  Writes concrete code changes from Sonnet's plans.  Tokens tracked
  under the ``opus`` bucket.  Runs in parallel with Sonnet (pipeline).
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
import os
import re
import shutil
import subprocess
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


@dataclass
class _PlanPoolItem:
    """A completed plan sitting in the pool, waiting for the builder."""
    task_index: int
    task: dict
    plan_result: dict | None
    plan_usage: dict


@dataclass
class _RemediationItem:
    """An audit-failure fix sitting in the remediation pool.

    Sonnet generates *fix_plan* (a dict in the same schema as
    ``_plan_task_with_llm`` output) while the builder is busy.
    Opus picks it up between tasks and applies the fix.
    """
    file: str
    findings: list[str]
    original_change: dict
    task_id: str
    fix_plan: dict | None = None
    priority: int = 10            # lower = higher priority
    _seq: int = 0                 # monotonic tiebreaker for PriorityQueue

    def __lt__(self, other: "_RemediationItem") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self._seq < other._seq


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
    "/retry":  "Re-run failed/skipped tasks through Sonnet + Opus.",
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

    # ── Handle pending Y/N prompts ──────────────────────────────────
    if state and state.get("pending_prompt"):
        prompt_id = state["pending_prompt"]
        answer = cmd.strip().lower()
        if answer not in ("y", "n", "yes", "no"):
            await _log(user_id, run_id,
                       "⚠ Please type Y or N.", "warn", "command")
            return {"ok": False, "message": "Expected Y or N."}

        is_yes = answer in ("y", "yes")
        state["pending_prompt"] = None

        if prompt_id == "push_test_confirm":
            all_changes = state.pop("_push_changes", [])
            task_results = state.pop("_push_task_results", [])
            if is_yes:
                # Apply changes first, then run tests
                await _log(user_id, run_id, "", "system", "command")
                await _log(user_id, run_id,
                           "🧪 Applying changes and running tests…",
                           "system", "command")
                apply_ok = await _apply_all_changes(
                    user_id, run_id, state, all_changes)
                if not apply_ok:
                    return {"ok": False,
                            "message": "Failed to apply changes."}
                passed, output = await _run_tests(
                    user_id, run_id, state)
                if passed:
                    await _log(user_id, run_id,
                               "✅ Tests passed!", "system", "command")
                    return await _run_pre_push_audit(
                        user_id, run_id, state, all_changes, task_results)
                else:
                    # Tests failed — run tiered auto-fix loop before
                    # falling back to the Y/N force-push prompt.
                    state["_push_changes"] = all_changes
                    state["_push_task_results"] = task_results
                    state["_last_test_output"] = output

                    await _log(user_id, run_id, "", "system", "command")
                    await _log(user_id, run_id,
                               "╔══════════════════════════════════════════════════╗",
                               "system", "command")
                    await _log(user_id, run_id,
                               "║  ❌ Tests failed — starting auto-fix…            ║",
                               "system", "command")
                    await _log(user_id, run_id,
                               "╚══════════════════════════════════════════════════╝",
                               "system", "command")

                    fix_passed, fix_output = await _auto_fix_loop(
                        user_id, run_id, state, all_changes, output)

                    if fix_passed:
                        await _log(user_id, run_id, "", "system", "command")
                        await _log(user_id, run_id,
                                   "✅ Auto-fix succeeded — tests pass!",
                                   "system", "command")
                        return await _run_pre_push_audit(
                            user_id, run_id, state, all_changes,
                            task_results)
                    else:
                        # All tiers exhausted — ask force push Y/N
                        await _log(user_id, run_id, "", "system", "command")
                        await _log(user_id, run_id,
                                   "╔══════════════════════════════════════════════════╗",
                                   "system", "command")
                        await _log(user_id, run_id,
                                   "║  ❌ Auto-fix exhausted — push anyway?            ║",
                                   "system", "command")
                        await _log(user_id, run_id,
                                   "║                                                  ║",
                                   "system", "command")
                        await _log(user_id, run_id,
                                   "║  [Y] Push despite failures                       ║",
                                   "system", "command")
                        await _log(user_id, run_id,
                                   "║  [N] Cancel push                                 ║",
                                   "system", "command")
                        await _log(user_id, run_id,
                                   "╚══════════════════════════════════════════════════╝",
                                   "system", "command")
                        state["pending_prompt"] = "push_force_confirm"
                        await _emit(user_id, "upgrade_prompt", {
                            "run_id": run_id,
                            "prompt_id": "push_force_confirm"})
                        return {"ok": True,
                                "message": "Auto-fix exhausted. Y/N?"}
            else:
                # User chose N — skip tests, push directly
                await _log(user_id, run_id,
                           "⏩ Skipping tests — pushing directly…",
                           "system", "command")
                return await _push_changes(
                    user_id, run_id, state, all_changes, task_results)

        elif prompt_id == "push_force_confirm":
            if is_yes:
                all_changes = state.pop("_push_changes", [])
                task_results = state.pop("_push_task_results", [])
                await _log(user_id, run_id,
                           "⚠ Force pushing despite test failures…",
                           "warn", "command")
                return await _run_pre_push_audit(
                    user_id, run_id, state, all_changes, task_results)
            else:
                # Keep changes in state so /push works again
                await _log(user_id, run_id,
                           "🛑 Push cancelled. Fix the issues and try "
                           "/push again.", "system", "command")
                return {"ok": True, "message": "Push cancelled."}

        elif prompt_id == "push_audit_confirm":
            if is_yes:
                all_changes = state.pop("_push_changes", [])
                task_results = state.pop("_push_task_results", [])
                await _log(user_id, run_id,
                           "⚠ Pushing despite audit failures…",
                           "warn", "command")
                return await _commit_and_push(
                    user_id, run_id, state, all_changes, task_results)
            else:
                # Keep changes in state so /push works again
                await _log(user_id, run_id,
                           "🛑 Push cancelled. Fix the audit failures "
                           "and try /push again.", "system", "command")
                return {"ok": True, "message": "Push cancelled."}

        return {"ok": False, "message": "Unknown prompt."}

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

    if cmd == "/retry":
        if state["status"] not in ("completed", "stopped", "error"):
            return {"ok": False,
                    "message": "Wait for the run to finish before retrying."}

        # Tell _run_upgrade's finally block NOT to clean up this session
        state["_cleanup_cancelled"] = True

        # Collect all skipped/failed entries that have retry data
        failed_entries = [
            (idx, tr) for idx, tr in enumerate(state["task_results"])
            if tr.get("status") == "skipped" and tr.get("_retry_task")
        ]
        if not failed_entries:
            await _log(user_id, run_id,
                       "✅ No failed tasks to retry.", "system", "command")
            return {"ok": True, "message": "Nothing to retry."}

        # Retrieve stashed worker config
        sonnet_w: _WorkerSlot | None = state.get("_sonnet_worker")
        opus_w: _WorkerSlot | None = state.get("_opus_worker")
        if not sonnet_w or not opus_w:
            await _log(user_id, run_id,
                       "❌ Worker config unavailable — cannot retry.",
                       "error", "command")
            return {"ok": False, "message": "Worker config lost."}

        stack_profile = state.get("_stack_profile", {})
        repo_name = state.get("repo_name", "unknown")
        narrator_key = state.get("_narrator_key", "")
        narrator_model = state.get("_narrator_model", "")

        # Announce
        task_names = [
            tr.get("task_name")
            or f"{tr.get('_retry_task', {}).get('from_state', '?')} → "
               f"{tr.get('_retry_task', {}).get('to_state', '?')}"
            for _, tr in failed_entries
        ]
        await _log(user_id, run_id, "", "system", "command")
        await _log(user_id, run_id,
                   f"🔄 Retrying {len(failed_entries)} failed task(s):",
                   "system", "command")
        for name in task_names:
            await _log(user_id, run_id, f"  • {name}", "info", "command")
        await _log(user_id, run_id, "", "system", "command")

        # Launch retry in background
        state["status"] = "running"
        asyncio.create_task(
            _run_retry(user_id, run_id, state, failed_entries,
                       sonnet_w, opus_w, repo_name, stack_profile,
                       narrator_key=narrator_key,
                       narrator_model=narrator_model)
        )
        return {"ok": True,
                "message": f"Retrying {len(failed_entries)} task(s)."}

    if cmd == "/push":
        task_results = state.get("task_results", [])
        # Collect all planned file paths across tasks for final scope gate
        all_planned: set[str] = set()
        for tr in task_results:
            plan_data = (tr.get("llm_result") or {}).get("_plan_result") or {}
            for pe in plan_data.get("plan", []):
                pf = pe.get("file", "")
                if pf:
                    all_planned.add(pf)
            # Also pull from task-level plan stash
            plan_stash = tr.get("_plan") or {}
            for pe in plan_stash.get("plan", []):
                pf = pe.get("file", "")
                if pf:
                    all_planned.add(pf)

        all_changes: list[dict] = []
        dropped_at_push = 0
        for tr in task_results:
            llm_result = tr.get("llm_result") or {}
            for change in llm_result.get("changes", []):
                fpath = change.get("file", "")
                # Safety net: if we have planned files and this isn't one, drop it
                if all_planned and fpath and fpath not in all_planned:
                    dropped_at_push += 1
                    await _log(user_id, run_id,
                               f"  🚫 Push-gate dropped out-of-scope: {fpath}",
                               "warn", "command")
                    continue
                all_changes.append(change)

        if dropped_at_push:
            await _log(user_id, run_id,
                       f"⚠ {dropped_at_push} out-of-scope file(s) removed from push manifest.",
                       "warn", "command")

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

        # Stash changes for the prompt flow
        state["_push_changes"] = all_changes
        state["_push_task_results"] = task_results

        # Show confirmation box
        await _log(user_id, run_id, "", "system", "command")
        await _log(user_id, run_id, "╔══════════════════════════════════════════════════╗", "system", "command")
        await _log(user_id, run_id, "║  🧪 Run tests before committing and pushing?     ║", "system", "command")
        await _log(user_id, run_id, "║                                                  ║", "system", "command")
        await _log(user_id, run_id, "║  [Y] Run tests first  (recommended)              ║", "system", "command")
        await _log(user_id, run_id, "║  [N] Push directly — skip tests                  ║", "system", "command")
        await _log(user_id, run_id, "╚══════════════════════════════════════════════════╝", "system", "command")
        await _log(user_id, run_id, "", "system", "command")

        state["pending_prompt"] = "push_test_confirm"
        await _emit(user_id, "upgrade_prompt", {
            "run_id": run_id, "prompt_id": "push_test_confirm"})
        return {"ok": True, "message": "Awaiting Y/N response."}

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
# Inline per-file deterministic audit
# ---------------------------------------------------------------------------

# Re-use patterns from the audit engine (zero LLM cost)
from app.audit.engine import _SECRET_PATTERNS as _SECRETS_RE


def _audit_file_change(
    change: dict,
    *,
    planned_files: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Run deterministic checks on a single proposed file change.

    Returns ``(verdict, findings)`` where *verdict* is one of:
      - ``"PASS"`` — all checks OK
      - ``"FAIL"`` — content issues (syntax, secrets, etc.)
      - ``"REJECT"`` — hard block: file not in planner scope (change must
        be dropped entirely, not just flagged)

    *findings* is a (possibly empty) list of human-readable issue
    descriptions.

    Checks performed (all pure-Python, zero LLM cost):
      1. **Syntax** — ``compile()`` for ``.py``, ``json.loads()`` for ``.json``
      2. **Secrets scan** — regex patterns from ``app.audit.engine``
      3. **Import-star** — ``from X import *`` in Python files
      4. **Scope compliance** — file path was in Sonnet's planned file list
         (hard REJECT when violated)
    """
    content = change.get("after_snippet") or ""
    filepath = change.get("file", "")
    action = change.get("action", "modify")
    findings: list[str] = []

    # Deletions are always safe from a content perspective
    if action == "delete" or not content:
        return "PASS", []

    # 1. Syntax check
    if filepath.endswith(".py"):
        try:
            compile(content, filepath, "exec")
        except SyntaxError as exc:
            loc = f"{filepath}:{exc.lineno}" if exc.lineno else filepath
            findings.append(f"Syntax error — {loc}: {exc.msg}")
    elif filepath.endswith(".json"):
        try:
            json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append(f"Invalid JSON — {filepath}: {exc}")

    # 2. Secrets scan
    for pattern, description in _SECRETS_RE:
        if re.search(pattern, content):
            findings.append(f"Secret detected — {filepath}: {description}")

    # 3. Import-star check (Python only)
    if filepath.endswith(".py"):
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("from ") and "import *" in stripped:
                findings.append(
                    f"Wildcard import — {filepath}: {stripped}")

    # 4. Scope compliance (when Sonnet's plan is available)
    #    This is a HARD REJECT — out-of-scope changes must be dropped.
    if planned_files is not None and filepath and filepath not in planned_files:
        findings.append(
            f"Scope deviation — {filepath}: not in Sonnet's planned file list")
        return "REJECT", findings

    verdict = "FAIL" if findings else "PASS"
    return verdict, findings


# ---------------------------------------------------------------------------
# Evidence file writers (diff log + audit trail)
# ---------------------------------------------------------------------------


def _write_diff_log(
    working_dir: str,
    all_changes: list[dict],
    repo_name: str,
) -> str:
    """Write ``Forge/evidence/diff_log.md`` to the working directory.

    Generates a deterministic markdown summary of every proposed change
    so Scout checks A3/A5 pass.  Returns the absolute path written.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Diff Log",
        "",
        f"> Auto-generated by ForgeGuard Upgrade IDE — {now}",
        f"> Repository: `{repo_name}`",
        "",
        f"## Changes ({len(all_changes)} file(s))",
        "",
    ]

    for i, c in enumerate(all_changes, 1):
        action = c.get("action", "modify")
        filepath = c.get("file", "unknown")
        desc = c.get("description", "")
        lines.append(f"### {i}. `{filepath}` — {action}")
        if desc:
            lines.append(f"")
            lines.append(f"{desc}")
        before = c.get("before_snippet", "")
        after = c.get("after_snippet", "")
        if before:
            ext = Path(filepath).suffix.lstrip(".") or "text"
            lines.append("")
            lines.append(f"**Before:**")
            lines.append(f"```{ext}")
            # Truncate very large snippets to keep log readable
            lines.append(before[:2000] + ("…" if len(before) > 2000 else ""))
            lines.append("```")
        if after:
            ext = Path(filepath).suffix.lstrip(".") or "text"
            lines.append("")
            lines.append(f"**After:**")
            lines.append(f"```{ext}")
            lines.append(after[:2000] + ("…" if len(after) > 2000 else ""))
            lines.append("```")
        lines.append("")

    content = "\n".join(lines)
    evidence_dir = Path(working_dir) / "Forge" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_path = evidence_dir / "diff_log.md"
    out_path.write_text(content, encoding="utf-8")
    return str(out_path)


def _write_audit_trail(
    working_dir: str,
    audit_results: list[dict],
    repo_name: str,
) -> str:
    """Write ``Forge/evidence/audit_ledger.md`` to the working directory.

    Aggregates per-file audit verdicts into a markdown table for the
    Scout W2 check.  Returns the absolute path written.

    Each entry in *audit_results* is:
    ``{"file": str, "action": str, "verdict": str, "findings": list[str]}``
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = len(audit_results)
    passed = sum(1 for r in audit_results if r["verdict"] == "PASS")
    failed = total - passed

    lines = [
        "# Audit Ledger",
        "",
        f"> Auto-generated by ForgeGuard Upgrade IDE — {now}",
        f"> Repository: `{repo_name}`",
        "",
        f"**Summary:** {passed}/{total} files passed, {failed} failed",
        "",
        "| # | File | Action | Verdict | Findings |",
        "|---|------|--------|---------|----------|",
    ]

    for i, r in enumerate(audit_results, 1):
        icon = "✅" if r["verdict"] == "PASS" else "❌"
        findings_str = "; ".join(r.get("findings", [])) or "—"
        lines.append(
            f"| {i} | `{r['file']}` | {r['action']} | {icon} {r['verdict']} "
            f"| {findings_str} |"
        )

    lines.append("")
    content = "\n".join(lines)
    evidence_dir = Path(working_dir) / "Forge" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    out_path = evidence_dir / "audit_ledger.md"
    out_path.write_text(content, encoding="utf-8")
    return str(out_path)


# ---------------------------------------------------------------------------
# Remediation plan generator (deterministic + optional LLM fallback)
# ---------------------------------------------------------------------------

_REMEDIATION_SYSTEM_PROMPT = """\
You are ForgeGuard's Remediation Planner.  A file has failed \
deterministic audit checks.  Produce a precise, minimal fix plan \
that the Code Builder (Opus) will implement.

For EACH file in your plan, provide:
- **file** — exact relative path from repo root
- **action** — "modify", "create", or "delete"
- **description** — specific one-line summary of the change
- **current_state** — what the problematic code looks like now \
(reference line numbers, function names, patterns)
- **target_state** — exactly what it should look like after the fix
- **rationale** — WHY this fix resolves the audit finding
- **key_considerations** — constraints, things the Builder must not break

Respond with valid JSON:
{
  "analysis": "root-cause explanation of what went wrong",
  "plan": [
    {
      "file": "path/to/file",
      "action": "modify",
      "description": "exact change needed",
      "current_state": "what the code looks like now",
      "target_state": "what it should look like after",
      "rationale": "why this fix resolves the finding",
      "key_considerations": "constraints"
    }
  ],
  "risks": [],
  "verification_strategy": ["re-run inline audit — expect PASS"],
  "implementation_notes": "specific code snippet if possible"
}

IMPORTANT: Return ONLY the JSON object. No markdown fences, no prose."""


def _build_deterministic_fix(
    file_path: str, findings: list[str], original_change: dict,
) -> dict | None:
    """Try to produce a fix plan without calling the LLM.

    Returns a plan dict (same schema as ``_plan_task_with_llm`` output)
    for well-understood failure modes, or ``None`` when human/LLM
    judgement is needed.
    """
    content = original_change.get("after_snippet", "")
    plan_entries: list[dict] = []

    for finding in findings:
        if "Wildcard import" in finding:
            # Extract the wildcard line and suggest explicit imports
            plan_entries.append({
                "file": file_path,
                "action": "modify",
                "description": f"Replace wildcard import: {finding}",
                "key_considerations": (
                    "Replace 'from X import *' with explicit named imports"
                ),
            })
        elif "Scope deviation" in finding:
            # Out-of-scope file — suggest removal
            plan_entries.append({
                "file": file_path,
                "action": "delete",
                "description": f"Remove out-of-scope file: {finding}",
                "key_considerations": (
                    "File not in Sonnet's plan — remove to maintain scope"
                ),
            })
        elif "Syntax error" in finding:
            plan_entries.append({
                "file": file_path,
                "action": "modify",
                "description": f"Fix syntax: {finding}",
                "key_considerations": (
                    "Re-generate with corrected syntax"
                ),
            })
        elif "Invalid JSON" in finding:
            plan_entries.append({
                "file": file_path,
                "action": "modify",
                "description": f"Fix JSON: {finding}",
                "key_considerations": "Ensure valid JSON structure",
            })

    if not plan_entries:
        return None  # unknown failure type — needs LLM

    return {
        "analysis": f"Deterministic fix for {len(plan_entries)} finding(s) "
                     f"in {file_path}",
        "plan": plan_entries,
        "risks": [],
        "verification_strategy": ["Re-run inline audit"],
        "implementation_notes": (
            f"Original content length: {len(content)} chars. "
            f"Apply minimal targeted fix."
        ),
    }


async def _generate_remediation_plan(
    user_id: str,
    run_id: str,
    file_path: str,
    findings: list[str],
    original_change: dict,
    *,
    api_key: str,
    model: str,
    tokens: Any,
) -> dict | None:
    """Generate a fix plan for an audit failure.

    First tries a deterministic fix; falls back to Sonnet LLM only for
    cases that need human-level judgement (e.g. secret removal).
    """
    # Fast path — deterministic fix
    det_fix = _build_deterministic_fix(file_path, findings, original_change)
    if det_fix is not None:
        return det_fix

    # Slow path — Sonnet LLM
    try:
        prompt = (
            f"File: {file_path}\n"
            f"Findings:\n" +
            "\n".join(f"  - {f}" for f in findings) +
            f"\n\nOriginal content (first 2000 chars):\n"
            f"{(original_change.get('after_snippet', ''))[:2000]}\n\n"
            f"Generate a minimal fix plan."
        )
        raw = await chat(
            system=_REMEDIATION_SYSTEM_PROMPT,
            user=prompt,
            api_key=api_key,
            model=model,
        )
        text = raw.get("text", "")
        usage = raw.get("usage", {})
        p_in = usage.get("input_tokens", 0)
        p_out = usage.get("output_tokens", 0)
        tokens.add("sonnet", p_in, p_out)

        # Parse JSON
        text = _strip_codeblock(text)
        return json.loads(text)
    except Exception:
        logger.warning(
            "Remediation LLM call failed for %s — will skip auto-fix",
            file_path,
        )
        return None


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
            # Fall back to create if the file doesn't exist yet.
            # The LLM may label a new-ish file as "modify" if it assumed
            # the file already existed.
            after = change.get("after_snippet", "")
            if after:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(after, encoding="utf-8")
            else:
                raise FileNotFoundError(f"File not found: {rel_path}")
            return
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


async def _apply_all_changes(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
) -> bool:
    """Apply file changes to the cloned workspace.  Returns True if any succeeded."""
    working_dir = state.get("working_dir")
    if not working_dir or not Path(working_dir).exists():
        await _log(user_id, run_id,
                   "❌ No working directory — repository was not cloned.",
                   "error", "command")
        return False

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
    state["_applied_count"] = applied
    return applied > 0


def _detect_test_command(working_dir: str) -> tuple[str, list[str]]:
    """Detect the appropriate test command for the repo.

    Returns ``(label, [cmd, args…])``.
    Checks for common config files to pick the right runner.
    """
    wd = Path(working_dir)

    # Python — prefer pytest, fall back to unittest
    if (wd / "pytest.ini").exists() or (wd / "pyproject.toml").exists() \
            or (wd / "setup.cfg").exists() or (wd / "tests").is_dir() \
            or (wd / "test").is_dir():
        return "pytest", ["python", "-m", "pytest", "--tb=short", "-q"]

    # Node / JS — package.json with test script
    pkg_json = wd / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            test_script = pkg.get("scripts", {}).get("test", "")
            if test_script and "no test" not in test_script.lower():
                return f"npm test ({test_script})", ["npm", "test"]
        except Exception:
            pass

    # Go
    if (wd / "go.mod").exists():
        return "go test", ["go", "test", "./..."]

    # Rust
    if (wd / "Cargo.toml").exists():
        return "cargo test", ["cargo", "test"]

    # Fallback — try pytest anyway (many Python repos lack config)
    if any(wd.glob("*.py")) or any(wd.glob("**/*test*.py")):
        return "pytest (auto-detected)", ["python", "-m", "pytest", "--tb=short", "-q"]

    return "", []


async def _run_tests(
    user_id: str,
    run_id: str,
    state: dict,
) -> tuple[bool, str]:
    """Run the repo's test suite in the cloned workspace.

    Returns ``(passed: bool, output: str)``.
    """
    working_dir = state.get("working_dir", "")
    label, cmd = _detect_test_command(working_dir)

    if not cmd:
        await _log(user_id, run_id,
                   "⚠ No test runner detected — skipping tests.",
                   "warn", "command")
        return True, ""  # no tests = pass

    await _log(user_id, run_id,
               f"🧪 Running tests: {label}…", "thinking", "command")

    def _blocking_run() -> subprocess.CompletedProcess[str]:
        """Run test command in a thread — avoids event-loop subprocess
        issues on Windows (NotImplementedError with ProactorEventLoop)."""
        return subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "CI": "1"},
        )

    try:
        result = await asyncio.to_thread(_blocking_run)
        output = (result.stdout or "") + (result.stderr or "")

        # Limit log output to last 40 lines
        lines = output.strip().splitlines()
        if len(lines) > 40:
            display = ["  …(truncated)…"] + [f"  {l}" for l in lines[-40:]]
        else:
            display = [f"  {l}" for l in lines]

        for line in display:
            await _log(user_id, run_id, line, "info", "command")

        passed = result.returncode == 0
        if passed:
            await _log(user_id, run_id,
                       "✅ Tests passed (exit code 0)", "system", "command")
        else:
            await _log(user_id, run_id,
                       f"❌ Tests failed (exit code {result.returncode})",
                       "error", "command")
        return passed, output

    except subprocess.TimeoutExpired:
        await _log(user_id, run_id,
                   "⏱️ Tests timed out after 120s.", "error", "command")
        return False, "Timeout after 120s"
    except FileNotFoundError:
        await _log(user_id, run_id,
                   f"⚠ Test runner not found: '{cmd[0]}' is not installed "
                   f"or not on PATH.", "error", "command")
        return False, f"Command not found: {cmd[0]}"
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        await _log(user_id, run_id,
                   f"⚠ Test runner error: {err_msg}", "error", "command")
        return False, err_msg


# ---------------------------------------------------------------------------
# Auto-fix loop — tiered escalation when push-time tests fail
# ---------------------------------------------------------------------------

_FIX_PLANNER_PROMPT = """\
You are ForgeGuard's Fix Planner. Test failures were detected after \
applying migration changes. Your job is to diagnose the failures and \
produce a precise fix plan that the Code Builder will implement.

You will receive:
1. The pytest output showing which tests failed and why.
2. The file changes that were applied (what was changed).
3. The current contents of the failing files.
4. Previous fix attempts that did NOT resolve the problem (if any).

Rules:
- Focus ONLY on fixing the test failures. Do not refactor unrelated code.
- Identify the root cause of each failure before proposing changes.
- If a previous attempt failed, analyse WHY it failed and try a \
  different approach — do NOT repeat the same fix.
- Produce a JSON plan matching this schema:

{
  "diagnosis": "root-cause explanation of why tests fail",
  "plan": [
    {
      "file": "path/to/file",
      "action": "modify" | "create" | "delete",
      "description": "what needs to change and why",
      "target_symbols": ["function or class names to change"]
    }
  ]
}

Respond with ONLY the JSON object. No markdown fences, no prose."""

_FIX_THINKING_PLANNER_PROMPT = """\
You are ForgeGuard's Fix Planner (deep analysis mode). Previous \
standard-mode fix attempts have ALL FAILED. You MUST use extended \
thinking to deeply reason about what went wrong.

You will receive the full history of failed attempts including the \
exact error output from each try. Study EVERY past attempt carefully \
before proposing a new fix — you must NOT repeat any approach that \
has already failed.

Diagnostic steps (use your thinking capacity):
1. Read every past attempt and understand exactly what was tried.
2. Compare the error output before and after each attempt.
3. Identify the TRUE root cause (it may be different from what \
   previous attempts assumed).
4. Consider indirect causes: missing imports, wrong module paths, \
   side-effects in __init__.py, fixture issues, conftest problems.
5. Design a fix that addresses the root cause directly.

Respond with JSON matching this schema:

{
  "diagnosis": "deep root-cause analysis (be specific)",
  "failed_approach_analysis": "why each previous attempt failed",
  "plan": [
    {
      "file": "path/to/file",
      "action": "modify" | "create" | "delete",
      "description": "what needs to change and why",
      "target_symbols": ["function or class names to change"]
    }
  ]
}

Respond with ONLY the JSON object. No markdown fences, no prose."""


def _parse_test_failures(output: str) -> list[dict]:
    """Extract structured failure info from pytest output.

    Returns a list of ``{"file", "line", "test", "error_type", "message"}``
    dicts — one per detected failure.
    """
    failures: list[dict] = []
    # Match FAILED lines: FAILED tests/test_foo.py::test_bar - ErrorType: msg
    failed_re = re.compile(
        r"FAILED\s+(?P<file>[^\s:]+)::(?P<test>\S+)"
        r"(?:\s*-\s*(?P<error_type>\w+):\s*(?P<message>.+))?",
    )
    # Match  file:line: in ErrorType  (traceback headers)
    tb_re = re.compile(
        r"(?P<file>[^\s:]+):(?P<line>\d+):\s+(?:in\s+\S+\s+)?(?P<error_type>\w+Error)",
    )
    # Match  file:line: in <symbol>  (traceback without inline error)
    tb_loc_re = re.compile(
        r"(?P<file>[^\s:]+\.py):(?P<line>\d+):\s+in\s+",
    )
    # Match  E   ErrorType: message  (pytest error lines)
    e_line_re = re.compile(
        r"^E\s+(?P<error_type>\w+(?:Error|Exception)):\s*(?P<message>.+)",
    )

    # Track last seen traceback file for associating E-lines
    pending_tb_file: str | None = None
    pending_tb_line: int | None = None

    for line in output.splitlines():
        stripped = line.strip()

        m = failed_re.search(stripped)
        if m:
            failures.append({
                "file": m.group("file"),
                "line": None,
                "test": m.group("test"),
                "error_type": m.group("error_type") or "Unknown",
                "message": (m.group("message") or "").strip(),
            })
            pending_tb_file = None
            continue

        m = tb_re.search(stripped)
        if m and not any(f["file"] == m.group("file") for f in failures):
            failures.append({
                "file": m.group("file"),
                "line": int(m.group("line")),
                "test": None,
                "error_type": m.group("error_type"),
                "message": stripped,
            })
            pending_tb_file = None
            continue

        m = tb_loc_re.search(stripped)
        if m:
            pending_tb_file = m.group("file")
            pending_tb_line = int(m.group("line"))
            continue

        m = e_line_re.match(stripped)
        if m and pending_tb_file:
            if not any(f["file"] == pending_tb_file for f in failures):
                failures.append({
                    "file": pending_tb_file,
                    "line": pending_tb_line,
                    "test": None,
                    "error_type": m.group("error_type"),
                    "message": m.group("message").strip(),
                })
            pending_tb_file = None
            continue

    return failures


def _read_failing_files(
    working_dir: str,
    failures: list[dict],
    all_changes: list[dict],
) -> dict[str, str]:
    """Read the current contents of files mentioned in failures + changes.

    Returns ``{relative_path: content}`` capped at 200 KB total.
    """
    wd = Path(working_dir)
    files_to_read: set[str] = set()

    for f in failures:
        fp = f.get("file", "")
        if fp:
            files_to_read.add(fp)
    for c in all_changes:
        fp = c.get("file", "")
        if fp:
            files_to_read.add(fp)

    contents: dict[str, str] = {}
    budget = 200_000  # ~200 KB
    for rel in sorted(files_to_read):
        p = wd / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > budget:
                text = text[:budget] + "\n[TRUNCATED]"
            contents[rel] = text
            budget -= len(text)
            if budget <= 0:
                break
        except Exception:
            pass

    return contents


async def _auto_fix_loop(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    test_output: str,
) -> tuple[bool, str]:
    """Tiered auto-fix loop invoked when push-time tests fail.

    **Tier 1** — up to ``LLM_FIX_MAX_TIER1`` attempts:
      Sonnet (standard) diagnoses → Opus writes code fix → re-test.

    **Tier 2** — up to ``LLM_FIX_MAX_TIER2`` attempts:
      Sonnet with *extended thinking* diagnoses → Opus writes fix → re-test.

    Returns ``(passed, final_test_output)``.  If all attempts are
    exhausted the caller should present a final Y/N prompt.
    """
    api_key = state.get("api_key", "")
    planner_model = state.get("planner_model", settings.LLM_PLANNER_MODEL)
    builder_model = state.get("builder_model", settings.LLM_BUILDER_MODEL)
    working_dir = state.get("working_dir", "")
    max_t1 = settings.LLM_FIX_MAX_TIER1
    max_t2 = settings.LLM_FIX_MAX_TIER2
    thinking_budget = settings.LLM_THINKING_BUDGET

    attempt_history: list[dict] = []  # keeps every attempt for context
    current_output = test_output

    # ── Tier 1: Standard Sonnet → Opus ──────────────────────────────
    for attempt in range(1, max_t1 + 1):
        tier_label = f"Tier 1 ({attempt}/{max_t1})"
        await _log(user_id, run_id, "", "system", "command")
        await _log(user_id, run_id,
                   f"🔧 Auto-fix {tier_label} — Sonnet diagnosing failures…",
                   "system", "command")
        await _emit(user_id, "fix_attempt_start", {
            "run_id": run_id, "tier": 1, "attempt": attempt,
            "max_attempts": max_t1,
        })

        passed, current_output = await _single_fix_attempt(
            user_id, run_id, state, all_changes, current_output,
            attempt_history,
            api_key=api_key,
            planner_model=planner_model,
            builder_model=builder_model,
            working_dir=working_dir,
            thinking_budget=0,
            tier_label=tier_label,
        )

        await _emit(user_id, "fix_attempt_result", {
            "run_id": run_id, "tier": 1, "attempt": attempt,
            "passed": passed,
        })

        if passed:
            return True, current_output

    # ── Tier 2: Sonnet with extended thinking → Opus ────────────────
    await _log(user_id, run_id, "", "system", "command")
    await _log(user_id, run_id,
               "╔══════════════════════════════════════════════════╗",
               "system", "command")
    await _log(user_id, run_id,
               "║  🧠 Escalating to Tier 2 — deep thinking mode   ║",
               "system", "command")
    await _log(user_id, run_id,
               "╚══════════════════════════════════════════════════╝",
               "system", "command")

    for attempt in range(1, max_t2 + 1):
        tier_label = f"Tier 2 ({attempt}/{max_t2})"
        await _log(user_id, run_id, "", "system", "command")
        await _log(user_id, run_id,
                   f"🧠 Auto-fix {tier_label} — Sonnet deep-analysing failures…",
                   "system", "command")
        await _emit(user_id, "fix_attempt_start", {
            "run_id": run_id, "tier": 2, "attempt": attempt,
            "max_attempts": max_t2,
        })

        passed, current_output = await _single_fix_attempt(
            user_id, run_id, state, all_changes, current_output,
            attempt_history,
            api_key=api_key,
            planner_model=planner_model,
            builder_model=builder_model,
            working_dir=working_dir,
            thinking_budget=thinking_budget,
            tier_label=tier_label,
        )

        await _emit(user_id, "fix_attempt_result", {
            "run_id": run_id, "tier": 2, "attempt": attempt,
            "passed": passed,
        })

        if passed:
            return True, current_output

    # All tiers exhausted
    await _log(user_id, run_id, "", "system", "command")
    await _log(user_id, run_id,
               f"❌ Auto-fix exhausted ({max_t1 + max_t2} attempts failed).",
               "error", "command")
    return False, current_output


async def _single_fix_attempt(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    test_output: str,
    attempt_history: list[dict],
    *,
    api_key: str,
    planner_model: str,
    builder_model: str,
    working_dir: str,
    thinking_budget: int,
    tier_label: str,
) -> tuple[bool, str]:
    """Execute one diagnose → fix → test cycle.

    Appends the attempt to *attempt_history* (mutates in place) so
    subsequent attempts have full context.

    Returns ``(passed, test_output)``.
    """
    failures = _parse_test_failures(test_output)
    file_contents = _read_failing_files(working_dir, failures, all_changes)

    # ── Step 1: Sonnet diagnoses and plans the fix ──────────────────
    system_prompt = (
        _FIX_THINKING_PLANNER_PROMPT if thinking_budget > 0
        else _FIX_PLANNER_PROMPT
    )

    user_payload = json.dumps({
        "test_output": test_output[-8000:],  # last 8 KB of output
        "parsed_failures": failures,
        "applied_changes": [
            {"file": c.get("file"), "action": c.get("action"),
             "description": c.get("description", "")}
            for c in all_changes
        ],
        "current_file_contents": file_contents,
        "previous_attempts": attempt_history,
    }, indent=2)

    try:
        plan_result = await chat(
            api_key=api_key,
            model=planner_model,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_payload}],
            max_tokens=4096,
            provider="anthropic",
            thinking_budget=thinking_budget,
        )
        plan_text = plan_result.get("text", "") if isinstance(plan_result, dict) else str(plan_result)
        plan_text = _strip_codeblock(plan_text)

        if not plan_text.strip():
            await _log(user_id, run_id,
                       f"  ⚠ [{tier_label}] Sonnet returned empty diagnosis",
                       "error", "command")
            attempt_history.append({
                "tier_label": tier_label, "diagnosis": None,
                "fix_result": None, "test_output": test_output,
                "error": "empty diagnosis",
            })
            return False, test_output

        plan = json.loads(plan_text)
    except (json.JSONDecodeError, Exception) as exc:
        short = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _log(user_id, run_id,
                   f"  ⚠ [{tier_label}] Diagnosis failed: {short}",
                   "error", "command")
        attempt_history.append({
            "tier_label": tier_label, "diagnosis": None,
            "fix_result": None, "test_output": test_output,
            "error": short,
        })
        return False, test_output

    diagnosis = plan.get("diagnosis", "")
    if diagnosis:
        # Truncate very long diagnoses for display
        disp = diagnosis[:300] + "…" if len(diagnosis) > 300 else diagnosis
        await _log(user_id, run_id,
                   f"  🔍 [{tier_label}] Diagnosis: {disp}",
                   "info", "command")

    # ── Step 2: Opus writes the code fix ────────────────────────────
    await _log(user_id, run_id,
               f"  🔧 [{tier_label}] Opus writing fix…", "thinking", "command")

    fix_payload: dict = {
        "fix_plan": plan,
        "workspace_files": file_contents,
    }
    fix_user_msg = json.dumps(fix_payload, indent=2)

    try:
        build_result = await chat(
            api_key=api_key,
            model=builder_model,
            system_prompt=_BUILDER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": fix_user_msg}],
            max_tokens=settings.LLM_BUILDER_MAX_TOKENS,
            provider="anthropic",
        )
        build_text = build_result.get("text", "") if isinstance(build_result, dict) else str(build_result)
        build_text = _strip_codeblock(build_text)

        if not build_text.strip():
            await _log(user_id, run_id,
                       f"  ⚠ [{tier_label}] Opus returned empty fix",
                       "error", "command")
            attempt_history.append({
                "tier_label": tier_label, "diagnosis": diagnosis,
                "fix_result": None, "test_output": test_output,
                "error": "empty builder response",
            })
            return False, test_output

        fix_result = json.loads(build_text)
    except (json.JSONDecodeError, Exception) as exc:
        short = f"{type(exc).__name__}: {str(exc)[:200]}"
        await _log(user_id, run_id,
                   f"  ⚠ [{tier_label}] Fix build failed: {short}",
                   "error", "command")
        attempt_history.append({
            "tier_label": tier_label, "diagnosis": diagnosis,
            "fix_result": None, "test_output": test_output,
            "error": short,
        })
        return False, test_output

    # ── Step 3: Apply the fix changes ───────────────────────────────
    fix_changes = fix_result.get("changes", [])
    if not fix_changes:
        await _log(user_id, run_id,
                   f"  ⚠ [{tier_label}] Opus produced no changes",
                   "warn", "command")
        attempt_history.append({
            "tier_label": tier_label, "diagnosis": diagnosis,
            "fix_result": fix_result, "test_output": test_output,
            "error": "no changes produced",
        })
        return False, test_output

    applied = 0
    for c in fix_changes:
        try:
            _apply_file_change(working_dir, c)
            applied += 1
            await _log(user_id, run_id,
                       f"  ✅ [{tier_label}] {c.get('action', 'modify')}: "
                       f"{c.get('file', '?')}", "info", "command")
        except Exception as exc:
            await _log(user_id, run_id,
                       f"  ⚠ [{tier_label}] {c.get('file', '?')}: {exc}",
                       "warn", "command")

    if applied == 0:
        await _log(user_id, run_id,
                   f"  ⚠ [{tier_label}] No changes applied successfully",
                   "warn", "command")
        attempt_history.append({
            "tier_label": tier_label, "diagnosis": diagnosis,
            "fix_result": fix_result, "test_output": test_output,
            "error": "no changes applied",
        })
        return False, test_output

    await _log(user_id, run_id,
               f"  📝 [{tier_label}] Applied {applied}/{len(fix_changes)} "
               f"fix change(s)", "system", "command")

    # ── Step 4: Re-run tests ───────────────────────────────────────
    await _log(user_id, run_id,
               f"  🧪 [{tier_label}] Re-running tests…", "thinking", "command")
    passed, new_output = await _run_tests(user_id, run_id, state)

    attempt_history.append({
        "tier_label": tier_label,
        "diagnosis": diagnosis,
        "fix_changes": [
            {"file": c.get("file"), "action": c.get("action")}
            for c in fix_changes
        ],
        "test_passed": passed,
        "test_output": new_output[-4000:],  # keep last 4 KB for context
    })

    if passed:
        await _log(user_id, run_id,
                   f"  ✅ [{tier_label}] Tests pass after fix!",
                   "system", "command")
    else:
        await _log(user_id, run_id,
                   f"  ❌ [{tier_label}] Tests still failing",
                   "error", "command")

    return passed, new_output


async def _commit_and_push(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    task_results: list[dict],
) -> dict:
    """Git add → commit → push.  Assumes changes are already applied."""
    working_dir = state.get("working_dir", "")
    access_token = state.get("access_token", "")
    repo_name = state.get("repo_name", "unknown")
    applied = state.get("_applied_count", len(all_changes))

    if not access_token:
        await _log(user_id, run_id,
                   "❌ No GitHub access token — connect GitHub in Settings.",
                   "error", "command")
        return {"ok": False, "message": "No GitHub access token."}

    # Build commit message
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

    try:
        await _log(user_id, run_id, "📋 Staging changes…", "system", "command")
        await git_client.add_all(working_dir)

        await _log(user_id, run_id, "💾 Committing…", "system", "command")
        sha = await git_client.commit(working_dir, commit_msg)
        if sha:
            await _log(user_id, run_id,
                       f"  Commit {sha[:8]}", "info", "command")

        try:
            branch = (await git_client._run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"], cwd=working_dir,
            )).strip() or "main"
        except Exception:
            branch = state.get("branch", "main")

        remote_url = f"https://github.com/{repo_name}.git"
        await git_client.set_remote(working_dir, remote_url)

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


async def _push_changes(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    task_results: list[dict],
) -> dict:
    """Apply file changes, commit, and push (no tests)."""
    ok = await _apply_all_changes(user_id, run_id, state, all_changes)
    if not ok:
        return {"ok": False, "message": "No changes could be applied."}
    return await _run_pre_push_audit(
        user_id, run_id, state, all_changes, task_results)


async def _run_pre_push_audit(
    user_id: str,
    run_id: str,
    state: dict,
    all_changes: list[dict],
    task_results: list[dict],
) -> dict:
    """Write evidence files, check audit results, gate the push.

    Flow: diff log -> audit trail -> if any FAIL -> Y/N prompt -> push or cancel.
    If all PASS -> commit and push immediately.
    """
    working_dir = state.get("working_dir", "")
    repo_name = state.get("repo_name", "unknown")
    audit_results = state.get("audit_results", [])

    # 1. Write diff log
    await _log(user_id, run_id, "", "system", "command")
    await _log(user_id, run_id,
               "📄 Writing diff log…", "system", "command")
    try:
        diff_log_path = _write_diff_log(working_dir, all_changes, repo_name)
        await _log(user_id, run_id,
                   f"  ✅ diff_log.md written ({len(all_changes)} file(s))",
                   "info", "command")
    except Exception as exc:
        await _log(user_id, run_id,
                   f"  ⚠ Failed to write diff log: {exc}",
                   "warn", "command")

    # 2. Write audit trail
    if audit_results:
        await _log(user_id, run_id,
                   "📋 Writing audit trail…", "system", "command")
        try:
            _write_audit_trail(working_dir, audit_results, repo_name)
            passed = sum(1 for r in audit_results if r["verdict"] == "PASS")
            failed = sum(1 for r in audit_results if r["verdict"] != "PASS")
            await _log(user_id, run_id,
                       f"  ✅ Audit trail written — {passed} passed, {failed} failed",
                       "info", "command")
        except Exception as exc:
            await _log(user_id, run_id,
                       f"  ⚠ Failed to write audit trail: {exc}",
                       "warn", "command")

    # 3. Check for audit failures
    failures = [r for r in audit_results if r["verdict"] != "PASS"]
    if failures:
        # Show failure summary and offer Y/N prompt
        await _log(user_id, run_id, "", "system", "command")
        await _log(user_id, run_id,
                   "╔══════════════════════════════════════════════════╗",
                   "system", "command")
        await _log(user_id, run_id,
                   f"║  ❌ Audit: {len(failures)} file(s) failed — push anyway? ║",
                   "system", "command")
        await _log(user_id, run_id,
                   "║                                                  ║",
                   "system", "command")
        for f in failures[:5]:
            name = f["file"][:40]
            reason = "; ".join(f.get("findings", []))[:50]
            await _log(user_id, run_id,
                       f"║  • {name}: {reason}",
                       "warn", "command")
        if len(failures) > 5:
            await _log(user_id, run_id,
                       f"║  … and {len(failures) - 5} more",
                       "warn", "command")
        await _log(user_id, run_id,
                   "║                                                  ║",
                   "system", "command")
        await _log(user_id, run_id,
                   "║  [Y] Push anyway — ignore audit failures         ║",
                   "system", "command")
        await _log(user_id, run_id,
                   "║  [N] Cancel push — fix issues first              ║",
                   "system", "command")
        await _log(user_id, run_id,
                   "╚══════════════════════════════════════════════════╝",
                   "system", "command")

        state["_push_changes"] = all_changes
        state["_push_task_results"] = task_results
        state["pending_prompt"] = "push_audit_confirm"
        await _emit(user_id, "upgrade_prompt", {
            "run_id": run_id, "prompt_id": "push_audit_confirm"})
        return {"ok": True, "message": "Audit failed. Y/N?"}

    # 4. All passed — commit and push
    await _log(user_id, run_id,
               "✅ All files passed inline audit", "system", "command")
    return await _commit_and_push(
        user_id, run_id, state, all_changes, task_results)


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

_PLANNER_SYSTEM_PROMPT = """\
You are ForgeGuard's Upgrade Planner (Sonnet). You analyse migration tasks \
and produce highly structured, detailed implementation plans for the Code \
Builder (Opus).

Your plans are the SINGLE SOURCE OF TRUTH that the Builder works from. \
The Builder will ONLY touch files you list — nothing more, nothing less. \
Be thorough: any file you forget to list will NOT be changed.

═══ PLAN QUALITY STANDARD ═══

For EACH file in your plan, provide:
- **file** — exact relative path from repo root
- **action** — "modify", "create", or "delete"
- **description** — a specific, one-line summary of what changes
- **current_state** — what the relevant code looks like now \
(reference line numbers, function names, class names)
- **target_state** — exactly what it should look like after the change \
(describe the concrete transformation, not vague goals)
- **rationale** — WHY this change is needed (link to the migration goal)
- **key_considerations** — gotchas, edge cases, ordering dependencies, \
things the Builder must not break

For "create" actions, replace "current_state" with "template" describing \
the expected file structure (imports, classes, functions, exports).

═══ OUTPUT FORMAT ═══

Respond with valid JSON matching this schema:
{
  "analysis": "2-3 sentence summary: what this migration involves, why \
it matters, and the overall approach",
  "plan": [
    {
      "file": "path/to/file.py",
      "action": "modify",
      "description": "Add connection timeout to pool creation",
      "current_state": "Line ~14: asyncpg.create_pool(dsn=..., min_size=2, \
max_size=10) — no timeout configured",
      "target_state": "Wrap in asyncio.wait_for(..., timeout=15) and add \
command_timeout=60 parameter",
      "rationale": "Pool creation hangs forever if DB is unreachable, \
causing the server to appear frozen on startup",
      "key_considerations": "Must not break existing pool.close() in \
lifespan shutdown; timeout error should be allowed to propagate"
    }
  ],
  "risks": ["specific, actionable risk descriptions — not generic warnings"],
  "verification_strategy": ["concrete steps to verify success, e.g. \
'Run pytest tests/test_health.py — should pass with DB connected'"],
  "implementation_notes": "important sequencing notes, cross-file \
dependencies, or things the Builder needs to know about the codebase"
}

═══ RULES ═══
- List EVERY file that must change. Omissions mean the Builder cannot \
touch them.
- When a "file_tree" key is present in the input, use it to reference \
REAL paths, function names, and approximate line numbers in your \
current_state descriptions. Do NOT guess paths that don't exist.
- Reference actual function names, class names, and approximate line \
numbers when describing current_state.
- If the migration requires config/env changes, list those files too.
- If tests need updating, list the test files explicitly.
- Keep risks actionable — "tests may break" is useless; \
"test_health.py::test_db_connected will need updating because it \
mocks get_pool()" is useful.

IMPORTANT: Return ONLY the JSON object. Do NOT wrap it in markdown code fences."""

_BUILDER_SYSTEM_PROMPT = """\
You are ForgeGuard's Code Builder (Opus). You take a migration plan from \
the Planner and produce concrete, production-quality code changes.

═══ STRICT SCOPE RULES ═══
You MUST only touch files that appear in the Planner's file list \
(the "plan" array in "planner_analysis").  Any change to a file not \
explicitly listed by the Planner will be automatically rejected.

Do NOT:
- Add helper files, utils, configs, or "nice-to-have" extras.
- Rename or restructure files beyond what the plan specifies.
- Create test files, documentation, or supporting modules unless the \
plan explicitly lists them.

If the plan seems incomplete or you believe an additional file MUST be \
changed for the migration to work, do NOT silently add it.  Instead, \
record your concern in the "objections" array (see schema below) so \
the human operator can review and update the plan.

═══ FILE CONTENT RULES ═══
You are given the ACTUAL file contents from the cloned repository under \
the "workspace_files" key.  Use these real contents to craft precise \
before_snippet / after_snippet values.

Rules for before_snippet (modify action):
- MUST be an exact, contiguous substring copied from the provided file.
- Include enough surrounding lines (3-5) so the match is unique.
- Do NOT paraphrase or abbreviate the original code.

Rules for after_snippet:
- Must be the replacement text that will substitute before_snippet.
- Preserve indentation and style from the original file.

For "create" actions, omit before_snippet and put the full file in after_snippet.

═══ RESPONSE SCHEMA ═══
Respond with valid JSON matching this schema:
{
  "thinking": ["step-by-step reasoning about implementing each change"],
  "changes": [
    {
      "file": "path/to/file",
      "action": "modify" | "create" | "delete",
      "description": "what this change does",
      "before_snippet": "exact text from the file (for modify)",
      "after_snippet": "replacement text"
    }
  ],
  "objections": [
    "(optional) Any concerns about the plan — missing files, \
conflicting requirements, database violations, etc.  The human \
operator will see these and can adjust the plan."
  ],
  "warnings": ["any risks or things to watch out for"],
  "verification_steps": ["how to verify this migration worked"],
  "status": "proposed"
}

Write production-quality code. Be thorough and precise with before/after snippets.

IMPORTANT: Return ONLY the JSON object. Do NOT wrap it in markdown code fences."""

_CODEBLOCK_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_codeblock(text: str) -> str:
    """Remove optional ```json ... ``` wrapper, surrounding prose, and whitespace.

    LLMs sometimes emit prose preamble before the JSON object.  After
    stripping markdown fences we fall back to extracting the outermost
    ``{…}`` (or ``[…]``) so a response like::

        I'll produce the changes now.
        {"changes": [...]}

    still parses correctly.
    """
    text = text.strip()
    m = _CODEBLOCK_RE.match(text)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # If the text already looks like JSON, return as-is
    if text.startswith("{") or text.startswith("["):
        return text

    # Extract outermost JSON object/array from surrounding prose.
    # Pick whichever delimiter ({…} or […]) appears first.
    best_start = len(text)
    best: str | None = None
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1 or start >= best_start:
            continue
        end = text.rfind(close_ch)
        if end > start:
            best_start = start
            best = text[start:end + 1]

    return best if best is not None else text


# ---------------------------------------------------------------------------
# Narrator system (Haiku — lightweight plain-English commentary)
# ---------------------------------------------------------------------------

_NARRATOR_SYSTEM_PROMPT = """\
You are a concise narrator for ForgeGuard's Upgrade IDE. \
Summarise what just happened in plain English for a non-technical audience.

Rules:
- ONE short sentence only. Two at absolute most.
- Be direct and informative, not chatty.
- No analogies, no metaphors, no emojis, no cheerleading.
- Never start with "Great progress" or similar filler.
- Say WHAT changed and WHY it matters, nothing else."""


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

    Always fires when narrator is enabled — Haiku is cheap enough to
    run from the start so users see plain-English commentary immediately.
    """
    try:
        result = await chat(
            api_key=narrator_key,
            model=narrator_model,
            system_prompt=_NARRATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": event_summary}],
            max_tokens=80,
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
        logger.warning("Narration failed for run %s", run_id, exc_info=True)
        # Let the user know narration failed so they're not stuck on "Loading"
        state = _active_upgrades.get(run_id)
        if state:
            await _emit(user_id, "upgrade_narration", {
                "run_id": run_id,
                "text": "(Narration unavailable — Haiku call failed. Check your second API key in Settings.)",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })


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
        "narrator_watching": True,
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

    # Build workers — Sonnet plans, Opus codes (both use Key 1)
    sonnet_worker = _WorkerSlot(
        label="sonnet",
        api_key=key1,
        model=settings.LLM_PLANNER_MODEL,
        display="Sonnet",
    )
    opus_worker = _WorkerSlot(
        label="opus",
        api_key=key1,
        model=settings.LLM_BUILDER_MODEL,
        display="Opus",
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
        "narrator_watching": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "working_dir": prepared_dir,
        "access_token": prepared_token,
        "branch": prepared_branch,
        # private control handles (not serialised)
        "_pause_event": pause_event,
        "_stop_flag": stop_flag,
        # retry context — stashed so /retry can re-run failed tasks
        "_sonnet_worker": sonnet_worker,
        "_opus_worker": opus_worker,
        "_stack_profile": results.get("stack_profile", {}),
        "_narrator_key": narrator_key,
        "_narrator_model": narrator_model,
    }

    asyncio.create_task(
        _run_upgrade(uid, rid, run, results, plan, tasks,
                     sonnet_worker, opus_worker,
                     narrator_key=narrator_key, narrator_model=narrator_model)
    )

    return {
        "run_id": rid,
        "status": "running",
        "total_tasks": len(tasks),
        "repo_name": run.get("repo_name", ""),
        "narrator_enabled": narrator_enabled,
        "workers": ["sonnet", "opus"],
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
    sonnet_worker: _WorkerSlot,
    opus_worker: _WorkerSlot,
    narrator_key: str = "",
    narrator_model: str = "",
) -> None:
    """Background coroutine — pipelined dual-worker execution.

    Sonnet plans task N+1 **in parallel** with Opus coding task N.
    Haiku narrates (non-blocking) after each task completes.
    """
    state = _active_upgrades[run_id]
    repo_name = run.get("repo_name", "unknown")
    stack_profile = results.get("stack_profile", {})
    tokens = _TokenAccumulator()
    state["_token_tracker"] = tokens
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
                "worker": "sonnet",  # initial assignment — Sonnet plans first
            }
            for i, t in enumerate(tasks)
        ]

        await _emit(user_id, "upgrade_started", {
            "run_id": run_id,
            "repo_name": repo_name,
            "total_tasks": len(tasks),
            "narrator_enabled": narrator_enabled,
            "workers": [
                {"label": sonnet_worker.label, "model": sonnet_worker.model,
                 "display": sonnet_worker.display},
                {"label": opus_worker.label, "model": opus_worker.model,
                 "display": opus_worker.display},
            ],
            "tasks": task_descriptors,
        })

        await _log(user_id, run_id,
                    f"🚀 Forge IDE — Starting upgrade for {repo_name}", "system")
        await _log(user_id, run_id,
                    "🧠 Sonnet planning · Opus coding — running in parallel",
                    "system")
        if narrator_enabled:
            await _log(user_id, run_id,
                        "🎙️ Haiku narrator active — plain-English commentary enabled",
                        "system")
        await _log(user_id, run_id,
                    f"📋 {len(tasks)} migration task(s) queued", "system")

        executive = plan.get("executive_brief") or {}
        if executive.get("headline"):
            await _log(user_id, run_id,
                        f"📊 Assessment: {executive['headline']}", "info")
        if executive.get("health_grade"):
            await _log(user_id, run_id,
                        f"🏥 Health grade: {executive['health_grade']}", "info")

        await asyncio.sleep(0.5)

        # Fire opening narration (non-blocking) — rich context
        if narrator_enabled:
            brief = executive.get("headline", "")
            grade = executive.get("health_grade", "")
            task_overview = "; ".join(
                f"{t.get('from_state', '?')} → {t.get('to_state', '?')} "
                f"({t.get('priority', 'med')} priority)"
                for t in tasks[:6]
            )
            asyncio.create_task(_narrate(
                user_id, run_id,
                f"Starting upgrade for repository '{repo_name}'. "
                f"Health grade: {grade}. {brief} "
                f"{len(tasks)} tasks planned: {task_overview}.",
                narrator_key=narrator_key, narrator_model=narrator_model,
                tokens=tokens,
            ))

        # ── Helper: emit results for a completed task ────────────

        async def _emit_task_results(
            task_index: int,
            task: dict,
            plan_result: dict | None,
            code_result: dict | None,
            plan_usage: dict,
            code_usage: dict,
        ) -> None:
            """Log + emit events for one completed task."""
            task_id = task.get("id", f"TASK-{task_index}")
            task_name = (
                f"{task.get('from_state', '?')} → {task.get('to_state', '?')}"
            )

            # (Sonnet's analysis was already logged in _sonnet_planner)

            # ─ Log Opus's code output ─
            if code_result:
                thinking = code_result.get("thinking", [])
                for thought in thinking:
                    await _log(user_id, run_id,
                                f"💭 [Opus] {thought}", "thinking")
                    await asyncio.sleep(0.08)

                changes = code_result.get("changes", [])
                # Build planned file list from Sonnet's plan for scope check
                _planned = None
                if plan_result:
                    _plan_entries = plan_result.get("plan", [])
                    if _plan_entries:
                        _planned = [p.get("file", "") for p in _plan_entries]

                if changes:
                    await _log(user_id, run_id,
                                f"📝 [Opus] {len(changes)} file change(s) proposed:",
                                "system")
                    for change in list(changes):  # copy — we may .remove() inside
                        icon = {"modify": "✏️", "create": "➕",
                                "delete": "🗑️"}.get(
                            change.get("action", "modify"), "📄")
                        await _log(
                            user_id, run_id,
                            f"  {icon} {change.get('file', '?')} — "
                            f"{change.get('description', '')}",
                            "info",
                        )
                        await _emit(user_id, "upgrade_file_diff", {
                            "run_id": run_id,
                            "task_id": task_id,
                            "worker": "opus",
                            **change,
                        })
                        await asyncio.sleep(0.05)

                        # ── Inline audit (deterministic, zero LLM cost) ──
                        await _emit(user_id, "file_audit_start", {
                            "run_id": run_id,
                            "task_id": task_id,
                            "file": change.get("file", ""),
                        })
                        verdict, findings = _audit_file_change(
                            change, planned_files=_planned)
                        audit_entry = {
                            "file": change.get("file", ""),
                            "action": change.get("action", "modify"),
                            "verdict": verdict,
                            "findings": findings,
                            "task_id": task_id,
                            "original_change": change,
                        }
                        state.setdefault("audit_results", []).append(
                            audit_entry)
                        if verdict == "REJECT":
                            # Hard block — drop this change entirely
                            await _log(user_id, run_id,
                                        f"    🚫 [Opus] REJECTED (scope): "
                                        f"{change.get('file', '?')} "
                                        f"— not in planner's file list, dropped",
                                        "warn")
                            await _emit(user_id, "file_audit_reject", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                                "findings": findings,
                            })
                            changes.remove(change)
                            continue
                        elif verdict == "PASS":
                            await _log(user_id, run_id,
                                        f"    ✅ [Opus] Audit PASS: {change.get('file', '?')}",
                                        "info")
                            await _emit(user_id, "file_audit_pass", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                            })
                        else:
                            for f in findings:
                                await _log(user_id, run_id,
                                            f"    ❌ [Opus] {f}", "warn")
                            await _emit(user_id, "file_audit_fail", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                                "findings": findings,
                            })

                # Surface builder objections to the user
                for objection in code_result.get("objections", []):
                    await _log(user_id, run_id,
                               f"💬 [Opus objection] {objection}", "warn")
                if code_result.get("objections"):
                    await _emit(user_id, "upgrade_objections", {
                        "run_id": run_id,
                        "task_id": task_id,
                        "objections": code_result.get("objections", []),
                    })

                for warn in code_result.get("warnings", []):
                    await _log(user_id, run_id, f"⚠️ [Opus] {warn}", "warn")

                verifications = code_result.get("verification_steps", [])
                if verifications:
                    await _log(user_id, run_id, "✅ [Opus] Verification:", "info")
                    for v in verifications:
                        await _log(user_id, run_id, f"  → [Opus] {v}", "info")

                n_changes = len(changes)
                result_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "proposed",
                    "changes_count": n_changes,
                    "worker": "opus",
                    "tokens": {
                        "sonnet": plan_usage,
                        "opus": code_usage,
                    },
                    "llm_result": code_result,
                    "_plan": plan_result,
                }
                await _log(user_id, run_id,
                            f"✅ Task {task_id} complete — "
                            f"{n_changes} changes proposed", "system")
            else:
                result_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "skipped",
                    "changes_count": 0,
                    "worker": "opus",
                    "tokens": {
                        "sonnet": plan_usage,
                        "opus": code_usage,
                    },
                    # Stash originals so /retry can re-run this task
                    "_retry_task": task,
                    "_retry_plan": plan_result,
                }
                await _log(user_id, run_id,
                            f"⏭️ Task {task_id} skipped (no result)",
                            "warn")

            # State update
            snap = tokens.snapshot()
            state["task_results"].append(result_entry)
            state["completed_tasks"] += 1
            state["tokens"] = snap

            await _emit(user_id, "upgrade_task_complete", {
                "run_id": run_id,
                "task_id": task_id,
                "task_index": task_index,
                "status": result_entry["status"],
                "changes_count": result_entry["changes_count"],
                "worker": "opus",
                "token_delta": {
                    "sonnet": plan_usage,
                    "opus": code_usage,
                },
                "token_cumulative": snap,
            })

        # ── Pool-based dual-worker execution ─────────────────────
        # Sonnet plans ALL tasks as fast as possible → plan pool.
        # Opus pulls plans from the pool when ready → codes them.
        # After planning, Sonnet generates remediation plans for
        # audit failures → remediation pool.  Opus drains these
        # between tasks.
        #
        # Time 0: [Sonnet] Plan Task 0
        # Time 1: [Sonnet] Plan Task 1  (pool←)  ‖  [Opus] Code Task 0  (←pool)
        # Time 2: [Sonnet] Plan Task 2  (pool←)  ‖  [Opus] still on 0
        # Time 3: [Sonnet] Plan Task 3  (pool←)  ‖  [Opus] Code Task 1  (←pool)
        # …
        # Time K: [Sonnet] Remediate F0           ‖  [Opus] Code Task N

        if tasks:
            plan_pool: asyncio.Queue[_PlanPoolItem | None] = asyncio.Queue()
            remediation_pool: asyncio.PriorityQueue = asyncio.PriorityQueue()
            sonnet_done = asyncio.Event()
            _rem_seq = 0

            state["_plan_pool"] = plan_pool
            state["_remediation_pool"] = remediation_pool

            # ── Sonnet planner coroutine ─────────────────────────
            async def _sonnet_planner() -> None:
                nonlocal _rem_seq

                for i, task in enumerate(tasks):
                    if state["_stop_flag"].is_set():
                        break
                    if not state["_pause_event"].is_set():
                        await state["_pause_event"].wait()
                        if state["_stop_flag"].is_set():
                            break

                    task_id = task.get("id", f"TASK-{i}")
                    task_name = (
                        f"{task.get('from_state', '?')} → "
                        f"{task.get('to_state', '?')}"
                    )

                    await _emit(user_id, "plan_task_start", {
                        "run_id": run_id,
                        "task_id": task_id,
                        "task_index": i,
                        "task_name": task_name,
                        "worker": "sonnet",
                    })
                    await _log(user_id, run_id,
                                f"🧠 [Sonnet] Planning task "
                                f"{i + 1}/{len(tasks)}: "
                                f"{task_name}…", "thinking")

                    plan_result, plan_usage = await _plan_task_with_llm(
                        user_id, run_id, repo_name, stack_profile,
                        task,
                        api_key=sonnet_worker.api_key,
                        model=sonnet_worker.model,
                        working_dir=state.get("working_dir", ""),
                    )
                    p_in = plan_usage.get("input_tokens", 0)
                    p_out = plan_usage.get("output_tokens", 0)
                    tokens.add("sonnet", p_in, p_out)
                    await _emit(user_id, "upgrade_token_tick", {
                        "run_id": run_id, **tokens.snapshot()})

                    # Log plan details
                    if plan_result:
                        analysis = plan_result.get("analysis", "")
                        if analysis:
                            await _log(user_id, run_id,
                                        f"🧠 [Sonnet] {analysis}",
                                        "thinking")
                        plan_files = plan_result.get("plan", [])
                        if plan_files:
                            await _log(
                                user_id, run_id,
                                f"📋 [Sonnet] Identified "
                                f"{len(plan_files)} file(s):",
                                "info",
                            )
                            for pf in plan_files:
                                await _log(
                                    user_id, run_id,
                                    f"  📄 {pf.get('file', '?')} — "
                                    f"{pf.get('description', '')}",
                                    "info",
                                )
                        for risk in plan_result.get("risks", []):
                            await _log(user_id, run_id,
                                        f"  ⚠ [Sonnet] {risk}", "warn")

                    # Push to pool — Opus will pick it up when ready.
                    # If planning failed (plan_result is None), still
                    # enqueue so Opus can emit the skip/failure status
                    # and keep task numbering consistent.
                    if plan_result is None:
                        await _log(user_id, run_id,
                                    f"⚠ [Sonnet] Plan for task "
                                    f"{i + 1} failed — Opus will "
                                    f"skip it", "warn")
                    pool_item = _PlanPoolItem(
                        i, task, plan_result, plan_usage)
                    await plan_pool.put(pool_item)
                    pool_depth = plan_pool.qsize()
                    await _log(user_id, run_id,
                                f"📥 [Sonnet] Plan for task {i + 1} "
                                f"queued (pool depth: {pool_depth})",
                                "info")
                    await _emit(user_id, "plan_pool_update", {
                        "run_id": run_id,
                        "action": "push",
                        "task_index": i,
                        "pool_depth": pool_depth,
                    })

                    # Fire narration (non-blocking)
                    if narrator_enabled and plan_result:
                        _pf = plan_result.get("plan", [])
                        _fl = ", ".join(
                            p.get("file", "?") for p in _pf[:5])
                        _an = plan_result.get("analysis", "")
                        asyncio.create_task(_narrate(
                            user_id, run_id,
                            f"Sonnet planned task {i + 1}: "
                            f"'{task_name}'. {_an} "
                            f"Files: {_fl}. "
                            f"Plan queued (pool: {pool_depth}).",
                            narrator_key=narrator_key,
                            narrator_model=narrator_model,
                            tokens=tokens,
                        ))

                # Sentinel — tells Opus no more plans are coming
                await plan_pool.put(None)

                # ── Remediation mode ─────────────────────────────
                await _log(user_id, run_id,
                            "🧠 [Sonnet] All tasks planned — switching "
                            "to audit remediation mode", "system")

                idle_cycles = 0
                while not state["_stop_flag"].is_set():
                    failures = [
                        r for r in state.get("audit_results", [])
                        if r.get("verdict") == "FAIL"
                        and not r.get("_remediation_queued")
                    ]
                    if not failures:
                        idle_cycles += 1
                        if idle_cycles > 5:
                            break
                        await asyncio.sleep(2.0)
                        continue
                    idle_cycles = 0

                    for failure in failures:
                        if state["_stop_flag"].is_set():
                            break
                        failure["_remediation_queued"] = True
                        fp = failure.get("file", "?")
                        fi = failure.get("findings", [])
                        tid = failure.get("task_id", "")

                        await _log(user_id, run_id,
                                    f"🔧 [Sonnet] Generating fix for "
                                    f"{fp}…", "thinking")

                        fix_plan = await _generate_remediation_plan(
                            user_id, run_id, fp, fi,
                            failure.get("original_change", {}),
                            api_key=sonnet_worker.api_key,
                            model=sonnet_worker.model,
                            tokens=tokens,
                        )

                        _rem_seq += 1
                        rem_item = _RemediationItem(
                            file=fp, findings=fi,
                            original_change=failure.get(
                                "original_change", {}),
                            task_id=tid, fix_plan=fix_plan,
                            _seq=_rem_seq,
                        )
                        await remediation_pool.put(rem_item)
                        await _emit(user_id, "remediation_queued", {
                            "run_id": run_id,
                            "file": fp,
                            "findings": fi,
                            "has_fix": fix_plan is not None,
                            "pool_depth": remediation_pool.qsize(),
                        })
                        await _log(user_id, run_id,
                                    f"📥 [Sonnet] Remediation for "
                                    f"{fp} queued "
                                    f"(pool: {remediation_pool.qsize()})",
                                    "info")

                sonnet_done.set()

            # ── Opus builder coroutine ───────────────────────────
            async def _opus_builder() -> None:
                while True:
                    if state["_stop_flag"].is_set():
                        break
                    if not state["_pause_event"].is_set():
                        await _log(user_id, run_id,
                                    "⏸️  Paused — waiting for /resume…",
                                    "system")
                        await state["_pause_event"].wait()
                        if state["_stop_flag"].is_set():
                            break

                    # Pull next plan from pool (blocks until ready)
                    pool_item = await plan_pool.get()
                    if pool_item is None:
                        # Sentinel — no more plan tasks.
                        # Wait for Sonnet's remediations, then drain.
                        await sonnet_done.wait()
                        await _drain_remediation_pool()
                        break

                    task_index = pool_item.task_index
                    task = pool_item.task
                    current_plan = pool_item.plan_result
                    plan_usage = pool_item.plan_usage
                    task_id = task.get("id", f"TASK-{task_index}")
                    task_name = (
                        f"{task.get('from_state', '?')} → "
                        f"{task.get('to_state', '?')}"
                    )
                    state["current_task"] = task_id

                    # ── Task section header (shown when Opus
                    #    starts, not when Sonnet plans) ──
                    await _log(user_id, run_id, "", "system")
                    await _log(user_id, run_id,
                                f"━━━ Task {task_index + 1}"
                                f"/{len(tasks)}: "
                                f"{task_name} ━━━", "system")
                    await _emit(user_id, "upgrade_task_start", {
                        "run_id": run_id,
                        "task_id": task_id,
                        "task_index": task_index,
                        "task_name": task_name,
                        "priority": task.get(
                            "priority", "medium"),
                        "category": task.get("category", ""),
                        "steps": task.get("steps", []),
                        "worker": "opus",
                    })

                    pool_depth = plan_pool.qsize()
                    await _log(user_id, run_id,
                                f"⚡ [Opus] Writing code for task "
                                f"{task_index + 1}… "
                                f"(pool depth: {pool_depth})",
                                "thinking")
                    await _emit(user_id, "plan_pool_update", {
                        "run_id": run_id,
                        "action": "pull",
                        "task_index": task_index,
                        "pool_depth": pool_depth,
                    })

                    # Skip tasks whose plan failed
                    if current_plan is None:
                        await _log(
                            user_id, run_id,
                            f"⏭️ [Opus] Skipping task "
                            f"{task_index + 1} — Sonnet's "
                            f"plan failed", "warn")
                        state["completed_tasks"] = (
                            state.get("completed_tasks", 0) + 1)
                        state["task_results"].append({
                            "task_id": task_id,
                            "task_name": task_name,
                            "status": "skipped",
                            "reason": "planning_failed",
                            "changes_count": 0,
                        })
                        await _emit(
                            user_id, "upgrade_task_complete", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "status": "skipped",
                            })
                        continue

                    # Show what Opus will work on
                    _pf = current_plan.get("plan", [])
                    if _pf:
                        _fn = [p.get("file", "?")
                               for p in _pf[:6]]
                        _ex = (f" +{len(_pf) - 6} more"
                               if len(_pf) > 6 else "")
                        await _log(
                            user_id, run_id,
                            f"  📖 [Opus] Reading {len(_pf)} "
                            f"file(s): "
                            f"{', '.join(_fn)}{_ex}",
                            "thinking",
                        )

                    # Code the task
                    code_result, code_usage = (
                        await _build_task_with_llm(
                            user_id, run_id, repo_name,
                            stack_profile, task, current_plan,
                            api_key=opus_worker.api_key,
                            model=opus_worker.model,
                            working_dir=state.get("working_dir", ""),
                        )
                    )
                    c_in = code_usage.get("input_tokens", 0)
                    c_out = code_usage.get("output_tokens", 0)
                    tokens.add("opus", c_in, c_out)

                    await _emit(user_id, "upgrade_token_tick", {
                        "run_id": run_id, **tokens.snapshot()})

                    # Emit results + inline audit
                    await _emit_task_results(
                        task_index, task, current_plan,
                        code_result, plan_usage, code_usage,
                    )

                    # ── Per-task verify-fix loop ─────────────────
                    # If inline audit found FAIL verdicts for files
                    # in this task, give Opus ONE re-attempt to fix
                    # them before moving on.
                    task_failures = [
                        r for r in state.get("audit_results", [])
                        if r.get("task_id") == task_id
                        and r.get("verdict") == "FAIL"
                        and not r.get("_retried")
                    ]
                    if task_failures and code_result:
                        await _log(
                            user_id, run_id,
                            f"🔄 [Opus] {len(task_failures)} file(s) "
                            f"failed audit — attempting inline fix…",
                            "thinking",
                        )
                        for fail_entry in task_failures:
                            fail_entry["_retried"] = True

                        # Build a mini-remediation plan inline
                        fail_files = [
                            f.get("file", "?")
                            for f in task_failures
                        ]
                        fail_findings = {
                            f.get("file", "?"): f.get("findings", [])
                            for f in task_failures
                        }
                        retry_plan = {
                            "analysis": (
                                f"Inline audit failures in "
                                f"{', '.join(fail_files)}. "
                                f"Fix the issues and keep all other "
                                f"code unchanged."
                            ),
                            "plan": [
                                {
                                    "file": ff.get("file", ""),
                                    "action": ff.get("action", "modify"),
                                    "description": (
                                        f"Fix audit findings: "
                                        f"{'; '.join(ff.get('findings', []))}"
                                    ),
                                    "key_considerations": (
                                        "Only fix the flagged issues. "
                                        "Do NOT change anything else."
                                    ),
                                }
                                for ff in task_failures
                            ],
                            "risks": [],
                            "verification_strategy": [
                                "Re-run inline audit — all PASS"
                            ],
                            "implementation_notes": (
                                f"Findings: {json.dumps(fail_findings)}"
                            ),
                        }
                        retry_result, retry_usage = (
                            await _build_task_with_llm(
                                user_id, run_id, repo_name,
                                stack_profile, task, retry_plan,
                                api_key=opus_worker.api_key,
                                model=opus_worker.model,
                                working_dir=state.get(
                                    "working_dir", ""),
                            )
                        )
                        r_in = retry_usage.get("input_tokens", 0)
                        r_out = retry_usage.get("output_tokens", 0)
                        tokens.add("opus", r_in, r_out)

                        if retry_result:
                            retry_changes = retry_result.get(
                                "changes", [])
                            _rp = [
                                p.get("file", "")
                                for p in retry_plan.get("plan", [])
                            ]
                            fixed = 0
                            for rc in retry_changes:
                                v, f = _audit_file_change(
                                    rc, planned_files=_rp)
                                if v == "PASS":
                                    fixed += 1
                                    await _log(
                                        user_id, run_id,
                                        f"    ✅ [Opus] Re-audit PASS: "
                                        f"{rc.get('file', '?')}",
                                        "info",
                                    )
                                else:
                                    await _log(
                                        user_id, run_id,
                                        f"    ❌ [Opus] Re-audit still "
                                        f"FAIL: {rc.get('file', '?')}",
                                        "warn",
                                    )
                            await _log(
                                user_id, run_id,
                                f"🔄 [Opus] Inline fix: {fixed}/"
                                f"{len(retry_changes)} file(s) now pass",
                                "info",
                            )
                        else:
                            await _log(
                                user_id, run_id,
                                "⚠ [Opus] Inline fix returned no "
                                "changes — will rely on remediation "
                                "pool", "warn",
                            )

                    # Fire narration
                    if narrator_enabled:
                        n_changes = 0
                        changed_files: list[str] = []
                        if code_result:
                            cl = code_result.get("changes", [])
                            n_changes = len(cl)
                            changed_files = [
                                c.get("file", "?")
                                for c in cl[:5]
                            ]
                        remaining = (
                            len(tasks) - state["completed_tasks"])
                        asyncio.create_task(_narrate(
                            user_id, run_id,
                            f"Opus finished task "
                            f"{task_index + 1}/{len(tasks)}: "
                            f"'{task_name}'. "
                            f"{n_changes} file(s) changed: "
                            f"{', '.join(changed_files)}. "
                            f"{state['completed_tasks']}"
                            f"/{len(tasks)} done, "
                            f"{remaining} remaining. "
                            f"Plan pool: {plan_pool.qsize()} "
                            f"waiting.",
                            narrator_key=narrator_key,
                            narrator_model=narrator_model,
                            tokens=tokens,
                        ))

                    # Between tasks: apply any ready remediations
                    await _drain_remediation_pool()
                    await asyncio.sleep(0.15)

            # ── Remediation applier ──────────────────────────────
            async def _drain_remediation_pool() -> None:
                """Apply all available remediation items."""
                applied = 0
                while not remediation_pool.empty():
                    try:
                        fix_item = remediation_pool.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    await _log(user_id, run_id,
                                f"🔧 [Opus] Applying remediation for "
                                f"{fix_item.file}…", "thinking")

                    if fix_item.fix_plan:
                        fix_task = {
                            "id": (f"FIX-{fix_item.task_id}"
                                   f"-{fix_item.file}"),
                            "from_state": "audit failure",
                            "to_state": "remediated",
                            "steps": [
                                f"Fix: {f}"
                                for f in fix_item.findings
                            ],
                            "priority": "high",
                        }
                        code_result, code_usage = (
                            await _build_task_with_llm(
                                user_id, run_id, repo_name,
                                stack_profile, fix_task,
                                fix_item.fix_plan,
                                api_key=opus_worker.api_key,
                                model=opus_worker.model,
                                working_dir=state.get(
                                    "working_dir", ""),
                            )
                        )
                        c_in = code_usage.get("input_tokens", 0)
                        c_out = code_usage.get("output_tokens", 0)
                        tokens.add("opus", c_in, c_out)

                        if code_result:
                            changes = code_result.get("changes", [])
                            for change in changes:
                                await _emit(
                                    user_id,
                                    "upgrade_file_diff",
                                    {
                                        "run_id": run_id,
                                        "task_id": fix_task["id"],
                                        "worker": "opus",
                                        "source": "remediation",
                                        **change,
                                    },
                                )
                            await _log(
                                user_id, run_id,
                                f"✅ Remediation applied: "
                                f"{fix_item.file} "
                                f"({len(changes)} change(s))",
                                "info",
                            )
                        applied += 1
                    else:
                        await _log(
                            user_id, run_id,
                            f"⚠️ No auto-fix for "
                            f"{fix_item.file}: "
                            f"{'; '.join(fix_item.findings)}",
                            "warn",
                        )

                    await _emit(user_id, "remediation_applied", {
                        "run_id": run_id,
                        "file": fix_item.file,
                        "had_fix": fix_item.fix_plan is not None,
                        "pool_remaining":
                            remediation_pool.qsize(),
                    })

                if applied:
                    await _emit(user_id, "upgrade_token_tick", {
                        "run_id": run_id, **tokens.snapshot()})

            # Launch both workers in parallel — fully decoupled
            await asyncio.gather(
                _sonnet_planner(),
                _opus_builder(),
            )

        # ── Wrap up ──────────────────────────────────────────────
        was_stopped = state["_stop_flag"].is_set()
        total_changes = sum(
            r.get("changes_count", 0) for r in state["task_results"])
        proposed = sum(
            1 for r in state["task_results"] if r["status"] == "proposed")
        skipped = sum(
            1 for r in state["task_results"] if r["status"] == "skipped")
        final_tokens = tokens.snapshot()

        await _log(user_id, run_id, "", "system")
        await _log(user_id, run_id,
                    "━━━ Upgrade Analysis Complete ━━━", "system")
        await _log(user_id, run_id,
                    f"📊 {proposed} task(s) analysed, {skipped} skipped",
                    "system")
        await _log(user_id, run_id,
                    f"📝 {total_changes} total file changes proposed",
                    "system")
        await _log(user_id, run_id,
                    f"⚡ Tokens used — "
                    f"Sonnet: {_fmt_tokens(final_tokens['sonnet']['total'])} | "
                    f"Opus: {_fmt_tokens(final_tokens['opus']['total'])} | "
                    f"Haiku: {_fmt_tokens(final_tokens['haiku']['total'])} | "
                    f"Total: {_fmt_tokens(final_tokens['total'])}",
                    "system")
        if was_stopped:
            await _log(user_id, run_id,
                        "🛑 Execution was stopped by user before all "
                        "tasks completed.", "system")
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
        await _log(user_id, run_id,
                    "❌ Upgrade execution failed with an unexpected error",
                    "error")
        await _emit(user_id, "upgrade_complete", {
            "run_id": run_id,
            "status": "error",
            "tokens": tokens.snapshot(),
        })
    finally:
        state["current_task"] = None
        # NOTE: Do NOT clean up working_dir here — the user may still
        # want to /push or /retry.  Cleanup happens on session expiry
        # UNLESS a /retry re-activated the session (flag check below).
        try:
            await asyncio.sleep(600)
        except asyncio.CancelledError:
            pass
        # If /retry relaunched the session, don't nuke the state.
        if state.get("_cleanup_cancelled"):
            return
        wd = state.get("working_dir")
        if wd:
            parent = str(Path(wd).parent)
            shutil.rmtree(parent, ignore_errors=True)
        _active_upgrades.pop(run_id, None)


# ---------------------------------------------------------------------------
# /retry — re-run failed/skipped tasks
# ---------------------------------------------------------------------------

async def _run_retry(
    user_id: str,
    run_id: str,
    state: dict,
    failed_entries: list[tuple[int, dict]],
    sonnet_worker: _WorkerSlot,
    opus_worker: _WorkerSlot,
    repo_name: str,
    stack_profile: dict,
    *,
    narrator_key: str = "",
    narrator_model: str = "",
) -> None:
    """Re-run skipped/failed tasks through the Sonnet→Opus pipeline.

    Each entry in *failed_entries* is ``(index_in_task_results, result_dict)``
    where *result_dict* contains ``_retry_task`` (original task) and
    optionally ``_retry_plan`` (Sonnet's previous plan, unused in full
    re-plan mode).  Results are replaced **in-place** inside
    ``state["task_results"]``.
    """
    tokens: _TokenAccumulator = state.get("_token_tracker") or _TokenAccumulator()
    narrator_enabled = bool(narrator_key and narrator_model)

    try:
        total = len(failed_entries)
        completed = 0

        for idx, tr in failed_entries:
            task = tr["_retry_task"]
            task_id = task.get("id", f"TASK-{idx}")
            task_name = (
                f"{task.get('from_state', '?')} → "
                f"{task.get('to_state', '?')}"
            )

            # ── Announce ──
            await _log(user_id, run_id, "", "system")
            await _log(user_id, run_id,
                        f"━━━ Retry {completed + 1}/{total}: {task_name} ━━━",
                        "system")

            await _emit(user_id, "upgrade_task_start", {
                "run_id": run_id,
                "task_id": task_id,
                "task_index": idx,
                "task_name": task_name,
                "priority": task.get("priority", "medium"),
                "category": task.get("category", ""),
                "steps": task.get("steps", []),
                "worker": "sonnet",
            })

            # ── Sonnet re-plans ──
            await _log(user_id, run_id,
                        f"🧠 [Sonnet] Re-planning task {task_id}…",
                        "thinking")

            plan_result, plan_usage = await _plan_task_with_llm(
                user_id, run_id, repo_name, stack_profile, task,
                api_key=sonnet_worker.api_key, model=sonnet_worker.model,
                working_dir=state.get("working_dir", ""),
            )
            p_in = plan_usage.get("input_tokens", 0)
            p_out = plan_usage.get("output_tokens", 0)
            tokens.add("sonnet", p_in, p_out)

            if plan_result:
                analysis = plan_result.get("analysis", "")
                if analysis:
                    await _log(user_id, run_id,
                                f"🧠 [Sonnet] {analysis}", "thinking")
                plan_files = plan_result.get("plan", [])
                if plan_files:
                    await _log(user_id, run_id,
                                f"📋 [Sonnet] Identified {len(plan_files)} file(s) to change:",
                                "info")
                    for pf in plan_files:
                        await _log(user_id, run_id,
                                    f"  📄 {pf.get('file', '?')} — {pf.get('description', '')}",
                                    "info")
                for risk in plan_result.get("risks", []):
                    await _log(user_id, run_id, f"  ⚠ [Sonnet] {risk}", "warn")

            # ── Opus builds ──
            await _log(user_id, run_id,
                        f"⚡ [Opus] Writing code for task {task_id}…",
                        "thinking")

            code_result, code_usage = await _build_task_with_llm(
                user_id, run_id, repo_name, stack_profile,
                task, plan_result,
                api_key=opus_worker.api_key, model=opus_worker.model,
                working_dir=state.get("working_dir", ""),
            )
            c_in = code_usage.get("input_tokens", 0)
            c_out = code_usage.get("output_tokens", 0)
            tokens.add("opus", c_in, c_out)

            # ── Log Opus output + inline audit ──
            if code_result:
                for thought in code_result.get("thinking", []):
                    await _log(user_id, run_id,
                                f"💭 [Opus] {thought}", "thinking")
                    await asyncio.sleep(0.08)

                changes = code_result.get("changes", [])
                # Build planned file list from Sonnet's plan for scope check
                _retry_planned = None
                if plan_result:
                    _rp_entries = plan_result.get("plan", [])
                    if _rp_entries:
                        _retry_planned = [p.get("file", "") for p in _rp_entries]

                if changes:
                    await _log(user_id, run_id,
                                f"📝 [Opus] {len(changes)} file change(s) proposed:",
                                "system")
                    for change in list(changes):  # copy — we may .remove() inside
                        icon = {"modify": "✏️", "create": "➕",
                                "delete": "🗑️"}.get(
                            change.get("action", "modify"), "📄")
                        await _log(
                            user_id, run_id,
                            f"  {icon} {change.get('file', '?')} — "
                            f"{change.get('description', '')}",
                            "info",
                        )
                        await _emit(user_id, "upgrade_file_diff", {
                            "run_id": run_id,
                            "task_id": task_id,
                            "worker": "opus",
                            **change,
                        })
                        await asyncio.sleep(0.05)

                        # ── Inline audit (retry flow) ──
                        await _emit(user_id, "file_audit_start", {
                            "run_id": run_id,
                            "task_id": task_id,
                            "file": change.get("file", ""),
                        })
                        verdict, findings = _audit_file_change(
                            change, planned_files=_retry_planned)
                        audit_entry = {
                            "file": change.get("file", ""),
                            "action": change.get("action", "modify"),
                            "verdict": verdict,
                            "findings": findings,
                            "task_id": task_id,
                            "original_change": change,
                        }
                        state.setdefault("audit_results", []).append(
                            audit_entry)
                        if verdict == "REJECT":
                            # Hard block — drop this change entirely
                            await _log(user_id, run_id,
                                        f"    🚫 [Opus] REJECTED (scope): "
                                        f"{change.get('file', '?')} "
                                        f"— not in planner's file list, dropped",
                                        "warn")
                            await _emit(user_id, "file_audit_reject", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                                "findings": findings,
                            })
                            changes.remove(change)
                            continue
                        elif verdict == "PASS":
                            await _log(user_id, run_id,
                                        f"    ✅ [Opus] Audit PASS: {change.get('file', '?')}",
                                        "info")
                            await _emit(user_id, "file_audit_pass", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                            })
                        else:
                            for f in findings:
                                await _log(user_id, run_id,
                                            f"    ❌ [Opus] {f}", "warn")
                            await _emit(user_id, "file_audit_fail", {
                                "run_id": run_id,
                                "task_id": task_id,
                                "file": change.get("file", ""),
                                "findings": findings,
                            })

                # Surface builder objections to the user
                for objection in code_result.get("objections", []):
                    await _log(user_id, run_id,
                               f"💬 [Opus objection] {objection}", "warn")
                if code_result.get("objections"):
                    await _emit(user_id, "upgrade_objections", {
                        "run_id": run_id,
                        "task_id": task_id,
                        "objections": code_result.get("objections", []),
                    })

                for warn in code_result.get("warnings", []):
                    await _log(user_id, run_id, f"⚠️ [Opus] {warn}", "warn")

                verifications = code_result.get("verification_steps", [])
                if verifications:
                    await _log(user_id, run_id, "✅ [Opus] Verification:", "info")
                    for v in verifications:
                        await _log(user_id, run_id, f"  → [Opus] {v}", "info")

                n_changes = len(changes)
                new_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "proposed",
                    "changes_count": n_changes,
                    "worker": "opus",
                    "tokens": {"sonnet": plan_usage, "opus": code_usage},
                    "llm_result": code_result,
                    "_plan": plan_result,
                }
                await _log(user_id, run_id,
                            f"✅ Task {task_id} complete — "
                            f"{n_changes} changes proposed", "system")
            else:
                # Still failed — keep retry data for another attempt
                new_entry = {
                    "task_id": task_id,
                    "task_name": task_name,
                    "status": "skipped",
                    "changes_count": 0,
                    "worker": "opus",
                    "tokens": {"sonnet": plan_usage, "opus": code_usage},
                    "_retry_task": task,
                    "_retry_plan": plan_result,
                }
                await _log(user_id, run_id,
                            f"⏭️ Task {task_id} still failed on retry",
                            "warn")

            # Replace the old entry in-place
            state["task_results"][idx] = new_entry

            # Token tick
            snap = tokens.snapshot()
            state["tokens"] = snap
            await _emit(user_id, "upgrade_token_tick", {
                "run_id": run_id, **snap})
            await _emit(user_id, "upgrade_task_complete", {
                "run_id": run_id,
                "task_id": task_id,
                "task_index": idx,
                "status": new_entry["status"],
                "changes_count": new_entry["changes_count"],
                "worker": "opus",
                "token_delta": {"sonnet": plan_usage, "opus": code_usage},
                "token_cumulative": snap,
            })

            completed += 1

            # Narrate (non-blocking)
            if narrator_enabled:
                n_ch = new_entry["changes_count"]
                asyncio.create_task(_narrate(
                    user_id, run_id,
                    f"Retry {completed}/{total}: task '{task_name}' "
                    f"{'produced ' + str(n_ch) + ' change(s)' if new_entry['status'] == 'proposed' else 'still failed'}. "
                    f"{total - completed} task(s) remaining.",
                    narrator_key=narrator_key,
                    narrator_model=narrator_model,
                    tokens=tokens,
                ))
            await asyncio.sleep(0.15)

        # ── Retry wrap-up ──
        final_tokens = tokens.snapshot()
        proposed = sum(
            1 for r in state["task_results"] if r["status"] == "proposed")
        still_skipped = sum(
            1 for r in state["task_results"] if r["status"] == "skipped")
        total_changes = sum(
            r.get("changes_count", 0) for r in state["task_results"])

        await _log(user_id, run_id, "", "system")
        await _log(user_id, run_id, "━━━ Retry Complete ━━━", "system")
        await _log(user_id, run_id,
                    f"📊 {proposed} task(s) proposed, {still_skipped} still skipped",
                    "system")
        await _log(user_id, run_id,
                    f"📝 {total_changes} total file changes proposed",
                    "system")
        await _log(user_id, run_id,
                    f"⚡ Tokens used — "
                    f"Sonnet: {_fmt_tokens(final_tokens['sonnet']['total'])} | "
                    f"Opus: {_fmt_tokens(final_tokens['opus']['total'])} | "
                    f"Haiku: {_fmt_tokens(final_tokens['haiku']['total'])} | "
                    f"Total: {_fmt_tokens(final_tokens['total'])}",
                    "system")

        if still_skipped > 0:
            await _log(user_id, run_id,
                        f"💡 {still_skipped} task(s) still failed — "
                        "you can /retry again or /push what succeeded.",
                        "system")
        else:
            await _log(user_id, run_id,
                        "✅ All tasks now have proposed changes — "
                        "use /push to apply them.",
                        "system")

        state["status"] = "completed"
        state["tokens"] = final_tokens

        await _emit(user_id, "upgrade_complete", {
            "run_id": run_id,
            "status": "completed",
            "total_tasks": len(state["task_results"]),
            "proposed": proposed,
            "skipped": still_skipped,
            "total_changes": total_changes,
            "tokens": final_tokens,
        })

    except Exception:
        logger.exception("Retry execution %s failed", run_id)
        state["status"] = "error"
        await _log(user_id, run_id,
                    "❌ Retry failed with an unexpected error", "error")
        await _emit(user_id, "upgrade_complete", {
            "run_id": run_id,
            "status": "error",
            "tokens": tokens.snapshot(),
        })
    finally:
        # Reset the cleanup_cancelled flag so a future 600s timer
        # from this retry (if we add one) can proceed.
        state.pop("_cleanup_cancelled", None)


def _fmt_tokens(n: int) -> str:
    """Format token count: 142300 → '142.3k'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ---------------------------------------------------------------------------
# Workspace file reader — feeds real file contents to the Builder
# ---------------------------------------------------------------------------

_FILE_SIZE_LIMIT = 50_000       # max bytes per file
_TOTAL_CONTENT_BUDGET = 200_000 # max bytes across all files


def _gather_tree_listing(
    working_dir: str,
    *,
    max_files: int = 300,
) -> str:
    """Build a compact file-tree string from the cloned workspace.

    Returns a newline-separated list of relative paths (sorted), capped
    at *max_files* entries.  Used to give the Planner (Sonnet) visibility
    into the actual repo structure before it writes its plan.
    """
    if not working_dir:
        return ""
    wd = Path(working_dir)
    if not wd.is_dir():
        return ""

    # Use git ls-files synchronously (fast, respects .gitignore)
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files = sorted(
            f.strip()
            for f in proc.stdout.splitlines()
            if f.strip()
        )
    except Exception:
        # Fallback: walk the directory
        files = sorted(
            str(p.relative_to(wd)).replace("\\", "/")
            for p in wd.rglob("*")
            if p.is_file()
            and ".git" not in p.parts
            and "node_modules" not in p.parts
            and "__pycache__" not in p.parts
        )

    if not files:
        return ""

    if len(files) > max_files:
        truncated = files[:max_files]
        truncated.append(f"… and {len(files) - max_files} more files")
        return "\n".join(truncated)
    return "\n".join(files)


def _gather_file_contents(
    working_dir: str,
    plan: dict | None,
) -> dict[str, str]:
    """Read files from the cloned workspace that Sonnet's plan references.

    Returns ``{relative_path: file_content}``.  Only files that exist and
    are under the per-file / total budget are included.
    """
    if not plan or not working_dir:
        return {}

    plan_items = plan.get("plan", [])
    if not plan_items:
        return {}

    wd = Path(working_dir)
    if not wd.is_dir():
        return {}

    contents: dict[str, str] = {}
    budget_remaining = _TOTAL_CONTENT_BUDGET

    for item in plan_items:
        rel = item.get("file", "")
        if not rel:
            continue
        # Only read files that exist (skip 'create' where file is new)
        fp = wd / rel
        if not fp.is_file():
            continue
        try:
            size = fp.stat().st_size
            if size > _FILE_SIZE_LIMIT or size > budget_remaining:
                # Include a truncation marker so Opus knows the file exists
                contents[rel] = (
                    f"[FILE TOO LARGE — {size:,} bytes — "
                    f"showing first {min(_FILE_SIZE_LIMIT, budget_remaining)} bytes]\n"
                    + fp.read_text(encoding="utf-8", errors="replace")[
                        : min(_FILE_SIZE_LIMIT, budget_remaining)
                    ]
                )
                budget_remaining -= min(_FILE_SIZE_LIMIT, budget_remaining)
            else:
                text = fp.read_text(encoding="utf-8", errors="replace")
                contents[rel] = text
                budget_remaining -= len(text.encode("utf-8"))
        except Exception:
            logger.debug("Could not read %s for builder context", rel)

        if budget_remaining <= 0:
            break

    return contents


# ---------------------------------------------------------------------------
# LLM functions — Planner (Sonnet) + Builder (Opus)
# ---------------------------------------------------------------------------


async def _plan_task_with_llm(
    user_id: str,
    run_id: str,
    repo_name: str,
    stack_profile: dict,
    task: dict,
    *,
    api_key: str,
    model: str,
    working_dir: str = "",
) -> tuple[dict | None, dict]:
    """Sonnet analyses a migration task and produces an implementation plan.

    When *working_dir* is provided the repo file tree is injected so
    Sonnet can reference real paths, function names, and line numbers.

    Returns ``(plan_dict | None, usage_dict)``.
    """
    if not api_key:
        await _log(user_id, run_id, "No API key — skipping planning", "warn")
        return None, {"input_tokens": 0, "output_tokens": 0}

    payload: dict = {
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
    }

    # Inject file tree so Sonnet can reference real paths / structure
    if working_dir:
        tree = _gather_tree_listing(working_dir)
        if tree:
            payload["file_tree"] = tree

    user_msg = json.dumps(payload, indent=2)

    try:
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_PLANNER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=8192,
            provider="anthropic",
        )
        usage = (result.get("usage", {}) if isinstance(result, dict)
                 else {"input_tokens": 0, "output_tokens": 0})
        text: str = result["text"] if isinstance(result, dict) else str(result)
        text = _strip_codeblock(text)

        if not text.strip():
            await _log(user_id, run_id,
                       f"⚠ [Sonnet] Empty response for task {task.get('id')} "
                       f"— model returned no content", "error")
            return None, usage

        return json.loads(text), usage
    except json.JSONDecodeError as exc:
        logger.warning("Sonnet returned non-JSON for %s: %s…",
                       task.get("id"), text[:120] if text else "(empty)")
        short = str(exc)[:120]
        await _log(user_id, run_id,
                   f"⚠ [Sonnet] Invalid JSON for task {task.get('id')}: {short}",
                   "error")
        return None, usage if "usage" in dir() else {"input_tokens": 0, "output_tokens": 0}
    except Exception as exc:
        logger.exception("Sonnet planning failed for %s", task.get("id"))
        short = f"{type(exc).__name__}: {str(exc)[:180]}"
        await _log(user_id, run_id,
                   f"Planning failed for task {task.get('id')}: {short}", "error")
        return None, {"input_tokens": 0, "output_tokens": 0}


async def _build_task_with_llm(
    user_id: str,
    run_id: str,
    repo_name: str,
    stack_profile: dict,
    task: dict,
    plan: dict | None,
    *,
    api_key: str,
    model: str,
    working_dir: str = "",
) -> tuple[dict | None, dict]:
    """Opus writes concrete code changes from Sonnet's plan.

    When *working_dir* is provided the real file contents referenced by
    Sonnet's plan are read from the cloned repo and injected into the
    payload so Opus can produce exact before/after snippets.

    Returns ``(code_result | None, usage_dict)``.
    """
    if not api_key:
        await _log(user_id, run_id, "No API key — skipping build", "warn")
        return None, {"input_tokens": 0, "output_tokens": 0}

    # Combine task details with Sonnet's plan for richer context
    payload: dict = {
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
    }
    if plan:
        payload["planner_analysis"] = plan

    # ── Inject real file contents from the cloned workspace ─────
    file_contents = _gather_file_contents(working_dir, plan)
    if file_contents:
        payload["workspace_files"] = file_contents
        total_bytes = sum(len(v.encode()) for v in file_contents.values())
        await _log(
            user_id, run_id,
            f"  🔧 [Opus] {len(file_contents)} workspace file(s) loaded "
            f"({total_bytes // 1024}KB) — generating code…",
            "thinking",
        )
    else:
        await _log(
            user_id, run_id,
            "  🔧 [Opus] Generating code from plan…",
            "thinking",
        )

    user_msg = json.dumps(payload, indent=2)

    try:
        result = await chat(
            api_key=api_key,
            model=model,
            system_prompt=_BUILDER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=settings.LLM_BUILDER_MAX_TOKENS,
            provider="anthropic",
        )
        usage = (result.get("usage", {}) if isinstance(result, dict)
                 else {"input_tokens": 0, "output_tokens": 0})
        text: str = result["text"] if isinstance(result, dict) else str(result)
        text = _strip_codeblock(text)

        if not text.strip():
            await _log(user_id, run_id,
                       f"⚠ [Opus] Empty response for task {task.get('id')} "
                       f"— model returned no content", "error")
            return None, usage

        return json.loads(text), usage
    except json.JSONDecodeError as exc:
        logger.warning("Opus returned non-JSON for %s: %s…",
                       task.get("id"), text[:120] if text else "(empty)")
        short = str(exc)[:120]
        await _log(user_id, run_id,
                   f"⚠ [Opus] Invalid JSON for task {task.get('id')}: {short}",
                   "error")
        return None, usage if "usage" in dir() else {"input_tokens": 0, "output_tokens": 0}
    except Exception as exc:
        logger.exception("Opus build failed for %s", task.get("id"))
        short = f"{type(exc).__name__}: {str(exc)[:180]}"
        await _log(user_id, run_id,
                   f"Build failed for task {task.get('id')}: {short}", "error")
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
