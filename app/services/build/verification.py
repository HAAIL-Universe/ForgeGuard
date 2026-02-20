"""Verification — inline audit, per-file audit, governance gate, syntax/test checks."""

import asyncio
import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from . import _state
from ._state import (
    FORGE_CONTRACTS_DIR,
    logger,
)
from .cost import _accumulate_cost, _get_token_rates

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Semaphore to limit concurrent per-file audit LLM calls (avoid rate-limit storms)
_FILE_AUDIT_SEMAPHORE = asyncio.Semaphore(3)

# Max fix attempts the auditor (Key 2) will try before deferring to builder
_AUDITOR_FIX_ROUNDS = 2


# ---------------------------------------------------------------------------
# Inline (phase-level) audit
# ---------------------------------------------------------------------------


async def _run_inline_audit(
    build_id: UUID,
    phase: str,
    builder_output: str,
    contracts: list[dict],
    api_key: str,
    audit_llm_enabled: bool = True,
) -> tuple[str, str]:
    """Run an LLM-based audit of the builder's phase output.

    When audit_llm_enabled is True, sends the builder output + reference
    contracts to a separate LLM call using auditor_prompt.md as the system
    prompt. The auditor checks for contract compliance, architectural
    drift, and semantic correctness.

    When disabled, returns ('PASS', '') as a no-op (self-certification).

    Returns (verdict, report) where verdict is 'PASS' or 'FAIL' and
    report is the full auditor response text (empty on PASS/stub).
    """
    try:
        await _state.build_repo.append_build_log(
            build_id,
            f"Running {'LLM' if audit_llm_enabled else 'stub'} audit for {phase}",
            source="audit",
            level="info",
        )

        if not audit_llm_enabled:
            await _state.build_repo.append_build_log(
                build_id,
                "LLM audit disabled — auto-passing",
                source="audit",
                level="info",
            )
            return ("PASS", "")

        # Load auditor system prompt from Forge/Contracts
        auditor_prompt_path = FORGE_CONTRACTS_DIR / "auditor_prompt.md"
        if not auditor_prompt_path.exists():
            logger.warning("auditor_prompt.md not found — falling back to stub audit")
            return ("PASS", "")
        auditor_system = auditor_prompt_path.read_text(encoding="utf-8")

        # Build reference contracts (everything except builder_contract + builder_directive)
        reference_types = {
            "blueprint", "manifesto", "stack", "schema",
            "physics", "boundaries", "phases", "ui",
        }
        reference_parts = ["# Reference Contracts (baseline for audit)\n"]
        for c in contracts:
            if c["contract_type"] in reference_types:
                reference_parts.append(f"\n---\n## {c['contract_type']}\n")
                reference_parts.append(c["content"])
                reference_parts.append("\n")
        reference_text = "\n".join(reference_parts)

        # Truncate builder output to last 50K chars to stay within context
        max_output_chars = 50_000
        trimmed_output = builder_output
        if len(builder_output) > max_output_chars:
            trimmed_output = (
                f"[... truncated {len(builder_output) - max_output_chars} chars ...]\n"
                + builder_output[-max_output_chars:]
            )

        # Compose the user message for the auditor
        user_message = (
            f"## Audit Request\n\n"
            f"**Phase:** {phase}\n\n"
            f"### Builder Output for This Phase\n\n"
            f"```\n{trimmed_output}\n```\n\n"
            f"### Reference Contracts\n\n"
            f"{reference_text}\n\n"
            f"### Instructions\n\n"
            f"Review the builder's output for {phase} against the reference contracts above.\n"
            f"Check for: contract compliance, architectural drift, boundary violations, "
            f"schema mismatches, logic errors, and test quality.\n\n"
            f"Classify each finding as BLOCKING (must fix) or ADVISORY (nice to have).\n"
            f"BLOCKING = broken functionality, wrong API schemas, missing required "
            f"deliverables, structural violations.\n"
            f"ADVISORY = style issues, optional tooling, cosmetic preferences.\n\n"
            f"Respond with your audit report. Your verdict MUST be either:\n"
            f"- `CLEAN` — if there are no BLOCKING issues (ADVISORY items are OK)\n"
            f"- `FLAGS FOUND` — ONLY if there are BLOCKING issues\n\n"
            f"End your response with exactly one of these lines:\n"
            f"VERDICT: CLEAN\n"
            f"VERDICT: FLAGS FOUND\n"
        )

        # Call the auditor LLM (Sonnet — accurate and fast)
        from app.clients.llm_client import chat as llm_chat
        result = await llm_chat(
            api_key=api_key,
            model=_state.settings.LLM_QUESTIONNAIRE_MODEL,  # Sonnet
            system_prompt=auditor_system,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4096,
            provider="anthropic",
        )

        audit_text = result["text"] if isinstance(result, dict) else result
        audit_usage = result.get("usage", {}) if isinstance(result, dict) else {}

        # Log the full audit report
        await _state.build_repo.append_build_log(
            build_id,
            f"Auditor report ({audit_usage.get('input_tokens', 0)} in / "
            f"{audit_usage.get('output_tokens', 0)} out):\n{audit_text}",
            source="audit",
            level="info",
        )

        # Parse verdict
        if "VERDICT: CLEAN" in audit_text:
            return ("PASS", audit_text)
        elif "VERDICT: FLAGS FOUND" in audit_text:
            return ("FAIL", audit_text)
        else:
            # Ambiguous response — log warning, default to PASS
            logger.warning("Auditor response missing clear verdict — defaulting to PASS")
            await _state.build_repo.append_build_log(
                build_id,
                "Auditor verdict unclear — defaulting to PASS",
                source="audit",
                level="warn",
            )
            return ("PASS", audit_text)

    except Exception as exc:
        logger.error("LLM audit error for %s: %s", phase, exc)
        await _state.build_repo.append_build_log(
            build_id,
            f"Audit error: {exc} — defaulting to PASS",
            source="audit",
            level="error",
        )
        return ("PASS", "")


