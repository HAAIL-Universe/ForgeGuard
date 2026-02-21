"""Build service sub-package — decomposed from build_service.py (R7).

Sub-modules:
    _state              — shared in-memory state, constants, small helpers
    cost                — cost gate / circuit breaker
    context             — context construction, conversation compaction
    planner             — per-phase build execution utilities (file generation, recovery)
    planner_agent_loop  — per-phase planner agent (manifest + chunk planning)
    verification        — inline audit, per-file audit, governance checks
    subagent            — sub-agent handoff protocol, per-role tool sets, runner

Project-level planning (what phases to build) is handled by:
    app/services/planner_service.py  — wraps Z:/ForgeCollection/planner/
"""
