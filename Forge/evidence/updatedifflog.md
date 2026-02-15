# Diff Log (overwrite each cycle)

## Cycle Metadata
- Timestamp: 2026-02-15T22:12:29+00:00
- Branch: master
- HEAD: 8de5071897732b10d5c24dd04a739ed698af09e9
- BASE_HEAD: de182ed267acb4a207dd5c40ae571a2614fe1bc2
- Diff basis: staged

## Cycle Status
- Status: COMPLETE

## Summary
- Removed Haiku from pricing table
- Updated system prompt for per-phase planning
- plan_tasks reset between phases
- build_overview WS event at build start
- phase_plan replaces build_plan event
- Frontend phase overview bar
- Updated physics.yaml with new events

## Files Changed (staged)
- Forge/Contracts/physics.yaml
- app/api/routers/projects.py
- app/services/build_service.py
- app/services/project_service.py
- tests/test_build_service.py
- tests/test_project_service.py
- tests/test_projects_router.py
- web/src/components/ContractProgress.tsx
- web/src/pages/BuildProgress.tsx

## git status -sb
    ## master...origin/master [ahead 9]
    M  Forge/Contracts/physics.yaml
    M  app/api/routers/projects.py
    M  app/services/build_service.py
    M  app/services/project_service.py
    M  tests/test_build_service.py
    M  tests/test_project_service.py
    M  tests/test_projects_router.py
    M  web/src/components/ContractProgress.tsx
    M  web/src/pages/BuildProgress.tsx

## Verification
- pytest: 350 backend pass
- vitest: 61 frontend pass
- Total: 411 tests pass

## Notes (optional)
- No Haiku references remain in code

## Next Steps
- Phase 17: Recovery Planner