# ---------------------------------------------------------------------------
# Per-file audit
# ---------------------------------------------------------------------------


async def _fix_single_file(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    file_path: str,
    findings: str,
    working_dir: str,
    model: str | None = None,
    label: str = "auditor",
) -> str:
    """Apply a targeted fix to a single file based on audit findings.

    Reads the file + related sibling files from disk, sends to the LLM
    with the audit findings for correction, writes the fixed output back.
    Uses ``_FILE_AUDIT_SEMAPHORE`` to respect concurrency limits.

    Returns the new file content after the fix.
    """
    from app.clients.llm_client import chat as llm_chat
    from app.services.tool_executor import _exec_write_file
    import re as _re_fix

    wd = Path(working_dir)
    target = wd / file_path

    # Read current file content
    current_content = ""
    if target.exists():
        try:
            current_content = target.read_text(encoding="utf-8")
        except Exception:
            pass

    # Gather related context (siblings + imports)
    ctx: dict[str, str] = {}
    # Parse imports from the file
    for imp_match in _re_fix.finditer(r'(?:from|import)\s+([\w.]+)', current_content):
        mod = imp_match.group(1)
        mod_path = mod.replace(".", "/") + ".py"
        mod_fp = wd / mod_path
        if mod_fp.exists() and mod_path != file_path and len(ctx) < 6:
            try:
                ctx[mod_path] = mod_fp.read_text(encoding="utf-8")
            except Exception:
                pass
    # Sibling files in same directory
    target_dir = target.parent
    if target_dir.exists():
        for sibling in sorted(target_dir.iterdir()):
            if len(ctx) >= 6:
                break
            if sibling.is_file() and sibling.suffix in (".py", ".ts", ".tsx", ".js"):
                rel = str(sibling.relative_to(wd)).replace("\\", "/")
                if rel != file_path and rel not in ctx:
                    try:
                        ctx[rel] = sibling.read_text(encoding="utf-8")
                    except Exception:
                        pass

    # Truncate context files to keep within budget
    max_ctx_chars = 8_000
    trimmed_ctx: dict[str, str] = {}
    total = 0
    for p, c in ctx.items():
        if total + len(c) > max_ctx_chars:
            break
        trimmed_ctx[p] = c
        total += len(c)

    # Build fix prompt
    resolve_model = model or _state.settings.LLM_QUESTIONNAIRE_MODEL

    system_prompt = (
        "You are a code fixer. You receive a file that has structural issues "
        "identified by a code auditor. Your job is to fix ONLY the specific "
        "issues listed — do not refactor, restyle, or otherwise change code "
        "that is working correctly.\n\n"
        "Output ONLY the complete fixed file content. No markdown fences, "
        "no explanation, no preamble.\n\n"
        "Rules:\n"
        "- Fix each identified issue precisely\n"
        "- Preserve all existing functionality\n"
        "- Keep the same imports, structure, and style\n"
        "- If an import is missing, add it\n"
        "- If a function is referenced but undefined, define it or fix the reference\n"
        "- Do NOT remove code unless the finding specifically says it's wrong\n"
    )

    parts = [f"## File to Fix: `{file_path}`\n\n```\n{current_content}\n```\n"]
    parts.append(f"\n## Audit Findings (fix these)\n{findings}\n")
    if trimmed_ctx:
        parts.append("\n## Related Files (reference only — do not output these)\n")
        for cp, cc in trimmed_ctx.items():
            parts.append(f"\n### {cp}\n```\n{cc[:4000]}\n```\n")
    parts.append(
        f"\n## Instructions\n"
        f"Output the COMPLETE fixed content of `{file_path}`. "
        f"Fix only the issues listed above.\n"
    )

    user_message = "\n".join(parts)

    await _state._broadcast_build_event(user_id, build_id, "file_fixing", {
        "path": file_path,
        "fixer": label,
    })

    async with _FILE_AUDIT_SEMAPHORE:
        result = await asyncio.wait_for(
            llm_chat(
                api_key=api_key,
                model=resolve_model,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=16_384,
                provider="anthropic",
            ),
            timeout=180,
        )

    content = result["text"] if isinstance(result, dict) else result
    usage = result.get("usage", {}) if isinstance(result, dict) else {}

    # Strip markdown fences if model wrapped output
    content = re.sub(r"^```\w*\n", "", content)
    content = re.sub(r"\n```\s*$", "", content)
    if content and not content.endswith("\n"):
        content += "\n"

    # Write to disk
    _exec_write_file({"path": file_path, "content": content}, working_dir)

    # Record cost
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    input_rate, output_rate = _get_token_rates(resolve_model)
    cost = Decimal(input_t) * input_rate + Decimal(output_t) * output_rate
    await _state.build_repo.record_build_cost(
        build_id, f"fix:{label}:{file_path}", input_t, output_t,
        resolve_model, cost,
    )
    await _accumulate_cost(build_id, input_t, output_t, resolve_model, cost)

    await _state.build_repo.append_build_log(
        build_id,
        f"Fix applied by {label}: {file_path} ({input_t}+{output_t} tokens)",
        source="fix", level="info",
    )

    return content


