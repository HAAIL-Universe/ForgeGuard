"""Tests for coherence fixes — exports pipeline, CODER/AUDITOR prompts, trivial files."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fix 1: Phase planner fallback — exports in schema + enrichment + backfill
# ---------------------------------------------------------------------------

class TestPhasePlannerPrompt:
    """Verify phase planner system prompt instructs agent to emit depends_on + exports."""

    def test_system_prompt_mentions_depends_on(self):
        """Phase planner prompt must instruct agent to set depends_on."""
        from app.services.build.planner_agent_loop import _build_system_prompt

        blocks = _build_system_prompt()
        full_prompt = " ".join(b["text"] for b in blocks)
        assert "depends_on" in full_prompt
        assert "Models before services" in full_prompt

    def test_system_prompt_mentions_exports(self):
        """Phase planner prompt must instruct agent to set exports."""
        from app.services.build.planner_agent_loop import _build_system_prompt

        blocks = _build_system_prompt()
        full_prompt = " ".join(b["text"] for b in blocks)
        assert "exports" in full_prompt.lower()
        assert "PUBLIC API signatures" in full_prompt


class TestPhasePlannerExports:
    """Verify phase planner schema and enrichment preserve exports."""

    def test_write_phase_plan_schema_includes_exports(self):
        """write_phase_plan tool schema must include the 'exports' field."""
        from app.services.build.planner_agent_loop import _TOOL_DEFINITIONS as TOOL_DEFINITIONS

        write_plan_tool = None
        for tool in TOOL_DEFINITIONS:
            if tool.get("name") == "write_phase_plan":
                write_plan_tool = tool
                break

        assert write_plan_tool is not None, "write_phase_plan tool not found"
        manifest_items = (
            write_plan_tool["input_schema"]["properties"]["manifest"]["items"]
        )
        props = manifest_items["properties"]
        assert "exports" in props, "exports field missing from write_phase_plan schema"
        assert props["exports"]["type"] == "array"

    def test_validate_and_enrich_plan_preserves_exports(self):
        """_validate_and_enrich_plan must carry exports through."""
        from app.services.build.planner_agent_loop import _validate_and_enrich_plan

        raw_manifest = [
            {
                "path": "app/models/timer.py",
                "action": "create",
                "purpose": "Timer data model",
                "depends_on": [],
                "exports": [
                    "class Timer(BaseModel): id, name",
                    "enum TimerStatus: ACTIVE, PAUSED",
                ],
                "estimated_lines": 50,
            },
            {
                "path": "app/services/timer_service.py",
                "action": "create",
                "purpose": "Timer business logic",
                "depends_on": ["app/models/timer.py"],
                "exports": [
                    "class TimerService.__init__(self, repo)",
                    "def TimerService.create_timer(self, duration: int) -> Timer",
                ],
                "estimated_lines": 80,
            },
        ]
        raw_chunks = [
            {"name": "Chunk 1", "files": ["app/models/timer.py", "app/services/timer_service.py"]},
        ]

        manifest, chunks, errors = _validate_and_enrich_plan(raw_manifest, raw_chunks)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(manifest) == 2

        # timer.py exports preserved
        timer_entry = next(m for m in manifest if m["path"] == "app/models/timer.py")
        assert len(timer_entry["exports"]) == 2
        assert "class Timer(BaseModel): id, name" in timer_entry["exports"]

        # timer_service.py exports preserved
        service_entry = next(m for m in manifest if m["path"] == "app/services/timer_service.py")
        assert len(service_entry["exports"]) == 2
        assert service_entry["depends_on"] == ["app/models/timer.py"]

    def test_validate_and_enrich_plan_defaults_exports_to_empty(self):
        """Entries without exports should get an empty list."""
        from app.services.build.planner_agent_loop import _validate_and_enrich_plan

        raw_manifest = [
            {
                "path": "app/__init__.py",
                "action": "create",
                "purpose": "Package marker",
            },
        ]
        raw_chunks = [
            {"name": "Chunk 1", "files": ["app/__init__.py"]},
        ]

        manifest, chunks, errors = _validate_and_enrich_plan(raw_manifest, raw_chunks)

        assert not errors
        assert manifest[0]["exports"] == []


# ---------------------------------------------------------------------------
# Fix 1c: Exports + depends_on backfill from ALL phases' file_manifests
# ---------------------------------------------------------------------------

class TestExportsBackfill:
    """Verify exports and depends_on are backfilled from ALL phases when phase planner omits them."""

    @staticmethod
    def _run_backfill(manifest, phases):
        """Simulate the backfill logic from build_service.py."""
        _all_proj_files = []
        for _p in phases:
            _all_proj_files.extend(_p.get("file_manifest", []))
        if _all_proj_files:
            _proj_by_path = {f["path"]: f for f in _all_proj_files}
            for _entry in manifest:
                _proj_entry = _proj_by_path.get(_entry["path"], {})
                if not _entry.get("exports") and _proj_entry.get("exports"):
                    _entry["exports"] = _proj_entry["exports"]
                if not _entry.get("depends_on") and _proj_entry.get("depends_on"):
                    _entry["depends_on"] = _proj_entry["depends_on"]

    def test_backfill_exports_from_project_plan(self):
        """Phase planner output without exports gets backfilled from project plan."""
        phase_planner_manifest = [
            {"path": "app/models/timer.py", "action": "create", "purpose": "Timer model"},
            {"path": "app/services/timer_service.py", "action": "create", "purpose": "Service"},
        ]
        # File manifests spread across multiple phases
        phases = [
            {"file_manifest": [
                {
                    "path": "app/models/timer.py",
                    "exports": ["class Timer(BaseModel): id, name", "enum TimerStatus"],
                    "depends_on": [],
                },
            ]},
            {"file_manifest": [
                {
                    "path": "app/services/timer_service.py",
                    "exports": ["class TimerService.__init__(self, repo)"],
                    "depends_on": ["app/models/timer.py"],
                },
            ]},
        ]

        self._run_backfill(phase_planner_manifest, phases)

        assert phase_planner_manifest[0]["exports"] == [
            "class Timer(BaseModel): id, name", "enum TimerStatus"
        ]
        assert phase_planner_manifest[1]["exports"] == [
            "class TimerService.__init__(self, repo)"
        ]

    def test_backfill_depends_on_from_project_plan(self):
        """Phase planner output without depends_on gets backfilled."""
        phase_planner_manifest = [
            {"path": "app/services/timer_service.py", "action": "create", "purpose": "Service"},
        ]
        phases = [
            {"file_manifest": [
                {
                    "path": "app/services/timer_service.py",
                    "depends_on": ["app/models/timer.py", "app/repos/timer_repo.py"],
                    "exports": ["class TimerService"],
                },
            ]},
        ]

        self._run_backfill(phase_planner_manifest, phases)

        assert phase_planner_manifest[0]["depends_on"] == [
            "app/models/timer.py", "app/repos/timer_repo.py"
        ]
        assert phase_planner_manifest[0]["exports"] == ["class TimerService"]

    def test_backfill_reads_all_phases(self):
        """Backfill should read file_manifests from ALL phases, not just current."""
        phase_planner_manifest = [
            {"path": "app/services/timer_service.py", "action": "create", "purpose": "Service"},
        ]
        # timer_service.py is in phase 0's manifest, but the current phase (1) is empty
        phases = [
            {"number": 0, "file_manifest": [
                {
                    "path": "app/services/timer_service.py",
                    "exports": ["class TimerService"],
                    "depends_on": ["app/models/timer.py"],
                },
            ]},
            {"number": 1, "file_manifest": []},  # current phase — empty!
        ]

        self._run_backfill(phase_planner_manifest, phases)

        assert phase_planner_manifest[0]["exports"] == ["class TimerService"]
        assert phase_planner_manifest[0]["depends_on"] == ["app/models/timer.py"]

    def test_backfill_does_not_overwrite_existing_exports(self):
        """If phase planner DID emit exports, don't overwrite them."""
        phase_planner_manifest = [
            {
                "path": "app/models/timer.py",
                "action": "create",
                "exports": ["class Timer(BaseModel): id"],  # Phase planner's version
            },
        ]
        phases = [
            {"file_manifest": [
                {
                    "path": "app/models/timer.py",
                    "exports": ["class Timer(BaseModel): id, name", "enum TimerStatus"],
                },
            ]},
        ]

        self._run_backfill(phase_planner_manifest, phases)

        # Phase planner's version should be kept
        assert phase_planner_manifest[0]["exports"] == ["class Timer(BaseModel): id"]

    def test_backfill_does_not_overwrite_existing_depends_on(self):
        """If phase planner DID emit depends_on, don't overwrite."""
        phase_planner_manifest = [
            {
                "path": "app/routers/timers.py",
                "action": "create",
                "depends_on": ["app/services/timer_service.py"],  # Phase planner's version
            },
        ]
        phases = [
            {"file_manifest": [
                {
                    "path": "app/routers/timers.py",
                    "depends_on": ["app/services/timer_service.py", "app/models/timer.py"],
                },
            ]},
        ]

        self._run_backfill(phase_planner_manifest, phases)

        # Phase planner's version should be kept
        assert phase_planner_manifest[0]["depends_on"] == ["app/services/timer_service.py"]


