"""Tests for Check 6 (dependency map) in integration_audit.py
and enriched _extract_exports() in pipeline_state.py.
"""

import pytest
from pathlib import Path
from uuid import uuid4

# ---------------------------------------------------------------------------
# Check 6 sub-checks
# ---------------------------------------------------------------------------

from app.services.build.integration_audit import (
    _check_missing_init_py,
    _check_method_existence,
    _check_async_sync,
    _check_route_consistency,
    _check_dependency_map,
    _build_class_registry,
    IntegrationIssue,
)


# -- Sub-check A: Missing __init__.py ------------------------------------

class TestMissingInitPy:
    def test_detects_missing_init_py(self, tmp_path):
        chunk = {
            "app/routers/timers.py": "from app.models.timer import Timer\n",
        }
        all_files = {**chunk}
        issues = _check_missing_init_py(chunk, all_files, str(tmp_path))
        names = [i.check_name for i in issues]
        assert "missing_init_py" in names
        # Should flag both app/__init__.py and app/models/__init__.py
        messages = [i.message for i in issues]
        assert any("app/__init__.py" in m for m in messages)
        assert any("app/models/__init__.py" in m for m in messages)

    def test_no_issue_when_init_exists(self, tmp_path):
        chunk = {
            "app/routers/timers.py": "from app.models.timer import Timer\n",
        }
        all_files = {
            **chunk,
            "app/__init__.py": "",
            "app/models/__init__.py": "",
            "app/models/timer.py": "class Timer: pass\n",
            "app/routers/__init__.py": "",
        }
        issues = _check_missing_init_py(chunk, all_files, str(tmp_path))
        assert not any(i.check_name == "missing_init_py" for i in issues)

    def test_ignores_third_party_imports(self, tmp_path):
        chunk = {
            "app/main.py": "import fastapi\nfrom pydantic import BaseModel\n",
        }
        issues = _check_missing_init_py(chunk, {**chunk}, str(tmp_path))
        assert len(issues) == 0

    def test_checks_disk_for_init(self, tmp_path):
        # Create __init__.py on disk but not in all_files
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "__init__.py").write_text("")
        (tmp_path / "app" / "models").mkdir()
        (tmp_path / "app" / "models" / "__init__.py").write_text("")

        chunk = {
            "app/routers/timers.py": "from app.models.timer import Timer\n",
        }
        issues = _check_missing_init_py(chunk, {**chunk}, str(tmp_path))
        assert not any(i.check_name == "missing_init_py" for i in issues)


# -- Sub-check B: Method existence ----------------------------------------

class TestMethodExistence:
    def test_detects_missing_method(self):
        all_files = {
            "app/services/timer_service.py": (
                "class TimerService:\n"
                "    def create_timer(self, name: str) -> None:\n"
                "        pass\n"
                "    def get_timer(self, timer_id: str):\n"
                "        pass\n"
            ),
        }
        chunk = {
            "app/routers/timers.py": (
                "from app.services.timer_service import TimerService\n"
                "\n"
                "service = TimerService()\n"
                "service.list_timers()\n"  # doesn't exist
                "service.delete_timer('123')\n"  # doesn't exist
            ),
        }
        issues = _check_method_existence(chunk, {**all_files, **chunk})
        names = [i.check_name for i in issues]
        assert names.count("missing_method") == 2
        assert any("list_timers" in i.message for i in issues)
        assert any("delete_timer" in i.message for i in issues)

    def test_no_issue_when_method_exists(self):
        all_files = {
            "app/services/timer_service.py": (
                "class TimerService:\n"
                "    def create_timer(self, name: str):\n"
                "        pass\n"
            ),
        }
        chunk = {
            "app/routers/timers.py": (
                "from app.services.timer_service import TimerService\n"
                "service = TimerService()\n"
                "service.create_timer('test')\n"
            ),
        }
        issues = _check_method_existence(chunk, {**all_files, **chunk})
        assert not any(i.check_name == "missing_method" for i in issues)

    def test_build_class_registry(self):
        files = {
            "app/models.py": (
                "class User:\n"
                "    name: str\n"
                "    def __init__(self, name: str, email: str):\n"
                "        pass\n"
                "    async def save(self):\n"
                "        pass\n"
                "    def to_dict(self):\n"
                "        pass\n"
            ),
        }
        registry = _build_class_registry(files)
        assert "User" in registry
        assert registry["User"]["init_params"] == ["name", "email"]
        assert "save" in registry["User"]["methods"]
        assert registry["User"]["methods"]["save"] is True  # async
        assert "to_dict" in registry["User"]["methods"]
        assert registry["User"]["methods"]["to_dict"] is False  # sync