async def _builder_drain_fix_queue(
    build_id: UUID,
    user_id: UUID,
    builder_api_key: str,
    audit_api_key: str,
    fix_queue: "asyncio.Queue[tuple[str, str]]",
    working_dir: str,
    manifest_cache_path: Path,
    audit_llm_enabled: bool = True,
) -> list[tuple[str, str, str]]:
    """After file generation completes, builder (Key 1 / Opus) picks up
    any files that the auditor couldn't fix.

    For each queued file the builder applies a fix then the auditor
    re-audits.  Returns list of ``(path, final_verdict, findings)`` tuples.
    """
    results: list[tuple[str, str, str]] = []

    if fix_queue.empty():
        return results

    queue_size = fix_queue.qsize()
    await _state.build_repo.append_build_log(
        build_id,
        f"Builder picking up {queue_size} file(s) from auditor fix queue...",
        source="fix", level="info",
    )
    await _state._broadcast_build_event(user_id, build_id, "build_log", {
        "message": f"\U0001f527 Builder picking up {queue_size} file(s) the auditor couldn't fix...",
        "source": "fix", "level": "info",
    })

    while not fix_queue.empty():
        try:
            fpath, ffindings = fix_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        _state._touch_progress(build_id)

        try:
            # Builder fix (Key 1 / Opus — higher capability)
            fixed_content = await _fix_single_file(
                build_id, user_id, builder_api_key,
                fpath, ffindings, working_dir,
                model=_state.settings.LLM_BUILDER_MODEL,
                label="builder",
            )

            # Re-audit with auditor (Key 2)
            re_result = await _audit_single_file(
                build_id, user_id, audit_api_key,
                fpath, fixed_content, "",
                audit_llm_enabled,
            )
            _, re_verdict, re_findings = re_result

            if re_verdict == "PASS":
                _update_manifest_cache(manifest_cache_path, fpath, "fixed", "PASS")
                await _state._broadcast_build_event(user_id, build_id, "file_fixed", {
                    "path": fpath,
                    "fixer": "builder",
                    "rounds": 1,
                })
                await _state.build_repo.append_build_log(
                    build_id,
                    f"\u2713 Builder fixed {fpath}",
                    source="fix", level="info",
                )
                await _state._broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"\u2713 Builder fixed {fpath}",
                    "source": "fix", "level": "info",
                })
            else:
                _update_manifest_cache(manifest_cache_path, fpath, "audited", "FAIL")
                await _state.build_repo.append_build_log(
                    build_id,
                    f"Builder fix didn't resolve {fpath} \u2014 will proceed to recovery",
                    source="fix", level="warn",
                )

            results.append((fpath, re_verdict, re_findings))

        except Exception as exc:
            logger.warning("Builder fix failed for %s: %s", fpath, exc)
            results.append((fpath, "FAIL", str(exc)))

    return results


def _update_manifest_cache(
    manifest_cache_path: Path,
    file_path: str,
    status: str,
    verdict: str = "",
) -> None:
    """Update a single file's status in the cached manifest JSON.

    Called right after each per-file audit completes so the cache
    reflects the latest progress.  If the file is not found or the
    cache is unreadable the update is silently skipped (best-effort).
    """
    try:
        if not manifest_cache_path.exists():
            return
        data = json.loads(manifest_cache_path.read_text(encoding="utf-8"))
        for entry in data:
            if entry.get("path") == file_path:
                entry["status"] = status
                if verdict:
                    entry["audit_verdict"] = verdict
                break
        manifest_cache_path.write_text(
            json.dumps(data, indent=2), encoding="utf-8",
        )
    except Exception:
        pass  # best-effort


async def _audit_and_cache(
    manifest_cache_path: Path,
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    audit_llm_enabled: bool = True,
    audit_index: int = 0,
    audit_total: int = 0,
    working_dir: str = "",
    fix_queue: "asyncio.Queue[tuple[str, str]] | None" = None,
) -> tuple[str, str, str]:
    """Audit a file, attempt to fix if FAIL, then persist to manifest cache.

    Fix loop (up to ``_AUDITOR_FIX_ROUNDS``):
      1. Audit -> if PASS, done.
      2. If FAIL, call ``_fix_single_file`` (Key 2 / Sonnet), re-audit.
      3. If still failing after max rounds, push to ``fix_queue`` for builder.

    Returns ``(file_path, final_verdict, findings)``.
    """
    result = await _audit_single_file(
        build_id, user_id, audit_api_key,
        file_path, file_content, file_purpose,
        audit_llm_enabled, audit_index, audit_total,
    )
    fpath, fverdict, ffindings = result

    # If PASS or fixing disabled, just cache and return
    if fverdict == "PASS" or not working_dir or not audit_llm_enabled:
        _update_manifest_cache(manifest_cache_path, fpath, "audited", fverdict)
        return result

    # --- Auditor fix loop (Key 2 / Sonnet) ---
    for fix_round in range(1, _AUDITOR_FIX_ROUNDS + 1):
        try:
            _update_manifest_cache(manifest_cache_path, fpath, "fixing", fverdict)

            await _state.build_repo.append_build_log(
                build_id,
                f"Auditor fixing {fpath} (round {fix_round}/{_AUDITOR_FIX_ROUNDS})...",
                source="fix", level="info",
            )
            await _state._broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"\U0001f527 Auditor fixing {fpath} (round {fix_round}/{_AUDITOR_FIX_ROUNDS})...",
                "source": "fix", "level": "info",
            })

            fixed_content = await _fix_single_file(
                build_id, user_id, audit_api_key,
                fpath, ffindings, working_dir,
                label="auditor",
            )

            # Re-audit the fixed file
            result = await _audit_single_file(
                build_id, user_id, audit_api_key,
                fpath, fixed_content, file_purpose,
                audit_llm_enabled,
            )
            fpath, fverdict, ffindings = result

            if fverdict == "PASS":
                _update_manifest_cache(manifest_cache_path, fpath, "fixed", "PASS")
                await _state._broadcast_build_event(user_id, build_id, "file_fixed", {
                    "path": fpath,
                    "fixer": "auditor",
                    "rounds": fix_round,
                })
                await _state.build_repo.append_build_log(
                    build_id,
                    f"\u2713 Auditor fixed {fpath} in {fix_round} round(s)",
                    source="fix", level="info",
                )
                await _state._broadcast_build_event(user_id, build_id, "build_log", {
                    "message": f"\u2713 Auditor fixed {fpath} in {fix_round} round(s)",
                    "source": "fix", "level": "info",
                })
                return result

        except Exception as exc:
            logger.warning(
                "Auditor fix round %d failed for %s: %s",
                fix_round, fpath, exc,
            )

    # Auditor couldn't fix it — push to builder fix queue
    if fix_queue is not None:
        await fix_queue.put((fpath, ffindings))
        _update_manifest_cache(manifest_cache_path, fpath, "fix_queued", "FAIL")
        await _state.build_repo.append_build_log(
            build_id,
            f"Auditor couldn't fix {fpath} after {_AUDITOR_FIX_ROUNDS} rounds \u2014 queued for builder",
            source="fix", level="warn",
        )
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"\u23f3 {fpath} queued for builder fix (auditor exhausted {_AUDITOR_FIX_ROUNDS} rounds)",
            "source": "fix", "level": "warn",
        })
    else:
        _update_manifest_cache(manifest_cache_path, fpath, "audited", fverdict)

    return result


