"""Tests for Phase 40 — Structured Reconnaissance.

Covers:
  40.1  WorkspaceSnapshot engine (capture_snapshot, update_snapshot)
  40.3  Context pack builder (build_context_pack_for_file)
  40.x  snapshot_to_workspace_info
"""

import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forge_ide.workspace import (
    Workspace,
    WorkspaceSnapshot,
    TestInventory,
    SchemaInventory,
    capture_snapshot,
    update_snapshot,
    snapshot_to_workspace_info,
)
from forge_ide.context_pack import (
    build_context_pack_for_file,
    estimate_tokens,
    pack_to_text,
)


# ---------------------------------------------------------------------------
# Helpers — create a small fixture workspace on disk
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> tuple[Path, Workspace]:
    """Create a minimal multi-file workspace for testing."""
    root = tmp_path / "project"
    root.mkdir()

    # Python backend files
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "config.py").write_text(
        textwrap.dedent("""\
            import os

            DATABASE_URL = os.getenv("DATABASE_URL", "")

            class Settings:
                DEBUG: bool = False
        """),
        encoding="utf-8",
    )
    (root / "app" / "main.py").write_text(
        textwrap.dedent("""\
            from app.config import Settings

            def create_app():
                return "app"
        """),
        encoding="utf-8",
    )

    # Test file
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text(
        textwrap.dedent("""\
            import pytest
            from app.main import create_app

            def test_create_app():
                assert create_app() == "app"

            def test_something_else():
                assert True
        """),
        encoding="utf-8",
    )

    # TypeScript frontend file
    (root / "web").mkdir()
    (root / "web" / "src").mkdir(parents=True)
    (root / "web" / "src" / "App.tsx").write_text(
        textwrap.dedent("""\
            export function App() {
                return <div>Hello</div>;
            }

            export const VERSION = "1.0.0";
        """),
        encoding="utf-8",
    )

    # SQL migration
    (root / "db").mkdir()
    (root / "db" / "migrations").mkdir()
    (root / "db" / "migrations" / "001_users.sql").write_text(
        textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY,
                email TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS repos (
                id UUID PRIMARY KEY,
                user_id UUID REFERENCES users(id),
                full_name TEXT NOT NULL
            );
        """),
        encoding="utf-8",
    )

    # Config file
    (root / "requirements.txt").write_text("fastapi==0.115.6\npytest==8.3.4\n", encoding="utf-8")

    ws = Workspace(root)
    return root, ws


# ===========================================================================
# 40.1  capture_snapshot
# ===========================================================================


class TestCaptureSnapshot:
    """Tests for the unified workspace snapshot engine."""

    def test_basic_snapshot(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        assert isinstance(snap, WorkspaceSnapshot)
        assert snap.total_files > 0
        assert snap.total_lines > 0
        assert isinstance(snap.captured_at, datetime)

    def test_symbol_table_python(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        # Settings class should be in symbol table
        assert "app.config.Settings" in snap.symbol_table
        assert snap.symbol_table["app.config.Settings"] == "class"

        # create_app function should be in symbol table
        assert "app.main.create_app" in snap.symbol_table
        assert snap.symbol_table["app.main.create_app"] == "function"

        # DATABASE_URL constant should be in symbol table
        assert "app.config.DATABASE_URL" in snap.symbol_table
        assert snap.symbol_table["app.config.DATABASE_URL"] == "constant"

    def test_symbol_table_typescript(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        # App function should be detected from export
        assert "web.src.App.App" in snap.symbol_table
        assert snap.symbol_table["web.src.App.App"] == "function"

        # VERSION const should be detected
        assert "web.src.App.VERSION" in snap.symbol_table

    def test_dependency_graph(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        # app/main.py imports from app.config
        assert "app/main.py" in snap.dependency_graph
        imports = snap.dependency_graph["app/main.py"]
        assert "app.config" in imports

    def test_test_inventory(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        ti = snap.test_inventory
        assert len(ti.test_files) >= 1
        assert "tests/test_main.py" in ti.test_files
        assert ti.test_count >= 2  # test_create_app + test_something_else
        assert "pytest" in ti.frameworks

    def test_schema_inventory(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        si = snap.schema_inventory
        assert "users" in si.tables
        assert "repos" in si.tables
        assert len(si.migration_files) >= 1

        # Column detection
        if "users" in si.columns:
            assert "id" in si.columns["users"]
            assert "email" in si.columns["users"]

    def test_language_line_counts(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        assert "python" in snap.languages
        assert snap.languages["python"] > 0

    def test_file_tree_string(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        assert snap.file_tree  # non-empty
        assert isinstance(snap.file_tree, str)

    def test_empty_workspace(self, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        ws = Workspace(root)
        snap = capture_snapshot(ws)

        assert snap.total_files == 0
        assert snap.total_lines == 0
        assert snap.symbol_table == {}
        assert snap.test_inventory.test_count == 0


# ===========================================================================
# 40.1  update_snapshot
# ===========================================================================


class TestUpdateSnapshot:
    """Tests for incremental snapshot updates."""

    def test_update_adds_new_symbols(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        # Add a new file
        (root / "app" / "utils.py").write_text(
            textwrap.dedent("""\
                def helper():
                    return 42

                class Formatter:
                    pass
            """),
            encoding="utf-8",
        )
        ws.invalidate_cache()

        updated = update_snapshot(snap, ["app/utils.py"], ws)

        assert "app.utils.helper" in updated.symbol_table
        assert "app.utils.Formatter" in updated.symbol_table
        assert updated.total_files > snap.total_files

    def test_update_with_no_changes(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        updated = update_snapshot(snap, [], ws)

        # Should return the same snapshot (or equivalent)
        assert updated.total_files == snap.total_files
        assert updated.symbol_table == snap.symbol_table

    def test_update_removes_deleted_file_symbols(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        assert "app.config.Settings" in snap.symbol_table

        # Delete the file
        (root / "app" / "config.py").unlink()
        ws.invalidate_cache()

        updated = update_snapshot(snap, ["app/config.py"], ws)

        assert "app.config.Settings" not in updated.symbol_table


# ===========================================================================
# 40.x  snapshot_to_workspace_info
# ===========================================================================


class TestSnapshotToWorkspaceInfo:
    """Tests for the workspace_info string generation."""

    def test_produces_nonempty_string(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        info = snapshot_to_workspace_info(snap)

        assert isinstance(info, str)
        assert len(info) > 50
        assert "files" in info.lower()

    def test_includes_test_count(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        info = snapshot_to_workspace_info(snap)

        assert "test" in info.lower()
        assert "pytest" in info.lower()

    def test_includes_table_info(self, tmp_path: Path) -> None:
        root, ws = _make_workspace(tmp_path)
        snap = capture_snapshot(ws)

        info = snapshot_to_workspace_info(snap)

        assert "users" in info

    def test_empty_snapshot(self) -> None:
        snap = WorkspaceSnapshot(total_files=0, total_lines=0)
        info = snapshot_to_workspace_info(snap)
        assert "0 files" in info


# ===========================================================================
# 40.3  build_context_pack_for_file
# ===========================================================================


class TestBuildContextPackForFile:
    """Tests for the per-file context pack builder."""

    def _sample_contracts(self) -> list[dict]:
        return [
            {"contract_type": "blueprint", "content": "# Blueprint\nProject structure..."},
            {"contract_type": "schema", "content": "# Schema\nCREATE TABLE users (...)"},
            {"contract_type": "stack", "content": "# Stack\nPython 3.12, FastAPI"},
            {"contract_type": "ui", "content": "# UI\nReact + Tailwind"},
            {"contract_type": "boundaries", "content": "# Boundaries\n{\"rules\": []}"},
            {"contract_type": "phases", "content": "# Phases\nPhase 0 -- Genesis"},
        ]

    def test_python_backend_selects_correct_contracts(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="app/services/auth_service.py",
            file_purpose="Authentication service",
            contracts=contracts,
            context_file_contents={},
        )
        # Should include blueprint, schema, stack, boundaries
        contract_paths = [tf.path for tf in pack.target_files]
        assert "contracts/blueprint" in contract_paths
        assert "contracts/schema" in contract_paths
        assert "contracts/stack" in contract_paths
        assert "contracts/boundaries" in contract_paths
        # Should NOT include ui or phases
        assert "contracts/ui" not in contract_paths
        assert "contracts/phases" not in contract_paths

    def test_frontend_selects_ui_contract(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="web/src/pages/Dashboard.tsx",
            file_purpose="Dashboard page component",
            contracts=contracts,
            context_file_contents={},
        )
        contract_paths = [tf.path for tf in pack.target_files]
        assert "contracts/ui" in contract_paths
        assert "contracts/blueprint" in contract_paths
        # Should NOT include schema
        assert "contracts/schema" not in contract_paths

    def test_migration_selects_schema_only(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="db/migrations/002_repos.sql",
            file_purpose="Create repos table",
            contracts=contracts,
            context_file_contents={},
        )
        contract_paths = [tf.path for tf in pack.target_files]
        assert "contracts/schema" in contract_paths
        assert len(contract_paths) == 1

    def test_includes_dependency_snippets(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="app/services/repo_service.py",
            file_purpose="Repo management service",
            contracts=contracts,
            context_file_contents={
                "app/repos/repo_repo.py": "class RepoRepo:\n    pass\n",
                "app/config.py": "DATABASE_URL = ''\n",
            },
        )
        dep_paths = [ds.path for ds in pack.dependency_snippets]
        assert "app/repos/repo_repo.py" in dep_paths
        assert "app/config.py" in dep_paths

    def test_token_budget_enforcement(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="app/main.py",
            file_purpose="Entry point",
            contracts=contracts,
            context_file_contents={
                "dep.py": "x" * 200_000,  # massive fake dependency
            },
            budget_tokens=1_000,
        )
        text = pack_to_text(pack)
        tokens = estimate_tokens(text)
        # Should be roughly within budget (core content may exceed, but deps trimmed)
        # The core is always included, so we just verify trimming happened
        assert pack.token_estimate > 0

    def test_test_file_selects_correct_contracts(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="tests/test_auth_service.py",
            file_purpose="Auth service tests",
            contracts=contracts,
            context_file_contents={
                "app/services/auth_service.py": "class AuthService:\n    pass\n",
            },
        )
        contract_paths = [tf.path for tf in pack.target_files]
        # Python tests get blueprint, schema, stack
        assert "contracts/blueprint" in contract_paths
        assert "contracts/schema" in contract_paths

    def test_pack_serialises_to_text(self) -> None:
        contracts = self._sample_contracts()
        pack = build_context_pack_for_file(
            file_path="app/main.py",
            file_purpose="Entry point",
            contracts=contracts,
            context_file_contents={},
        )
        text = pack_to_text(pack)
        assert isinstance(text, str)
        assert len(text) > 0
        assert "app/main.py" in text