# -- Sub-check C: Async/sync mismatch ------------------------------------

class TestAsyncSync:
    def test_detects_await_on_sync(self):
        all_files = {
            "app/services/timer_service.py": (
                "class TimerService:\n"
                "    def create_timer(self, name: str):\n"
                "        pass\n"
            ),
        }
        chunk = {
            "app/routers/timers.py": (
                "from app.services.timer_service import TimerService\n"
                "async def handler():\n"
                "    service = TimerService()\n"
                "    await service.create_timer('test')\n"  # await on sync
            ),
        }
        issues = _check_async_sync(chunk, {**all_files, **chunk})
        assert any(i.check_name == "async_sync_mismatch" for i in issues)
        assert any("synchronous" in i.message for i in issues)

    def test_no_issue_when_await_on_async(self):
        all_files = {
            "app/services/timer_service.py": (
                "class TimerService:\n"
                "    async def create_timer(self, name: str):\n"
                "        pass\n"
            ),
        }
        chunk = {
            "app/routers/timers.py": (
                "from app.services.timer_service import TimerService\n"
                "async def handler():\n"
                "    service = TimerService()\n"
                "    await service.create_timer('test')\n"
            ),
        }
        issues = _check_async_sync(chunk, {**all_files, **chunk})
        assert not any(i.check_name == "async_sync_mismatch" for i in issues)


# -- Sub-check D: API route consistency -----------------------------------

class TestRouteConsistency:
    def test_detects_route_mismatch(self):
        all_files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "app.include_router(timer_router, prefix='/timer')\n"
            ),
            "app/routers/timers.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.get('/timers/{timer_id}')\n"
                "async def get_timer(timer_id: str): pass\n"
            ),
            "web/src/api.ts": (
                "const response = await fetch('/api/timers/123');\n"
            ),
        }
        issues = _check_route_consistency(all_files)
        assert any(i.check_name == "route_mismatch" for i in issues)

    def test_no_issue_when_routes_match(self):
        all_files = {
            "app/routers/timers.py": (
                "from fastapi import APIRouter\n"
                "router = APIRouter()\n"
                "@router.get('/timers')\n"
                "async def list_timers(): pass\n"
                "@router.post('/timers')\n"
                "async def create_timer(): pass\n"
            ),
            "web/src/api.ts": (
                "const response = await fetch('/timers');\n"
            ),
        }
        issues = _check_route_consistency(all_files)
        # Should not flag since /timers matches exactly
        route_issues = [i for i in issues if i.check_name == "route_mismatch"]
        assert len(route_issues) == 0

    def test_no_issues_without_frontend(self):
        all_files = {
            "app/routers/timers.py": (
                "@router.get('/timers')\n"
                "async def list_timers(): pass\n"
            ),
        }
        issues = _check_route_consistency(all_files)
        assert len(issues) == 0


# -- Full Check 6 integration -------------------------------------------

class TestCheckDependencyMap:
    def test_catches_multiple_issue_types(self, tmp_path):
        all_files = {
            "app/services/timer_service.py": (
                "class TimerService:\n"
                "    def create_timer(self, name: str):\n"
                "        pass\n"
            ),
        }
        chunk = {
            "app/routers/timers.py": (
                "from app.services.timer_service import TimerService\n"
                "async def handler():\n"
                "    service = TimerService()\n"
                "    await service.create_timer('test')\n"  # await on sync
                "    service.nonexistent_method()\n"  # missing method
            ),
        }
        issues = _check_dependency_map(chunk, {**all_files, **chunk}, str(tmp_path))
        check_names = {i.check_name for i in issues}
        assert "async_sync_mismatch" in check_names
        assert "missing_method" in check_names
        assert "missing_init_py" in check_names


# ---------------------------------------------------------------------------
# Enriched _extract_exports()
# ---------------------------------------------------------------------------