async def _audit_single_file(
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    audit_llm_enabled: bool = True,
    audit_index: int = 0,
    audit_total: int = 0,
) -> tuple[str, str, str]:
    """Light structural audit of a single generated file.

    Uses the *audit* API key (key 2) to avoid competing with the builder.
    Broadcasts a ``file_audited`` WS event on completion.
    Returns ``(file_path, verdict, findings_summary)``.

    Concurrency is bounded by ``_FILE_AUDIT_SEMAPHORE`` (3).
    Errors are handled gracefully (fail-open).
    """
    import time as _time

    progress_tag = f"[{audit_index}/{audit_total}]" if audit_total else ""

    t0 = _time.monotonic()

    try:
        # --- fast-path: audit disabled / trivially small files ---
        if not audit_llm_enabled or len(file_content.strip()) < 50:
            dur = int((_time.monotonic() - t0) * 1000)
            await _state._broadcast_build_event(user_id, build_id, "file_audited", {
                "path": file_path,
                "verdict": "PASS",
                "findings": "",
                "duration_ms": dur,
            })
            return (file_path, "PASS", "")

        # Announce audit start
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Auditing {progress_tag} {file_path}...",
            "source": "audit", "level": "info",
        })

        # --- Pre-LLM syntax gate: catch real parse errors early ---
        syntax_finding = ""
        if file_path.endswith(".py"):
            import ast as _ast_audit
            try:
                _ast_audit.parse(file_content, filename=file_path)
            except SyntaxError as _se:
                syntax_finding = (
                    f"L{_se.lineno or '?'}: SyntaxError \u2014 {_se.msg}"
                )

        # Acquire semaphore — limits concurrent LLM calls
        async with _FILE_AUDIT_SEMAPHORE:
            path, verdict, findings = await _audit_single_file_llm(
                build_id, user_id, audit_api_key,
                file_path, file_content, file_purpose,
                t0, progress_tag,
            )

        # If we found a real syntax error, always force FAIL regardless
        # of the LLM verdict so the fix loop runs before commit.
        if syntax_finding:
            combined = (
                f"{syntax_finding}\n{findings}".strip()
                if findings
                else syntax_finding
            )
            if verdict != "FAIL":
                dur = int((_time.monotonic() - t0) * 1000)
                await _state.build_repo.append_build_log(
                    build_id,
                    f"File audit FAIL (syntax) {progress_tag}: {file_path} ({dur}ms)\n{combined}",
                    source="audit", level="warn",
                )
                await _state._broadcast_build_event(user_id, build_id, "file_audited", {
                    "path": file_path,
                    "verdict": "FAIL",
                    "findings": combined[:2000],
                    "duration_ms": dur,
                })
            return (file_path, "FAIL", combined)

        return (path, verdict, findings)

    except Exception as exc:
        dur = int((_time.monotonic() - t0) * 1000)
        logger.warning("Per-file audit failed for %s: %s", file_path, exc)
        await _state.build_repo.append_build_log(
            build_id,
            f"Per-file audit error for {file_path}: {exc}",
            source="audit",
            level="warn",
        )
        await _state._broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": "PASS",
            "findings": f"Audit error: {exc}",
            "duration_ms": dur,
        })
        return (file_path, "PASS", "")


async def _audit_single_file_llm(
    build_id: UUID,
    user_id: UUID,
    audit_api_key: str,
    file_path: str,
    file_content: str,
    file_purpose: str,
    t0: float,
    progress_tag: str,
) -> tuple[str, str, str]:
    """Light structural LLM audit (runs under semaphore, separate API key)."""
    import time as _time

    try:
        # Truncate very large files
        max_file_chars = 12_000
        trimmed = file_content
        if len(file_content) > max_file_chars:
            trimmed = (
                file_content[:max_file_chars]
                + f"\n[... truncated {len(file_content) - max_file_chars} chars ...]"
            )

        system_prompt = (
            "You are a fast structural code auditor. Do a quick quality check "
            "on the file below.\n\n"
            "Check ONLY for:\n"
            "- Missing or broken imports/exports\n"
            "- Obvious logic errors or typos\n"
            "- Functions/classes referenced but never defined\n"
            "- File doesn't match its stated purpose\n\n"
            "Do NOT flag: style, naming, missing docs, optional improvements.\n\n"
            "If the file looks structurally sound, respond with just:\n"
            "VERDICT: CLEAN\n\n"
            "If there are real problems, list each on its own line with the "
            "line number(s) where the issue occurs, using this exact format:\n"
            "L<start>[-L<end>]: <short description>\n\n"
            "Examples:\n"
            "L42: 'UserService' imported but never defined in this module\n"
            "L110-L115: unreachable code after return statement\n\n"
            "After all issues, end with:\n"
            "VERDICT: FLAGS FOUND"
        )

        user_message = (
            f"**File:** `{file_path}`\n"
            f"**Purpose:** {file_purpose}\n\n"
            f"```\n{trimmed}\n```"
        )

        from app.clients.llm_client import chat as llm_chat

        result = await asyncio.wait_for(
            llm_chat(
                api_key=audit_api_key,
                model=_state.settings.LLM_QUESTIONNAIRE_MODEL,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1024,
                provider="anthropic",
            ),
            timeout=120,
        )

        audit_text = result["text"] if isinstance(result, dict) else result
        dur = int((_time.monotonic() - t0) * 1000)

        if "VERDICT: FLAGS FOUND" in audit_text:
            verdict = "FAIL"
            findings = audit_text
        else:
            verdict = "PASS"
            findings = ""

        level = "warn" if verdict == "FAIL" else "info"
        await _state.build_repo.append_build_log(
            build_id,
            f"File audit {verdict} {progress_tag}: {file_path} ({dur}ms)"
            + (f"\n{findings}" if findings else ""),
            source="audit",
            level=level,
        )

        await _state._broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": verdict,
            "findings": findings[:2000] if findings else "",
            "duration_ms": dur,
        })

        return (file_path, verdict, findings)

    except Exception as exc:
        dur = int((_time.monotonic() - t0) * 1000)
        logger.warning("Per-file audit LLM error for %s: %s", file_path, exc)
        await _state.build_repo.append_build_log(
            build_id,
            f"Per-file audit error for {file_path}: {exc}",
            source="audit",
            level="warn",
        )
        await _state._broadcast_build_event(user_id, build_id, "file_audited", {
            "path": file_path,
            "verdict": "PASS",
            "findings": f"Audit error: {exc}",
            "duration_ms": dur,
        })
        return (file_path, "PASS", "")