## Minimal Diff Hunks
    diff --git a/Forge/Contracts/physics.yaml b/Forge/Contracts/physics.yaml
    index 6c6e4d9..0e8ea04 100644
    --- a/Forge/Contracts/physics.yaml
    +++ b/Forge/Contracts/physics.yaml
    @@ -163,8 +163,12 @@ paths:
             payload: AuditRunSummary
             type: "file_created"
             payload: BuildFileEvent
    +        type: "build_overview"
    +        payload: BuildOverview
             type: "build_plan"
             payload: BuildPlan
    +        type: "phase_plan"
    +        payload: BuildPlan
             type: "plan_task_complete"
             payload: PlanTaskUpdate
             type: "build_turn"
    @@ -517,6 +521,12 @@ schemas:
         model: string
         estimated_cost_usd: float
     
    +  BuildOverview:
    +    phases:
    +      - number: integer
    +        name: string
    +        objective: string
    +
       BuildPlan:
         tasks: BuildPlanTask[]
     
    diff --git a/app/api/routers/projects.py b/app/api/routers/projects.py
    index cc00763..ca1c800 100644
    --- a/app/api/routers/projects.py
    +++ b/app/api/routers/projects.py
    @@ -8,6 +8,7 @@ from pydantic import BaseModel, Field
     
     from app.api.deps import get_current_user
     from app.services.project_service import (
    +    cancel_contract_generation,
         create_new_project,
         delete_user_project,
         generate_contracts,
    @@ -216,6 +217,25 @@ async def gen_contracts(
         return {"contracts": contracts}
     
     
    +@router.post("/{project_id}/contracts/cancel")
    +async def cancel_contracts(
    +    project_id: UUID,
    +    current_user: dict = Depends(get_current_user),
    +) -> dict:
    +    """Cancel an in-progress contract generation."""
    +    try:
    +        result = await cancel_contract_generation(current_user["id"], project_id)
    +    except ValueError as exc:
    +        detail = str(exc)
    +        code = (
    +            status.HTTP_404_NOT_FOUND
    +            if "not found" in detail.lower()
    +            else status.HTTP_400_BAD_REQUEST
    +        )
    +        raise HTTPException(status_code=code, detail=detail)
    +    return result
    +
    +
     @router.get("/{project_id}/contracts")
     async def list_project_contracts(
         project_id: UUID,
    diff --git a/app/services/build_service.py b/app/services/build_service.py
    index 1983ded..db4933c 100644
    --- a/app/services/build_service.py
    +++ b/app/services/build_service.py
    @@ -229,8 +229,6 @@ _MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
         # (input $/token, output $/token)
         "claude-opus-4":       (Decimal("0.000015"),  Decimal("0.000075")),   # $15 / $75 per 1M
         "claude-sonnet-4":     (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
    -    "claude-haiku-4":      (Decimal("0.000001"),  Decimal("0.000005")),   # $1 / $5 per 1M (legacy)
    -    "claude-sonnet-4":     (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
         "claude-3-5-sonnet":   (Decimal("0.000003"),  Decimal("0.000015")),   # $3 / $15 per 1M
     }
     # Fallback: Opus pricing (most expensive = safest default)
    @@ -679,16 +677,31 @@ async def _run_build(
             # System prompt for the builder agent
             system_prompt = (
                 "You are an autonomous software builder operating under the Forge governance framework.\n\n"
    -            "At the start of your first response, emit a structured build plan:\n"
    +            "At the start of EACH PHASE, emit a structured plan covering only that phase's deliverables:\n"
                 "=== PLAN ===\n"
    -            "1. First task\n"
    -            "2. Second task\n"
    +            "1. First task for this phase\n"
    +            "2. Second task for this phase\n"
                 "...\n"
                 "=== END PLAN ===\n\n"
    +            "Do NOT plan ahead to future phases. Each phase gets its own fresh plan.\n\n"
                 "As you complete each task, emit: === TASK DONE: N ===\n"
    -            "where N is the task number from your plan.\n"
    +            "where N is the task number from your current phase plan.\n"
             )
     
    +        # Emit build overview (high-level phase list) at build start
    +        try:
    +            phases_contract = await project_repo.get_contract_by_type(project_id, "phases")
    +            if phases_contract:
    +                overview_phases = _parse_phases_contract(phases_contract["content"])
    +                await _broadcast_build_event(user_id, build_id, "build_overview", {
    +                    "phases": [
    +                        {"number": p["number"], "name": p["name"], "objective": p.get("objective", "")}
    +                        for p in overview_phases
    +                    ],
    +                })
    +        except Exception:
    +            logger.debug("Could not emit build_overview", exc_info=True)
    +
             # Multi-turn conversation loop
             while True:
                 turn_count += 1
    @@ -795,13 +808,14 @@ async def _run_build(
                         if parsed_plan:
                             plan_tasks = parsed_plan
                             await _broadcast_build_event(
    -                            user_id, build_id, "build_plan", {
    +                            user_id, build_id, "phase_plan", {
    +                                "phase": current_phase,
                                     "tasks": plan_tasks,
                                 }
                             )
                             await build_repo.append_build_log(
                                 build_id,
    -                            f"Build plan detected: {len(plan_tasks)} tasks",
    +                            f"Phase plan detected for {current_phase}: {len(plan_tasks)} tasks",
                                 source="system", level="info",
                             )
     
    @@ -929,6 +943,7 @@ async def _run_build(
                         phase_start_time = datetime.now(timezone.utc)
                         phase_loop_count = 0
                         accumulated_text = ""
    +                    plan_tasks = []  # Fresh plan for next phase
     
                         # Record cost for this phase
                         await _record_phase_cost(build_id, current_phase, usage)
    diff --git a/app/services/project_service.py b/app/services/project_service.py
    index 6994dd6..16b823a 100644
    --- a/app/services/project_service.py
    +++ b/app/services/project_service.py
    @@ -23,6 +23,9 @@ from app.repos.project_repo import (
     
     logger = logging.getLogger(__name__)
     
    +# Active contract generation tasks ÔÇö checked between contracts for cancellation
    +_active_generations: set[str] = set()
    +
     # ---------------------------------------------------------------------------
     # Questionnaire definitions
     # ---------------------------------------------------------------------------
    @@ -430,52 +433,96 @@ async def generate_contracts(
             llm_api_key = settings.ANTHROPIC_API_KEY
             llm_model = settings.LLM_QUESTIONNAIRE_MODEL
     
    +    pid = str(project_id)
    +    _active_generations.add(pid)
    +
         generated = []
         total = len(CONTRACT_TYPES)
    -    for idx, contract_type in enumerate(CONTRACT_TYPES):
    -        # Notify client that generation of this contract has started
    -        await manager.send_to_user(str(user_id), {
    -            "type": "contract_progress",
    -            "payload": {
    -                "project_id": str(project_id),
    -                "contract_type": contract_type,
    -                "status": "generating",
    -                "index": idx,
    -                "total": total,
    -            },
    -        })
    -
    -        content, usage = await _generate_contract_content(
    -            contract_type, project, answers_text, llm_api_key, llm_model, provider
    -        )
    -        row = await upsert_contract(project_id, contract_type, content)
    -        generated.append({
    -            "id": str(row["id"]),
    -            "project_id": str(row["project_id"]),
    -            "contract_type": row["contract_type"],
    -            "version": row["version"],
    -            "created_at": row["created_at"],
    -            "updated_at": row["updated_at"],
    -        })
    -
    -        # Notify client that this contract is done
    -        await manager.send_to_user(str(user_id), {
    -            "type": "contract_progress",
    -            "payload": {
    -                "project_id": str(project_id),
    -                "contract_type": contract_type,
    -                "status": "done",
    -                "index": idx,
    -                "total": total,
    -                "input_tokens": usage.get("input_tokens", 0),
    -                "output_tokens": usage.get("output_tokens", 0),
    -            },
    -        })
    +    try:
    +        for idx, contract_type in enumerate(CONTRACT_TYPES):
    +            # Check cancellation between contracts
    +            if pid not in _active_generations:
    +                logger.info("Contract generation cancelled for project %s", pid)
    +                await manager.send_to_user(str(user_id), {
    +                    "type": "contract_progress",
    +                    "payload": {
    +                        "project_id": pid,
    +                        "contract_type": contract_type,
    +                        "status": "cancelled",
    +                        "index": idx,
    +                        "total": total,
    +                    },
    +                })
    +                raise ValueError("Contract generation cancelled")
    +
    +            # Notify client that generation of this contract has started
    +            await manager.send_to_user(str(user_id), {
    +                "type": "contract_progress",
    +                "payload": {
    +                    "project_id": pid,
    +                    "contract_type": contract_type,
    +                    "status": "generating",
    +                    "index": idx,
    +                    "total": total,
    +                },
    +            })
    +
    +            content, usage = await _generate_contract_content(
    +                contract_type, project, answers_text, llm_api_key, llm_model, provider
    +            )
    +            row = await upsert_contract(project_id, contract_type, content)
    +            generated.append({
    +                "id": str(row["id"]),
    +                "project_id": str(row["project_id"]),
    +                "contract_type": row["contract_type"],
    +                "version": row["version"],
    +                "created_at": row["created_at"],
    +                "updated_at": row["updated_at"],
    +            })
    +
    +            # Notify client that this contract is done
    +            await manager.send_to_user(str(user_id), {
    +                "type": "contract_progress",
    +                "payload": {
    +                    "project_id": pid,
    +                    "contract_type": contract_type,
    +                    "status": "done",
    +                    "index": idx,
    +                    "total": total,
    +                    "input_tokens": usage.get("input_tokens", 0),
    +                    "output_tokens": usage.get("output_tokens", 0),
    +                },
    +            })
    +    finally:
    +        _active_generations.discard(pid)
     
         await update_project_status(project_id, "contracts_ready")
         return generated
     
     
    +async def cancel_contract_generation(
    +    user_id: UUID,
    +    project_id: UUID,
    +) -> dict:
    +    """Cancel an in-progress contract generation.
    +
    +    Removes the project from the active set so the generation loop
    +    stops at the next contract boundary.
    +    """
    +    pid = str(project_id)
    +    if pid not in _active_generations:
    +        raise ValueError("No active contract generation for this project")
    +
    +    # Verify ownership
    +    project = await get_project_by_id(project_id)
    +    if not project or str(project["user_id"]) != str(user_id):
    +        raise ValueError("Project not found")
    +
    +    _active_generations.discard(pid)
    +    logger.info("Contract generation cancel requested for project %s", pid)
    +    return {"status": "cancelling"}
    +
    +
     async def list_contracts(
         user_id: UUID,
         project_id: UUID,
    diff --git a/tests/test_build_service.py b/tests/test_build_service.py
    index b1d5e56..15869d0 100644
    --- a/tests/test_build_service.py
    +++ b/tests/test_build_service.py
    @@ -69,6 +69,7 @@ async def test_start_build_success(mock_get_user, mock_build_repo, mock_project_
         mock_project_repo.get_project_by_id = AsyncMock(return_value=_project())
         mock_project_repo.get_contracts_by_project = AsyncMock(return_value=_contracts())
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_build_repo.get_latest_build_for_project = AsyncMock(return_value=None)
         mock_build_repo.create_build = AsyncMock(return_value=_build())
         mock_create_task.return_value = MagicMock()
    @@ -488,9 +489,9 @@ def test_get_token_rates_model_aware():
         assert opus_in == Decimal("0.000015")
         assert opus_out == Decimal("0.000075")
     
    -    haiku_in, haiku_out = build_service._get_token_rates("claude-haiku-4-5-20251001")
    -    assert haiku_in == Decimal("0.000001")
    -    assert haiku_out == Decimal("0.000005")
    +    sonnet_in, sonnet_out = build_service._get_token_rates("claude-sonnet-4-5-20251001")
    +    assert sonnet_in == Decimal("0.000003")
    +    assert sonnet_out == Decimal("0.000015")
     
         # Unknown model falls back to Opus (safest = most expensive)
         unk_in, unk_out = build_service._get_token_rates("some-unknown-model")
    @@ -1005,6 +1006,7 @@ async def test_run_build_multi_turn_plan_detected(
         })
         mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         # Mock audit to pass
    @@ -1014,10 +1016,10 @@ async def test_run_build_multi_turn_plan_detected(
                 "sk-ant-test", audit_llm_enabled=True,
             )
     
    -    # Check that build_plan event was broadcast
    +    # Check that phase_plan event was broadcast
         plan_calls = [
             c for c in mock_manager.send_to_user.call_args_list
    -        if c[0][1].get("type") == "build_plan"
    +        if c[0][1].get("type") == "phase_plan"
         ]
         assert len(plan_calls) >= 1
         plan_payload = plan_calls[0][0][1]["payload"]
    @@ -1056,6 +1058,7 @@ async def test_run_build_multi_turn_audit_feedback(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         # First audit fails, second passes, then build finishes on turn 3
    @@ -1101,6 +1104,7 @@ async def test_run_build_multi_turn_max_failures(
         mock_build_repo.pause_build = AsyncMock(return_value=True)
         mock_build_repo.resume_build = AsyncMock(return_value=True)
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         async def _auto_abort():
    @@ -1161,6 +1165,7 @@ async def test_run_build_turn_event_broadcast(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value="PASS"):
    @@ -1198,6 +1203,7 @@ async def test_run_build_task_done_broadcast(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value="PASS"):
    @@ -1250,6 +1256,7 @@ async def test_run_build_context_compaction(
             "total_input_tokens": 160000, "total_output_tokens": 160000, "total_cost_usd": Decimal("0.50"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         # All audits pass ÔÇö each turn adds assistant message, growing len(messages) past 5
    @@ -1302,6 +1309,7 @@ async def test_run_build_pauses_at_threshold(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         # All audits fail ÔåÆ triggers pause after PAUSE_THRESHOLD
    @@ -1371,6 +1379,7 @@ async def test_run_build_pause_resume_retry(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         async def _audit(*a, **k):
    @@ -1434,6 +1443,7 @@ async def test_run_build_pause_skip(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         async def _auto_resume():
    @@ -1485,6 +1495,7 @@ async def test_run_build_interjection_injected(
             "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
         })
         mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
         mock_manager.send_to_user = AsyncMock()
     
         # Pre-populate interjection queue
    @@ -1718,3 +1729,121 @@ def test_build_pause_timeout_default():
         from app.config import settings as s
         assert isinstance(s.BUILD_PAUSE_TIMEOUT_MINUTES, int)
         assert s.BUILD_PAUSE_TIMEOUT_MINUTES >= 1
    +
    +
    +# ---------------------------------------------------------------------------
    +# Tests: Per-phase planning & plan_tasks reset (Phase 16)
    +# ---------------------------------------------------------------------------
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.manager")
    +@patch("app.services.build_service.project_repo")
    +@patch("app.services.build_service.build_repo")
    +@patch("app.services.build_service.stream_agent")
    +async def test_plan_tasks_reset_between_phases(
    +    mock_stream, mock_build_repo, mock_project_repo, mock_manager
    +):
    +    """plan_tasks resets between phases so each phase gets a fresh plan."""
    +    call_counter = {"n": 0}
    +
    +    async def _stream_multi_phase(*args, **kwargs):
    +        call_counter["n"] += 1
    +        if call_counter["n"] == 1:
    +            yield (
    +                "=== PLAN ===\n1. Phase 0 task A\n2. Phase 0 task B\n=== END PLAN ===\n"
    +                "=== TASK DONE: 1 ===\n=== TASK DONE: 2 ===\n"
    +                "Phase: Phase 0 -- Genesis\n=== PHASE SIGN-OFF: PASS ===\n"
    +            )
    +        elif call_counter["n"] == 2:
    +            yield (
    +                "=== PLAN ===\n1. Phase 1 task X\n=== END PLAN ===\n"
    +                "=== TASK DONE: 1 ===\n"
    +                "Phase: Phase 1 -- Scaffold\n=== PHASE SIGN-OFF: PASS ===\n"
    +            )
    +        else:
    +            yield "Build complete."
    +
    +    mock_stream.side_effect = _stream_multi_phase
    +    mock_build_repo.update_build_status = AsyncMock()
    +    mock_build_repo.append_build_log = AsyncMock()
    +    mock_build_repo.record_build_cost = AsyncMock()
    +    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
    +        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    +    })
    +    mock_build_repo.increment_loop_count = AsyncMock(return_value=1)
    +    mock_project_repo.update_project_status = AsyncMock()
    +    mock_project_repo.get_contract_by_type = AsyncMock(return_value=None)
    +    mock_manager.send_to_user = AsyncMock()
    +
    +    with patch.object(build_service, "_run_inline_audit", new_callable=AsyncMock, return_value="PASS"):
    +        await build_service._run_build(
    +            _BUILD_ID, _PROJECT_ID, _USER_ID, _contracts(),
    +            "sk-ant-test", audit_llm_enabled=True,
    +        )
    +
    +    # Should have emitted TWO phase_plan events (one per phase)
    +    plan_calls = [
    +        c for c in mock_manager.send_to_user.call_args_list
    +        if c[0][1].get("type") == "phase_plan"
    +    ]
    +    assert len(plan_calls) == 2
    +
    +    # Phase 0 plan had 2 tasks
    +    assert len(plan_calls[0][0][1]["payload"]["tasks"]) == 2
    +    assert plan_calls[0][0][1]["payload"]["tasks"][0]["title"] == "Phase 0 task A"
    +
    +    # Phase 1 plan had 1 task (fresh, not carried over)
    +    assert len(plan_calls[1][0][1]["payload"]["tasks"]) == 1
    +    assert plan_calls[1][0][1]["payload"]["tasks"][0]["title"] == "Phase 1 task X"
    +
    +
    +@pytest.mark.asyncio
    +@patch("app.services.build_service.manager")
    +@patch("app.services.build_service.project_repo")
    +@patch("app.services.build_service.build_repo")
    +@patch("app.services.build_service.stream_agent")
    +async def test_build_overview_emitted(
    +    mock_stream, mock_build_repo, mock_project_repo, mock_manager
    +):
    +    """_run_build emits build_overview event when phases contract is available."""
    +    _reset_stream_counter()
    +    mock_stream.side_effect = _fake_stream_pass
    +    mock_build_repo.update_build_status = AsyncMock()
    +    mock_build_repo.append_build_log = AsyncMock()
    +    mock_build_repo.record_build_cost = AsyncMock()
    +    mock_build_repo.get_build_cost_summary = AsyncMock(return_value={
    +        "total_input_tokens": 100, "total_output_tokens": 200, "total_cost_usd": Decimal("0.01"),
    +    })
    ... (417 lines truncated, 917 total)