from app.services.build.pipeline_state import _extract_exports


class TestExtractExports:
    def test_extracts_class_with_fields(self):
        code = (
            "from pydantic import BaseModel\n"
            "\n"
            "class Timer(BaseModel):\n"
            "    id: str\n"
            "    name: str\n"
            "    duration_seconds: int\n"
        )
        exports = _extract_exports(code)
        assert any("Timer" in e and "id: str" in e for e in exports)

    def test_extracts_enum(self):
        code = (
            "from enum import Enum\n"
            "\n"
            "class TimerState(str, Enum):\n"
            "    IDLE = 'idle'\n"
            "    RUNNING = 'running'\n"
            "    PAUSED = 'paused'\n"
        )
        exports = _extract_exports(code)
        assert any("enum" in e and "IDLE" in e and "RUNNING" in e for e in exports)

    def test_extracts_method_signatures(self):
        code = (
            "class TimerService:\n"
            "    def __init__(self, repo: TimerRepo):\n"
            "        self.repo = repo\n"
            "\n"
            "    async def create_timer(self, name: str, duration: int) -> Timer:\n"
            "        pass\n"
            "\n"
            "    def get_timer(self, timer_id: str) -> Timer:\n"
            "        pass\n"
        )
        exports = _extract_exports(code)
        # Should have: class TimerService, __init__, create_timer, get_timer
        assert any("TimerService" in e for e in exports)
        assert any("__init__" in e and "repo: TimerRepo" in e for e in exports)
        assert any("async" in e and "create_timer" in e for e in exports)
        assert any("get_timer" in e and "timer_id: str" in e for e in exports)

    def test_extracts_standalone_functions(self):
        code = (
            "async def calculate_urgency(timer_id: str, factor: float = 1.0) -> float:\n"
            "    pass\n"
            "\n"
            "def format_time(seconds: int) -> str:\n"
            "    pass\n"
        )
        exports = _extract_exports(code)
        assert any("async" in e and "calculate_urgency" in e for e in exports)
        assert any("format_time" in e and "seconds: int" in e for e in exports)

    def test_respects_dunder_all(self):
        code = (
            '__all__ = ["Timer", "TimerState"]\n'
            "\n"
            "class Timer:\n"
            "    pass\n"
            "\n"
            "class TimerState:\n"
            "    pass\n"
            "\n"
            "class _Internal:\n"
            "    pass\n"
        )
        exports = _extract_exports(code)
        # __all__ is checked by the regex fallback (AST path parses all
        # top-level classes). The function returns names from __all__ in
        # the fallback path, or class signatures from AST path.
        export_text = " ".join(exports)
        assert "Timer" in export_text
        assert "TimerState" in export_text
        assert "_Internal" not in export_text

    def test_handles_empty_code(self):
        assert _extract_exports("") == []

    def test_handles_syntax_error(self):
        exports = _extract_exports("def broken(")
        # Should fall back to regex or return partial
        assert isinstance(exports, list)

    def test_skips_private(self):
        code = (
            "class _PrivateClass:\n"
            "    pass\n"
            "\n"
            "def _helper():\n"
            "    pass\n"
            "\n"
            "class PublicClass:\n"
            "    pass\n"
        )
        exports = _extract_exports(code)
        assert not any(e.startswith("_") for e in exports if isinstance(e, str) and "._" not in e)
        assert any("PublicClass" in e for e in exports)


# ---------------------------------------------------------------------------
# Plan schema validation
# ---------------------------------------------------------------------------

class TestFileManifestSchema:
    def test_accepts_depends_on_and_exports(self):
        from planner.plan_schema import FileManifest
        fm = FileManifest(
            path="app/main.py",
            layer="router",
            action="create",
            description="App entry point",
            depends_on=["app/routers/users.py"],
            exports=["router prefix: /api/users"],
        )
        assert fm.depends_on == ["app/routers/users.py"]
        assert fm.exports == ["router prefix: /api/users"]

    def test_defaults_to_empty_lists(self):
        from planner.plan_schema import FileManifest
        fm = FileManifest(
            path="app/main.py",
            layer="router",
            action="create",
            description="App entry point",
        )
        assert fm.depends_on == []
        assert fm.exports == []