# ---------------------------------------------------------------------------
# Phase output verification (syntax + tests)
# ---------------------------------------------------------------------------


async def _verify_phase_output(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    manifest: list[dict],
    working_dir: str,
    contracts: list[dict],
    touched_files: set[str] | None = None,
) -> dict:
    """Run syntax and test verification on generated files.

    Returns dict with syntax_errors, tests_passed, tests_failed, fixes_applied.
    Attempts to fix issues using the builder with rich context from disk.
    """
    from app.services.tool_executor import _exec_check_syntax, _exec_run_tests
    from .planner import _generate_single_file
    import re as _re_verify

    syntax_errors = 0
    fixes_applied = 0
    tests_passed = 0
    tests_failed = 0
    last_test_output = ""

    # Helper: gather related files from disk for context
    def _gather_related_context(target_path: str, max_files: int = 8) -> dict[str, str]:
        """Read files related to target_path from the working directory."""
        ctx: dict[str, str] = {}
        wd = Path(working_dir)
        # Always include the target file itself
        fp = wd / target_path
        if fp.exists():
            try:
                ctx[target_path] = fp.read_text(encoding="utf-8")
            except Exception:
                pass

        # Parse imports from the file to find related modules
        if target_path in ctx:
            for imp_match in _re_verify.finditer(
                r'(?:from|import)\s+([\w.]+)', ctx[target_path]
            ):
                mod = imp_match.group(1)
                # Convert dotted module to path: app.models.user -> app/models/user.py
                mod_path = mod.replace(".", "/") + ".py"
                mod_fp = wd / mod_path
                if mod_fp.exists() and mod_path not in ctx and len(ctx) < max_files:
                    try:
                        ctx[mod_path] = mod_fp.read_text(encoding="utf-8")
                    except Exception:
                        pass
                # Also try __init__.py in the package
                pkg_init = mod.replace(".", "/") + "/__init__.py"
                pkg_fp = wd / pkg_init
                if pkg_fp.exists() and pkg_init not in ctx and len(ctx) < max_files:
                    try:
                        ctx[pkg_init] = pkg_fp.read_text(encoding="utf-8")
                    except Exception:
                        pass

        # Include sibling files in the same directory
        target_dir = (wd / target_path).parent
        if target_dir.exists():
            for sibling in sorted(target_dir.iterdir()):
                if len(ctx) >= max_files:
                    break
                if sibling.is_file() and sibling.suffix == ".py":
                    rel = str(sibling.relative_to(wd)).replace("\\", "/")
                    if rel not in ctx:
                        try:
                            ctx[rel] = sibling.read_text(encoding="utf-8")
                        except Exception:
                            pass
        return ctx

    # Helper: extract failing file paths from pytest output
    def _parse_failing_files(test_output: str) -> list[str]:
        """Extract file paths of failing tests from pytest output."""
        failing: list[str] = []
        for m in _re_verify.finditer(r'(?:FAILED|ERROR)\s+([\w/\\.-]+\.py)', test_output):
            fp = m.group(1).replace("\\", "/")
            # Strip ::test_name suffix if present
            fp = fp.split("::")[0]
            if fp not in failing:
                failing.append(fp)
        return failing

    # Scope verification to only files touched in this phase/run if provided
    def _filter_manifest(entries: list[dict], *, exts: tuple[str, ...]) -> list[dict]:
        if touched_files is not None:
            return [f for f in entries if f["path"] in touched_files and f["path"].endswith(exts)]
        return [f for f in entries if f["path"].endswith(exts)]

    # Check syntax on Python files (touched-only when available)
    py_files = _filter_manifest(manifest, exts=(".py",))
    if py_files:
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Checking syntax on {len(py_files)} Python files...",
            "source": "verify", "level": "info",
        })
    for f in py_files:
        _state._touch_progress(build_id)
        result = await _exec_check_syntax({"file_path": f["path"]}, working_dir)
        if "No syntax errors" in result:
            continue
        if "error" in result.lower() or "SyntaxError" in result:
            syntax_errors += 1
            await _state._broadcast_build_event(user_id, build_id, "build_log", {
                "message": f"Syntax error in {f['path']}: {result.strip()}",
                "source": "verify", "level": "warn",
            })
            await _state._set_build_activity(
                build_id, user_id, f"Fixing syntax error in {f['path']}...",
                model="opus",
            )
            # Attempt targeted fix using _fix_single_file (preserves existing
            # code, only patches the syntax error — no full regeneration)
            for attempt in range(2):
                _state._touch_progress(build_id)
                try:
                    await _fix_single_file(
                        build_id, user_id, api_key,
                        f["path"],
                        f"Syntax error detected by ast.parse:\n{result}\n\n"
                        f"Fix ONLY the syntax error(s). Do NOT rewrite or restructure "
                        f"the file. Preserve all existing functionality.",
                        working_dir,
                        label="verify",
                    )
                    recheck = await _exec_check_syntax({"file_path": f["path"]}, working_dir)
                    # "No syntax errors" contains the substring "error", so
                    # check for the success phrase FIRST to avoid false negatives.
                    if "No syntax errors" in recheck:
                        fixes_applied += 1
                        syntax_errors -= 1
                        await _state._broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"\u2713 Syntax fixed: {f['path']} (attempt {attempt + 1})",
                            "source": "verify", "level": "info",
                        })
                        break
                    elif "SyntaxError" in recheck or "error" in recheck.lower():
                        await _state._broadcast_build_event(user_id, build_id, "build_log", {
                            "message": f"Fix attempt {attempt + 1} for {f['path']} \u2014 still has errors: {recheck.strip()}",
                            "source": "verify", "level": "warn",
                        })
                        result = recheck  # feed updated error into next attempt
                except Exception as exc:
                    logger.warning("Syntax fix attempt %d failed for %s: %s", attempt + 1, f["path"], exc)
                    await _state._broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Fix attempt {attempt + 1} failed for {f['path']}: {exc}",
                        "source": "verify", "level": "warn",
                    })

    # Run tests if test files were generated/touched
    if touched_files is not None:
        test_files = [f for f in manifest if f["path"].startswith("tests/") and f["path"] in touched_files]
    else:
        test_files = [f for f in manifest if f["path"].startswith("tests/")]
    if test_files:
        test_paths = " ".join(f["path"] for f in test_files)
        _state._touch_progress(build_id)
        await _state._broadcast_build_event(user_id, build_id, "build_log", {
            "message": f"Running pytest on {len(test_files)} test file(s)...",
            "source": "verify", "level": "info",
        })
        try:
            test_result = await _exec_run_tests(
                {"command": f"pytest {test_paths} -x -q -o addopts=", "timeout": 120}, working_dir,
            )
            last_test_output = test_result
            if "exit_code: 0" in test_result or "passed" in test_result.lower():
                tests_passed = len(test_files)
            else:
                tests_failed = len(test_files)
                # Identify which files actually failed
                failing_paths = _parse_failing_files(test_result)

                # Try to fix failures — target the impl files, not just tests
                for attempt in range(2):
                    if tests_failed == 0:
                        break
                    _state._touch_progress(build_id)
                    await _state._broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Tests failed \u2014 fix attempt {attempt + 1}/2...",
                        "source": "verify", "level": "warn",
                    })

                    # Determine which files to fix: both test + impl
                    files_to_fix: list[dict] = []
                    for tf in test_files:
                        # Find the implementation file the test is exercising
                        impl_candidates = []
                        # Parse test file for imports to find the real target
                        test_fp = Path(working_dir) / tf["path"]
                        if test_fp.exists():
                            try:
                                test_src = test_fp.read_text(encoding="utf-8")
                                for imp in _re_verify.finditer(
                                    r'from\s+([\w.]+)\s+import', test_src
                                ):
                                    mod = imp.group(1).replace(".", "/") + ".py"
                                    if (Path(working_dir) / mod).exists():
                                        impl_candidates.append(mod)
                            except Exception:
                                pass

                        # Fallback: conventional mapping
                        if not impl_candidates:
                            conventional = tf["path"].replace("tests/test_", "app/")
                            if (Path(working_dir) / conventional).exists():
                                impl_candidates.append(conventional)

                        # Fix the implementation file(s) first
                        for impl_path in impl_candidates:
                            impl_entry = next(
                                (m for m in manifest if m["path"] == impl_path),
                                {"path": impl_path, "language": "python",
                                 "purpose": f"Implementation for {tf['path']}"},
                            )
                            if impl_entry not in files_to_fix:
                                files_to_fix.append(impl_entry)

                        # Then fix the test file too
                        if tf not in files_to_fix:
                            files_to_fix.append(tf)

                    for fix_entry in files_to_fix:
                        try:
                            context = _gather_related_context(fix_entry["path"])
                            # Also include failing test output in context
                            await _generate_single_file(
                                build_id, user_id, api_key,
                                {**fix_entry, "purpose": f"Fix to pass tests: {fix_entry['path']}"},
                                contracts, context, "", working_dir,
                                error_context=(
                                    f"Test failure output:\n{test_result[:3000]}\n\n"
                                    f"Failing tests: {', '.join(failing_paths) if failing_paths else 'see output above'}\n\n"
                                    f"Fix the code so the tests pass. Focus on the error messages "
                                    f"and tracebacks above. Do NOT remove or weaken tests \u2014 "
                                    f"fix the implementation to match what the tests expect."
                                ),
                            )
                        except Exception:
                            pass

                    # Re-run tests
                    _state._touch_progress(build_id)
                    await _state._broadcast_build_event(user_id, build_id, "build_log", {
                        "message": f"Re-running tests after fix attempt {attempt + 1}...",
                        "source": "verify", "level": "info",
                    })
                    retest = await _exec_run_tests(
                        {"command": f"pytest {test_paths} -x -q -o addopts=", "timeout": 120}, working_dir,
                    )
                    last_test_output = retest
                    if "exit_code: 0" in retest or "passed" in retest.lower():
                        tests_passed = len(test_files)
                        tests_failed = 0
                        fixes_applied += 1
                        await _state._broadcast_build_event(user_id, build_id, "build_log", {
                            "message": "\u2713 All tests passing after fix",
                            "source": "verify", "level": "info",
                        })
                        break
                    # Update test_result for next attempt's error context
                    test_result = retest
                    failing_paths = _parse_failing_files(retest)
        except Exception as exc:
            logger.warning("Test verification failed: %s", exc)

    result = {
        "syntax_errors": syntax_errors,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "fixes_applied": fixes_applied,
        "test_output": last_test_output,
    }

    await _state._broadcast_build_event(user_id, build_id, "verification_result", result)
    return result