# ---------------------------------------------------------------------------
# Fix 2: CODER prompt includes dependency contract instructions
# ---------------------------------------------------------------------------

class TestCoderPrompt:
    """Verify CODER system prompt includes dependency contract rules."""

    def test_coder_prompt_mentions_dependency_contracts(self):
        """CODER prompt must reference dependency_contracts.md."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        coder_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "dependency_contracts.md" in coder_prompt
        assert "implementation_contract.md" in coder_prompt

    def test_coder_prompt_has_contract_rules_section(self):
        """CODER prompt must have DEPENDENCY CONTRACT RULES section."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        coder_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.CODER]
        assert "DEPENDENCY CONTRACT RULES" in coder_prompt
        assert "Do NOT redefine enums" in coder_prompt
        assert "__init__.py" in coder_prompt
        assert "double-up prefixes" in coder_prompt


# ---------------------------------------------------------------------------
# Fix 3: AUDITOR prompt + dep contracts passed to AUDITOR
# ---------------------------------------------------------------------------

class TestAuditorPrompt:
    """Verify AUDITOR system prompt and context include contract violations."""

    def test_auditor_prompt_checks_redefined_types(self):
        """AUDITOR should flag files that redefine dependency types."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        auditor_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "redefines a type/enum/class" in auditor_prompt

    def test_auditor_prompt_checks_wrong_class_name(self):
        """AUDITOR should flag wrong class names vs dependency contracts."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        auditor_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "wrong class/function name" in auditor_prompt

    def test_auditor_prompt_checks_init_prose(self):
        """AUDITOR should flag non-code __init__.py files."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        auditor_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "__init__.py contains non-code content" in auditor_prompt

    def test_auditor_prompt_checks_double_prefix(self):
        """AUDITOR should flag router double-prefix issues."""
        from app.services.build.subagent import _ROLE_SYSTEM_PROMPTS, SubAgentRole

        auditor_prompt = _ROLE_SYSTEM_PROMPTS[SubAgentRole.AUDITOR]
        assert "double-prefix" in auditor_prompt


# ---------------------------------------------------------------------------
# Fix 4: Trivial __init__.py with exports is NOT trivial
# ---------------------------------------------------------------------------

class TestTrivialFileDetection:
    """Verify _is_trivial_file correctly handles exports field."""

    def test_init_py_without_exports_is_trivial(self):
        """Plain __init__.py with no exports should be trivial."""
        from builder.builder_agent import _is_trivial_file

        assert _is_trivial_file("app/__init__.py", "Package marker") is True
        assert _is_trivial_file("app/__init__.py", "Package marker", []) is True
        assert _is_trivial_file("app/__init__.py", "Package marker", None) is True

    def test_init_py_with_exports_is_not_trivial(self):
        """__init__.py with planned exports needs real CODER generation."""
        from builder.builder_agent import _is_trivial_file

        assert _is_trivial_file(
            "app/__init__.py",
            "Package marker with re-exports",
            ["Timer", "TimerStatus"],
        ) is False

    def test_init_py_with_reexport_purpose_is_not_trivial(self):
        """__init__.py with re-export purpose should not be trivial."""
        from builder.builder_agent import _is_trivial_file

        assert _is_trivial_file(
            "app/models/__init__.py",
            "Barrel re-export of all model classes",
        ) is False

    def test_regular_file_is_never_trivial(self):
        """Non-trivial filenames should never be trivial."""
        from builder.builder_agent import _is_trivial_file

        assert _is_trivial_file("app/main.py", "App entrypoint") is False
        assert _is_trivial_file("app/services/timer.py", "Timer service") is False


# ---------------------------------------------------------------------------
# Dependency contract assembly (planner.py lines 1722-1746)
# ---------------------------------------------------------------------------

class TestDependencyContractAssembly:
    """Verify dependency_contracts.md and implementation_contract.md are assembled correctly."""

    def test_dependency_contract_built_from_exports(self):
        """When file has depends_on and deps have exports, dependency_contracts.md is created."""
        file_entry = {
            "path": "app/services/timer_service.py",
            "depends_on": ["app/models/timer.py", "app/repos/timer_repo.py"],
            "exports": ["class TimerService.__init__(self, repo)"],
        }
        file_entries_by_path = {
            "app/models/timer.py": {
                "path": "app/models/timer.py",
                "exports": ["class Timer(BaseModel): id, name", "enum TimerStatus"],
            },
            "app/repos/timer_repo.py": {
                "path": "app/repos/timer_repo.py",
                "exports": ["class TimerRepo.get(self, id) -> Timer | None"],
            },
        }

        # Simulate the assembly logic from planner.py lines 1722-1737
        _dep_contract_parts = []
        for dep in file_entry.get("depends_on", []):
            _dep_entry = file_entries_by_path.get(dep, {})
            _dep_exports = _dep_entry.get("exports", [])
            if _dep_exports:
                _dep_contract_parts.append(
                    f"### {dep}\n" + "\n".join(f"- {e}" for e in _dep_exports)
                )

        assert len(_dep_contract_parts) == 2
        assert "### app/models/timer.py" in _dep_contract_parts[0]
        assert "- class Timer(BaseModel): id, name" in _dep_contract_parts[0]
        assert "### app/repos/timer_repo.py" in _dep_contract_parts[1]

    def test_implementation_contract_built_from_own_exports(self):
        """When file has exports, implementation_contract.md is created."""
        file_entry = {
            "path": "app/services/timer_service.py",
            "exports": [
                "class TimerService.__init__(self, repo: TimerRepo)",
                "def TimerService.create_timer(self, duration: int) -> Timer",
            ],
        }

        _own_exports = file_entry.get("exports", [])
        context_files = {}
        if _own_exports:
            context_files["implementation_contract.md"] = (
                "# Implementation Contract\n"
                "This file MUST export the following interfaces exactly as specified:\n\n"
                + "\n".join(f"- {e}" for e in _own_exports)
            )

        assert "implementation_contract.md" in context_files
        content = context_files["implementation_contract.md"]
        assert "class TimerService.__init__(self, repo: TimerRepo)" in content
        assert "def TimerService.create_timer" in content

    def test_no_contracts_when_no_exports(self):
        """Files without depends_on/exports should NOT get contracts."""
        file_entry = {
            "path": "app/__init__.py",
            "depends_on": [],
            "exports": [],
        }
        file_entries_by_path = {}

        _dep_contract_parts = []
        for dep in file_entry.get("depends_on", []):
            _dep_entry = file_entries_by_path.get(dep, {})
            _dep_exports = _dep_entry.get("exports", [])
            if _dep_exports:
                _dep_contract_parts.append(f"### {dep}")

        _own_exports = file_entry.get("exports", [])

        assert len(_dep_contract_parts) == 0
        assert len(_own_exports) == 0

    def test_cross_chunk_dependency_resolution(self):
        """Dependencies in different chunks should still resolve exports."""
        # Chunk 0: timer.py, Chunk 1: timer_service.py
        phase_manifest = [
            {
                "path": "app/models/timer.py",
                "exports": ["class Timer(BaseModel)", "enum TimerStatus"],
            },
            {
                "path": "app/services/timer_service.py",
                "depends_on": ["app/models/timer.py"],
                "exports": ["class TimerService"],
            },
        ]
        tier_files = [  # chunk 1 only
            {
                "path": "app/services/timer_service.py",
                "depends_on": ["app/models/timer.py"],
                "exports": ["class TimerService"],
            },
        ]

        # Use phase_manifest (all chunks) for lookup — NOT tier_files
        _all_entries = phase_manifest
        _file_entries_by_path = {f["path"]: f for f in _all_entries}

        # timer_service.py can resolve timer.py's exports
        dep_entry = _file_entries_by_path.get("app/models/timer.py", {})
        assert dep_entry.get("exports") == ["class Timer(BaseModel)", "enum TimerStatus"]
