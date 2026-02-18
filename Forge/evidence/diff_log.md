# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-18T10:33:37+00:00
- Branch: master
- HEAD: cb38e2b2a64c91e392c977fe2b8f4716c56a78f9
- BASE_HEAD: 1aa99e88e786a6f72ed583333b58fce4883f7fe3
- Diff basis: unstaged (working tree)

## Cycle Status
- Status: IN_PROCESS

## Summary
- TODO: 1-5 bullets (what changed, why, scope).

## Files Changed (unstaged (working tree))
- app/services/upgrade_executor.py
- tests/test_upgrade_executor.py

## git status -sb
    ## master...origin/master
     M app/services/upgrade_executor.py
     M tests/test_upgrade_executor.py
    ?? app/services/architecture_baseline.py
    ?? app/services/consistency_scorer.py
    ?? app/services/reliability_scorer.py
    ?? tests/test_architecture_baseline.py
    ?? tests/test_consistency_scorer.py
    ?? tests/test_reliability_scorer.py

## Verification
- TODO: verification evidence (static -> runtime -> behavior -> contract).

## Notes (optional)
- TODO: blockers, risks, constraints.

## Next Steps
- TODO: next actions (small, specific).

## Minimal Diff Hunks
    diff --git a/app/services/upgrade_executor.py b/app/services/upgrade_executor.py
    index bfc98d9..208cf65 100644
    --- a/app/services/upgrade_executor.py
    +++ b/app/services/upgrade_executor.py
    @@ -61,6 +61,37 @@ class _WorkerSlot:
         display: str        # Human label for logs ÔÇö e.g. "Opus 4.6"
     
     
    +@dataclass
    +class _PlanPoolItem:
    +    """A completed plan sitting in the pool, waiting for the builder."""
    +    task_index: int
    +    task: dict
    +    plan_result: dict | None
    +    plan_usage: dict
    +
    +
    +@dataclass
    +class _RemediationItem:
    +    """An audit-failure fix sitting in the remediation pool.
    +
    +    Sonnet generates *fix_plan* (a dict in the same schema as
    +    ``_plan_task_with_llm`` output) while the builder is busy.
    +    Opus picks it up between tasks and applies the fix.
    +    """
    +    file: str
    +    findings: list[str]
    +    original_change: dict
    +    task_id: str
    +    fix_plan: dict | None = None
    +    priority: int = 10            # lower = higher priority
    +    _seq: int = 0                 # monotonic tiebreaker for PriorityQueue
    +
    +    def __lt__(self, other: "_RemediationItem") -> bool:
    +        if self.priority != other.priority:
    +            return self.priority < other.priority
    +        return self._seq < other._seq
    +
    +
     # ---------------------------------------------------------------------------
     # In-memory execution state
     # ---------------------------------------------------------------------------
    @@ -592,6 +623,152 @@ def _write_audit_trail(
         return str(out_path)
     
     
    +# ---------------------------------------------------------------------------
    +# Remediation plan generator (deterministic + optional LLM fallback)
    +# ---------------------------------------------------------------------------
    +
    +_REMEDIATION_SYSTEM_PROMPT = """\
    +You are ForgeGuard's Remediation Planner.  Given a file that failed \
    +deterministic audit checks, produce a minimal fix plan.
    +
    +Respond with valid JSON:
    +{
    +  "analysis": "what went wrong and how to fix it",
    +  "plan": [
    +    {
    +      "file": "path/to/file",
    +      "action": "modify",
    +      "description": "exact change needed",
    +      "key_considerations": "constraints"
    +    }
    +  ],
    +  "risks": [],
    +  "verification_strategy": ["re-run audit"],
    +  "implementation_notes": "specific code snippet if possible"
    +}
    +"""
    +
    +
    +def _build_deterministic_fix(
    +    file_path: str, findings: list[str], original_change: dict,
    +) -> dict | None:
    +    """Try to produce a fix plan without calling the LLM.
    +
    +    Returns a plan dict (same schema as ``_plan_task_with_llm`` output)
    +    for well-understood failure modes, or ``None`` when human/LLM
    +    judgement is needed.
    +    """
    +    content = original_change.get("after_snippet", "")
    +    plan_entries: list[dict] = []
    +
    +    for finding in findings:
    +        if "Wildcard import" in finding:
    +            # Extract the wildcard line and suggest explicit imports
    +            plan_entries.append({
    +                "file": file_path,
    +                "action": "modify",
    +                "description": f"Replace wildcard import: {finding}",
    +                "key_considerations": (
    +                    "Replace 'from X import *' with explicit named imports"
    +                ),
    +            })
    +        elif "Scope deviation" in finding:
    +            # Out-of-scope file ÔÇö suggest removal
    +            plan_entries.append({
    +                "file": file_path,
    +                "action": "delete",
    +                "description": f"Remove out-of-scope file: {finding}",
    +                "key_considerations": (
    +                    "File not in Sonnet's plan ÔÇö remove to maintain scope"
    +                ),
    +            })
    +        elif "Syntax error" in finding:
    +            plan_entries.append({
    +                "file": file_path,
    +                "action": "modify",
    +                "description": f"Fix syntax: {finding}",
    +                "key_considerations": (
    +                    "Re-generate with corrected syntax"
    +                ),
    +            })
    +        elif "Invalid JSON" in finding:
    +            plan_entries.append({
    +                "file": file_path,
    +                "action": "modify",
    +                "description": f"Fix JSON: {finding}",
    +                "key_considerations": "Ensure valid JSON structure",
    +            })
    +
    +    if not plan_entries:
    +        return None  # unknown failure type ÔÇö needs LLM
    +
    +    return {
    +        "analysis": f"Deterministic fix for {len(plan_entries)} finding(s) "
    +                     f"in {file_path}",
    +        "plan": plan_entries,
    +        "risks": [],
    +        "verification_strategy": ["Re-run inline audit"],
    +        "implementation_notes": (
    +            f"Original content length: {len(content)} chars. "
    +            f"Apply minimal targeted fix."
    +        ),
    +    }
    +
    +
    +async def _generate_remediation_plan(
    +    user_id: str,
    +    run_id: str,
    +    file_path: str,
    +    findings: list[str],
    +    original_change: dict,
    +    *,
    +    api_key: str,
    +    model: str,
    +    tokens: Any,
    +) -> dict | None:
    +    """Generate a fix plan for an audit failure.
    +
    +    First tries a deterministic fix; falls back to Sonnet LLM only for
    +    cases that need human-level judgement (e.g. secret removal).
    +    """
    +    # Fast path ÔÇö deterministic fix
    +    det_fix = _build_deterministic_fix(file_path, findings, original_change)
    +    if det_fix is not None:
    +        return det_fix
    +
    +    # Slow path ÔÇö Sonnet LLM
    +    try:
    +        prompt = (
    +            f"File: {file_path}\n"
    +            f"Findings:\n" +
    +            "\n".join(f"  - {f}" for f in findings) +
    +            f"\n\nOriginal content (first 2000 chars):\n"
    +            f"{(original_change.get('after_snippet', ''))[:2000]}\n\n"
    +            f"Generate a minimal fix plan."
    +        )
    +        raw = await chat(
    +            system=_REMEDIATION_SYSTEM_PROMPT,
    +            user=prompt,
    +            api_key=api_key,
    +            model=model,
    +        )
    +        text = raw.get("text", "")
    +        usage = raw.get("usage", {})
    +        p_in = usage.get("input_tokens", 0)
    +        p_out = usage.get("output_tokens", 0)
    +        tokens.add("sonnet", p_in, p_out)
    +
    +        # Parse JSON
    +        text = _strip_codeblock(text)
    +        return json.loads(text)
    +    except Exception:
    +        logger.warning(
    +            "Remediation LLM call failed for %s ÔÇö will skip auto-fix",
    +            file_path,
    +        )
    +        return None
    +
    +
     # ---------------------------------------------------------------------------
     # File change application
     # ---------------------------------------------------------------------------
    @@ -1630,6 +1807,7 @@ async def _run_upgrade(
                                 "verdict": verdict,
                                 "findings": findings,
                                 "task_id": task_id,
    +                            "original_change": change,
                             }
                             state.setdefault("audit_results", []).append(
                                 audit_entry)
    @@ -1717,219 +1895,411 @@ async def _run_upgrade(
                     "token_cumulative": snap,
                 })
     
    -        # ÔöÇÔöÇ Pipelined dual-worker execution ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    -        # Sonnet plans task N+1 while Opus codes task N.
    +        # ÔöÇÔöÇ Pool-based dual-worker execution ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +        # Sonnet plans ALL tasks as fast as possible ÔåÆ plan pool.
    +        # Opus pulls plans from the pool when ready ÔåÆ codes them.
    +        # After planning, Sonnet generates remediation plans for
    +        # audit failures ÔåÆ remediation pool.  Opus drains these
    +        # between tasks.
             #
             # Time 0: [Sonnet] Plan Task 0
    -        # Time 1: [Sonnet] Plan Task 1 ÔÇû [Opus] Code Task 0
    -        # Time 2: [Sonnet] Plan Task 2 ÔÇû [Opus] Code Task 1
    +        # Time 1: [Sonnet] Plan Task 1  (poolÔåÉ)  ÔÇû  [Opus] Code Task 0  (ÔåÉpool)
    +        # Time 2: [Sonnet] Plan Task 2  (poolÔåÉ)  ÔÇû  [Opus] still on 0
    +        # Time 3: [Sonnet] Plan Task 3  (poolÔåÉ)  ÔÇû  [Opus] Code Task 1  (ÔåÉpool)
             # ÔÇª
    -        # Time N:                         [Opus] Code Task N-1
    +        # Time K: [Sonnet] Remediate F0           ÔÇû  [Opus] Code Task N
     
    -        # Phase 0: Sonnet plans the first task
    -        if not tasks:
    -            # Nothing to do ÔÇö fall through to wrap-up
    -            pass
    -        else:
    -            first_task = tasks[0]
    -            first_id = first_task.get("id", "TASK-0")
    -            first_name = (f"{first_task.get('from_state', '?')} ÔåÆ "
    -                          f"{first_task.get('to_state', '?')}")
    +        if tasks:
    +            plan_pool: asyncio.Queue[_PlanPoolItem | None] = asyncio.Queue()
    +            remediation_pool: asyncio.PriorityQueue = asyncio.PriorityQueue()
    +            sonnet_done = asyncio.Event()
    +            _rem_seq = 0
     
    -            await _emit(user_id, "upgrade_task_start", {
    -                "run_id": run_id,
    -                "task_id": first_id,
    -                "task_index": 0,
    -                "task_name": first_name,
    -                "priority": first_task.get("priority", "medium"),
    -                "category": first_task.get("category", ""),
    -                "steps": first_task.get("steps", []),
    -                "worker": "sonnet",
    -            })
    -            await _log(user_id, run_id, "", "system")
    -            await _log(user_id, run_id,
    -                        f"ÔöüÔöüÔöü Task 1/{len(tasks)}: {first_name} ÔöüÔöüÔöü",
    -                        "system")
    -            await _log(user_id, run_id,
    -                        f"­ƒºá [Sonnet] Planning task 1ÔÇª", "thinking")
    +            state["_plan_pool"] = plan_pool
    +            state["_remediation_pool"] = remediation_pool
     
    -            current_plan, plan_usage = await _plan_task_with_llm(
    -                user_id, run_id, repo_name, stack_profile, first_task,
    -                api_key=sonnet_worker.api_key, model=sonnet_worker.model,
    -            )
    -            p_in = plan_usage.get("input_tokens", 0)
    -            p_out = plan_usage.get("output_tokens", 0)
    -            tokens.add("sonnet", p_in, p_out)
    -            await _emit(user_id, "upgrade_token_tick", {
    -                "run_id": run_id, **tokens.snapshot()})
    +            # ÔöÇÔöÇ Sonnet planner coroutine ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +            async def _sonnet_planner() -> None:
    +                nonlocal _rem_seq
     
    -            # Narrate after first plan completes
    -            if narrator_enabled and current_plan:
    -                plan_files = current_plan.get("plan", [])
    -                file_list = ", ".join(p.get("file", "?") for p in plan_files[:5])
    -                analysis = current_plan.get("analysis", "")
    -                asyncio.create_task(_narrate(
    -                    user_id, run_id,
    -                    f"Sonnet just finished planning task 1: '{first_name}'. "
    -                    f"{analysis} "
    -                    f"Files identified: {file_list}. "
    -                    f"Now handing off to Opus to write the code.",
    -                    narrator_key=narrator_key, narrator_model=narrator_model,
    -                    tokens=tokens,
    -                ))
    -
    -            # Pipeline loop
    -            for task_index in range(len(tasks)):
    -                # Check /stop
    -                if state["_stop_flag"].is_set():
    -                    await _log(user_id, run_id,
    -                                "­ƒøæ Stopped by user.", "system")
    -                    break
    -                # Honour /pause
    -                if not state["_pause_event"].is_set():
    -                    await _log(user_id, run_id,
    -                                "ÔÅ©´©Å  Paused ÔÇö waiting for /resumeÔÇª",
    -                                "system")
    -                    await state["_pause_event"].wait()
    +                for i, task in enumerate(tasks):
                         if state["_stop_flag"].is_set():
                             break
    -
    -                task = tasks[task_index]
    -                task_id = task.get("id", f"TASK-{task_index}")
    -                task_name = (f"{task.get('from_state', '?')} ÔåÆ "
    -                             f"{task.get('to_state', '?')}")
    -
    -                # Announce Opus coding this task
    -                await _log(user_id, run_id,
    -                            f"ÔÜí [Opus] Writing code for task "
    -                            f"{task_index + 1}ÔÇª", "thinking")
    -
    -                # Show what Opus will work on (immediate feedback)
    -                if current_plan:
    -                    _plan_files = current_plan.get("plan", [])
    -                    if _plan_files:
    -                        _fnames = [p.get("file", "?") for p in _plan_files[:6]]
    -                        _extra = (f" +{len(_plan_files) - 6} more"
    -                                  if len(_plan_files) > 6 else "")
    -                        await _log(
    -                            user_id, run_id,
    -                            f"  ­ƒôû [Opus] Reading {len(_plan_files)} file(s): "
    -                            f"{', '.join(_fnames)}{_extra}",
    -                            "thinking",
    -                        )
    -
    -                # Build parallel coroutines
    -                has_next = task_index + 1 < len(tasks)
    -
    -                async def _opus_code(
    -                    _task: dict = task,
    -                    _plan: dict | None = current_plan,
    -                ) -> tuple[dict | None, dict]:
    -                    return await _build_task_with_llm(
    -                        user_id, run_id, repo_name, stack_profile,
    -                        _task, _plan,
    -                        api_key=opus_worker.api_key,
    -                        model=opus_worker.model,
    -                        working_dir=state.get("working_dir", ""),
    +                    if not state["_pause_event"].is_set():
    +                        await state["_pause_event"].wait()
    +                        if state["_stop_flag"].is_set():
    +                            break
    +
    +                    task_id = task.get("id", f"TASK-{i}")
    +                    task_name = (
    +                        f"{task.get('from_state', '?')} ÔåÆ "
    +                        f"{task.get('to_state', '?')}"
                         )
     
    -                async def _sonnet_plan_next(
    -                    _next_task: dict,
    -                    _next_idx: int,
    -                ) -> tuple[dict | None, dict]:
    -                    next_id = _next_task.get("id", f"TASK-{_next_idx}")
    -                    next_name = (
    -                        f"{_next_task.get('from_state', '?')} ÔåÆ "
    -                        f"{_next_task.get('to_state', '?')}"
    -                    )
                         await _emit(user_id, "upgrade_task_start", {
                             "run_id": run_id,
    -                        "task_id": next_id,
    -                        "task_index": _next_idx,
    -                        "task_name": next_name,
    -                        "priority": _next_task.get("priority", "medium"),
    -                        "category": _next_task.get("category", ""),
    -                        "steps": _next_task.get("steps", []),
    +                        "task_id": task_id,
    +                        "task_index": i,
    +                        "task_name": task_name,
    +                        "priority": task.get("priority", "medium"),
    +                        "category": task.get("category", ""),
    +                        "steps": task.get("steps", []),
                             "worker": "sonnet",
                         })
                         await _log(user_id, run_id, "", "system")
                         await _log(user_id, run_id,
    -                                f"ÔöüÔöüÔöü Task {_next_idx + 1}/{len(tasks)}: "
    -                                f"{next_name} ÔöüÔöüÔöü", "system")
    +                                f"ÔöüÔöüÔöü Task {i + 1}/{len(tasks)}: "
    +                                f"{task_name} ÔöüÔöüÔöü", "system")
                         await _log(user_id, run_id,
                                     f"­ƒºá [Sonnet] Planning task "
    -                                f"{_next_idx + 1}ÔÇª", "thinking")
    -                    return await _plan_task_with_llm(
    +                                f"{i + 1}ÔÇª", "thinking")
    +
    +                    plan_result, plan_usage = await _plan_task_with_llm(
                             user_id, run_id, repo_name, stack_profile,
    -                        _next_task,
    +                        task,
                             api_key=sonnet_worker.api_key,
                             model=sonnet_worker.model,
                         )
    +                    p_in = plan_usage.get("input_tokens", 0)
    +                    p_out = plan_usage.get("output_tokens", 0)
    +                    tokens.add("sonnet", p_in, p_out)
    +                    await _emit(user_id, "upgrade_token_tick", {
    +                        "run_id": run_id, **tokens.snapshot()})
    +
    +                    # Log plan details
    +                    if plan_result:
    +                        analysis = plan_result.get("analysis", "")
    +                        if analysis:
    +                            await _log(user_id, run_id,
    +                                        f"­ƒºá [Sonnet] {analysis}",
    +                                        "thinking")
    +                        plan_files = plan_result.get("plan", [])
    +                        if plan_files:
    +                            await _log(
    +                                user_id, run_id,
    +                                f"­ƒôï [Sonnet] Identified "
    +                                f"{len(plan_files)} file(s):",
    +                                "info",
    +                            )
    +                            for pf in plan_files:
    +                                await _log(
    +                                    user_id, run_id,
    +                                    f"  ­ƒôä {pf.get('file', '?')} ÔÇö "
    +                                    f"{pf.get('description', '')}",
    +                                    "info",
    +                                )
    +                        for risk in plan_result.get("risks", []):
    +                            await _log(user_id, run_id,
    +                                        f"  ÔÜá [Sonnet] {risk}", "warn")
     
    -                # Run Opus + (optionally) Sonnet in parallel
    -                if has_next:
    -                    next_task = tasks[task_index + 1]
    -                    next_idx = task_index + 1
    -                    (code_result, code_usage), (next_plan, next_plan_usage) = (
    -                        await asyncio.gather(
    -                            _opus_code(),
    -                            _sonnet_plan_next(next_task, next_idx),
    +                    # Push to pool ÔÇö Opus will pick it up when ready
    +                    pool_item = _PlanPoolItem(
    +                        i, task, plan_result, plan_usage)
    +                    await plan_pool.put(pool_item)
    +                    pool_depth = plan_pool.qsize()
    +                    await _log(user_id, run_id,
    +                                f"­ƒôÑ [Sonnet] Plan for task {i + 1} "
    +                                f"queued (pool depth: {pool_depth})",
    +                                "info")
    +                    await _emit(user_id, "plan_pool_update", {
    +                        "run_id": run_id,
    +                        "action": "push",
    +                        "task_index": i,
    +                        "pool_depth": pool_depth,
    +                    })
    +
    +                    # Fire narration (non-blocking)
    +                    if narrator_enabled and plan_result:
    +                        _pf = plan_result.get("plan", [])
    +                        _fl = ", ".join(
    +                            p.get("file", "?") for p in _pf[:5])
    +                        _an = plan_result.get("analysis", "")
    +                        asyncio.create_task(_narrate(
    +                            user_id, run_id,
    +                            f"Sonnet planned task {i + 1}: "
    +                            f"'{task_name}'. {_an} "
    +                            f"Files: {_fl}. "
    +                            f"Plan queued (pool: {pool_depth}).",
    +                            narrator_key=narrator_key,
    +                            narrator_model=narrator_model,
    +                            tokens=tokens,
    +                        ))
    +
    +                # Sentinel ÔÇö tells Opus no more plans are coming
    +                await plan_pool.put(None)
    +
    +                # ÔöÇÔöÇ Remediation mode ÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇÔöÇ
    +                await _log(user_id, run_id,
    +                            "­ƒºá [Sonnet] All tasks planned ÔÇö switching "
    +                            "to audit remediation mode", "system")
    +
    +                idle_cycles = 0
    +                while not state["_stop_flag"].is_set():
    +                    failures = [
    +                        r for r in state.get("audit_results", [])
    +                        if r.get("verdict") == "FAIL"
    +                        and not r.get("_remediation_queued")
    +                    ]
    +                    if not failures:
    +                        idle_cycles += 1
    +                        if idle_cycles > 5:
    +                            break
    +                        await asyncio.sleep(2.0)
    +                        continue
    +                    idle_cycles = 0
    +
    +                    for failure in failures:
    +                        if state["_stop_flag"].is_set():
    +                            break
    +                        failure["_remediation_queued"] = True
    +                        fp = failure.get("file", "?")
    +                        fi = failure.get("findings", [])
    +                        tid = failure.get("task_id", "")
    ... (499 lines truncated, 999 total)