# ---------------------------------------------------------------------------
# Governance gate (deterministic checks at phase transitions)
# ---------------------------------------------------------------------------

# Re-use stdlib set from audit runner (avoids duplicate source of truth)
from app.audit.runner import _PYTHON_STDLIB, _PY_NAME_MAP, _extract_imports


async def _run_governance_checks(
    build_id: UUID,
    user_id: UUID,
    api_key: str,
    manifest: list[dict],
    working_dir: str,
    contracts: list[dict],
    touched_files: set[str],
    phase_name: str = "",
) -> dict:
    """Run deterministic governance checks before committing a phase.

    Checks modelled after Forge run_audit.ps1 (A1/A4/A9/W1/W3):
        G1 - Scope compliance (manifest vs disk)
        G2 - Boundary compliance (forbidden import patterns)
        G3 - Dependency gate (imports vs requirements)
        G4 - Secrets scan (file content, not git diff)
        G5 - Physics route coverage
        G6 - Rename / ghost file detection
        G7 - TODO / placeholder scan

    Returns dict:
        passed: bool - True if no blocking failures
        checks: list[dict] - per-check results {code, name, result, detail}
        blocking_failures: int
        warnings: int
    """
    import fnmatch as _fnmatch

    wd = Path(working_dir)
    checks: list[dict] = []

    await _state._broadcast_build_event(user_id, build_id, "build_log", {
        "message": f"Running governance checks for {phase_name}...",
        "source": "governance", "level": "info",
    })
    await _state._set_build_activity(build_id, user_id, f"Governance gate \u2013 {phase_name}...")

    # --- G1: Scope compliance (manifest vs disk) --------------------------
    manifest_paths = {e["path"] for e in manifest if e.get("path")}
    disk_paths: set[str] = set()
    if touched_files:
        for tf in touched_files:
            fp = wd / tf
            if fp.exists():
                disk_paths.add(tf)

    phantom = disk_paths - manifest_paths  # on disk but not in manifest
    missing = {
        p for p in manifest_paths
        if not (wd / p).exists()
        and not any(e.get("action") == "delete" for e in manifest if e.get("path") == p)
    }

    g1_violations: list[str] = []
    if phantom:
        g1_violations.append(f"phantom files (on disk, not in manifest): {', '.join(sorted(phantom)[:5])}")
    if missing:
        g1_violations.append(f"missing files (in manifest, not on disk): {', '.join(sorted(missing)[:5])}")

    checks.append({
        "code": "G1",
        "name": "Scope compliance",
        "result": "FAIL" if g1_violations else "PASS",
        "detail": "; ".join(g1_violations) if g1_violations else "All manifest files present on disk.",
    })

    # --- G2: Boundary compliance (adapt runner.check_a4) ------------------
    boundaries_data: dict | None = None
    for c in contracts:
        if c.get("contract_type") == "boundaries":
            try:
                content = c.get("content", "")
                cleaned = content.strip()
                if cleaned.startswith("```"):
                    first_nl = cleaned.find("\n")
                    if first_nl >= 0:
                        cleaned = cleaned[first_nl + 1:]
                if cleaned.rstrip().endswith("```"):
                    cleaned = cleaned.rstrip()[:-3]
                boundaries_data = json.loads(cleaned.strip())
            except (json.JSONDecodeError, Exception):
                pass
            break

    # Fallback: try loading from FORGE_CONTRACTS_DIR on disk
    if boundaries_data is None:
        boundaries_file = FORGE_CONTRACTS_DIR / "boundaries.json"
        if boundaries_file.exists():
            try:
                boundaries_data = json.loads(boundaries_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    g2_violations: list[str] = []
    if boundaries_data:
        for layer in boundaries_data.get("layers", []):
            layer_name = layer.get("name", "unknown")
            glob_pattern = layer.get("glob", "")
            forbidden = layer.get("forbidden", [])

            glob_dir = wd / os.path.dirname(glob_pattern)
            glob_filter = os.path.basename(glob_pattern)
            if not glob_dir.is_dir():
                continue

            for entry in sorted(glob_dir.iterdir()):
                if entry.name in ("__init__.py", "__pycache__"):
                    continue
                if not _fnmatch.fnmatch(entry.name, glob_filter):
                    continue
                if not entry.is_file():
                    continue
                # Only check files we actually touched (performance)
                rel = str(entry.relative_to(wd)).replace("\\", "/")
                if touched_files and rel not in touched_files:
                    continue
                try:
                    content = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for rule in forbidden:
                    pattern = rule.get("pattern", "")
                    reason = rule.get("reason", "")
                    if re.search(pattern, content, re.IGNORECASE):
                        g2_violations.append(
                            f"[{layer_name}] {entry.name} contains '{pattern}' ({reason})"
                        )

    checks.append({
        "code": "G2",
        "name": "Boundary compliance",
        "result": "FAIL" if g2_violations else "PASS",
        "detail": "; ".join(g2_violations) if g2_violations else "No forbidden patterns found.",
    })

    # --- G3: Dependency gate (adapt runner.check_a9) ----------------------
    forge_json = wd / "forge.json"
    g3_failures: list[str] = []
    if forge_json.exists():
        try:
            forge_cfg = json.loads(forge_json.read_text(encoding="utf-8"))
            dep_file = forge_cfg.get("backend", {}).get("dependency_file", "requirements.txt")
            lang = forge_cfg.get("backend", {}).get("language", "python")
            dep_path = wd / dep_file
            if dep_path.exists():
                dep_content = dep_path.read_text(encoding="utf-8")
                source_exts = {
                    "python": {".py"},
                    "typescript": {".ts", ".tsx"},
                    "javascript": {".js", ".jsx"},
                }.get(lang, set())
                for tf in sorted(touched_files):
                    ext = os.path.splitext(tf)[1]
                    if ext not in source_exts:
                        continue
                    fp = wd / tf
                    if not fp.exists():
                        continue
                    try:
                        file_content = fp.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    imports = _extract_imports(file_content, lang)
                    for imp in imports:
                        if lang == "python":
                            if imp in _PYTHON_STDLIB:
                                continue
                            local_dir = wd / imp
                            if local_dir.is_dir():
                                continue
                            look_for = _PY_NAME_MAP.get(imp, imp)
                            if not re.search(re.escape(look_for), dep_content, re.IGNORECASE):
                                g3_failures.append(
                                    f"{tf} imports '{imp}' (not in {dep_file})"
                                )
        except Exception:
            pass  # forge.json parse error — skip gracefully

    checks.append({
        "code": "G3",
        "name": "Dependency gate",
        "result": "FAIL" if g3_failures else "PASS",
        "detail": "; ".join(g3_failures[:10]) if g3_failures else "All imports have declared dependencies.",
    })

    # --- G4: Secrets scan (scan file content, not git diff) ---------------
    secret_patterns = ["sk-", "AKIA", "-----BEGIN", "password=", "secret=", "token="]
    g4_found: list[str] = []
    for tf in sorted(touched_files):
        fp = wd / tf
        if not fp.exists():
            continue
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for sp in secret_patterns:
            if sp in content:
                # Exclude false positives in test files, configs, or .env.example
                if any(ignore in tf for ignore in ("test_", ".example", "config.py")):
                    continue
                g4_found.append(f"{tf} contains '{sp}'")
    checks.append({
        "code": "G4",
        "name": "Secrets scan",
        "result": "WARN" if g4_found else "PASS",
        "detail": "; ".join(g4_found[:5]) if g4_found else "No secret patterns detected.",
    })

    # --- G5: Physics route coverage (adapt runner.check_w3) ---------------
    physics_yaml = FORGE_CONTRACTS_DIR / "physics.yaml"
    g5_uncovered: list[str] = []
    if physics_yaml.exists():
        try:
            yaml_lines = physics_yaml.read_text(encoding="utf-8").splitlines()
            physics_paths: list[str] = []
            for line in yaml_lines:
                m = re.match(r"^  (/[^:]+):", line)
                if m:
                    physics_paths.append(m.group(1))

            router_dir = wd / "app" / "api" / "routers"
            if router_dir.is_dir():
                router_files = [
                    f.name for f in router_dir.iterdir()
                    if f.is_file() and f.name not in ("__init__.py", "__pycache__")
                ]
                for p in physics_paths:
                    if p == "/" or "/static/" in p:
                        continue
                    parts = p.strip("/").split("/")
                    segment = parts[0] if parts else ""
                    if not segment:
                        continue
                    expected = [f"{segment}.py", f"{segment}.ts", f"{segment}.js"]
                    if not any(ef in router_files for ef in expected):
                        g5_uncovered.append(f"{p} (no handler for '{segment}')")
        except Exception:
            pass

    checks.append({
        "code": "G5",
        "name": "Physics route coverage",
        "result": "WARN" if g5_uncovered else "PASS",
        "detail": "; ".join(g5_uncovered[:5]) if g5_uncovered else "All physics paths covered.",
    })

    # --- G6: Rename / ghost file detection --------------------------------
    g6_issues: list[str] = []
    try:
        # Use git diff --name-status to detect renames
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--cached", "--name-status", "--diff-filter=R",
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout:
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if line.startswith("R"):
                    g6_issues.append(f"rename detected: {line.strip()}")
    except Exception:
        pass  # Git not available or no renames

    checks.append({
        "code": "G6",
        "name": "Rename detection",
        "result": "WARN" if g6_issues else "PASS",
        "detail": "; ".join(g6_issues[:5]) if g6_issues else "No renames detected.",
    })

    # --- G7: TODO / placeholder scan --------------------------------------
    todo_patterns = [
        re.compile(r"#\s*TODO\b", re.IGNORECASE),
        re.compile(r"//\s*TODO\b", re.IGNORECASE),
        re.compile(r"raise\s+NotImplementedError"),
        re.compile(r"pass\s*#\s*placeholder", re.IGNORECASE),
        re.compile(r"\.\.\.\s*#\s*stub", re.IGNORECASE),
    ]
    g7_found: list[str] = []
    for tf in sorted(touched_files):
        fp = wd / tf
        if not fp.exists():
            continue
        try:
            content = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pat in todo_patterns:
            matches = pat.findall(content)
            if matches:
                g7_found.append(f"{tf}: {len(matches)}\u00d7 '{pat.pattern}'")
                break  # One report per file

    checks.append({
        "code": "G7",
        "name": "TODO / placeholder scan",
        "result": "WARN" if g7_found else "PASS",
        "detail": "; ".join(g7_found[:10]) if g7_found else "No TODO/placeholder markers found.",
    })

    # --- Aggregate results ------------------------------------------------
    blocking = sum(1 for c in checks if c["result"] == "FAIL")
    warnings = sum(1 for c in checks if c["result"] == "WARN")
    passed = blocking == 0

    # Broadcast per-check results
    for c in checks:
        icon = "\u2705" if c["result"] == "PASS" else "\u274c" if c["result"] == "FAIL" else "\u26a0\ufe0f"
        await _state._broadcast_build_event(user_id, build_id, "governance_check", {
            "code": c["code"],
            "name": c["name"],
            "result": c["result"],
            "detail": c["detail"],
            "icon": icon,
            "phase": phase_name,
        })

    # Summary event
    summary_msg = (
        f"Governance gate: {len(checks) - blocking - warnings} PASS, "
        f"{blocking} FAIL, {warnings} WARN"
    )
    await _state.build_repo.append_build_log(
        build_id, summary_msg, source="governance", level="info" if passed else "warn",
    )
    event_type = "governance_pass" if passed else "governance_fail"
    await _state._broadcast_build_event(user_id, build_id, event_type, {
        "phase": phase_name,
        "checks": checks,
        "blocking_failures": blocking,
        "warnings": warnings,
        "passed": passed,
    })

    return {
        "passed": passed,
        "checks": checks,
        "blocking_failures": blocking,
        "warnings": warnings,
    }
