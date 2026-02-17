"""Build service sub-package — decomposed from build_service.py (R7).

Sub-modules:
    _state       — shared in-memory state, constants, small helpers
    cost         — cost gate / circuit breaker
    context      — context construction, conversation compaction
    planner      — phase planning, LLM plan generation, file manifests
    verification — inline audit, per-file audit, governance checks
"""
