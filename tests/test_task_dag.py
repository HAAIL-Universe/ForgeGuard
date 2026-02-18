"""Tests for Phase 41 — Task DAG & Progress Tracking.

Covers:
  41.1  TaskNode, TaskDAG, DAGProgress models
  41.2  DAG construction from manifest (from_manifest)
  41.3  State transitions, failure cascade, recovery
  41.5  Topological ordering, cycle detection, progress tracking
"""

import pytest

from forge_ide.contracts import (
    CyclicDependencyError,
    DAGProgress,
    TaskDAG,
    TaskNode,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_manifest() -> list[dict]:
    """Three files: B depends on A, C depends on B."""
    return [
        {
            "path": "app/config.py",
            "purpose": "Configuration module",
            "depends_on": [],
            "estimated_lines": 50,
        },
        {
            "path": "app/main.py",
            "purpose": "Entry point",
            "depends_on": ["app/config.py"],
            "estimated_lines": 80,
        },
        {
            "path": "app/routes.py",
            "purpose": "API routes",
            "depends_on": ["app/main.py"],
            "estimated_lines": 120,
        },
    ]


def _diamond_manifest() -> list[dict]:
    """Diamond dependency: D depends on B and C, both depend on A."""
    return [
        {"path": "a.py", "purpose": "Base", "depends_on": [], "estimated_lines": 10},
        {"path": "b.py", "purpose": "Left", "depends_on": ["a.py"], "estimated_lines": 20},
        {"path": "c.py", "purpose": "Right", "depends_on": ["a.py"], "estimated_lines": 30},
        {"path": "d.py", "purpose": "Top", "depends_on": ["b.py", "c.py"], "estimated_lines": 40},
    ]


def _cyclic_manifest() -> list[dict]:
    """Circular dependency: A → B → C → A."""
    return [
        {"path": "a.py", "depends_on": ["c.py"]},
        {"path": "b.py", "depends_on": ["a.py"]},
        {"path": "c.py", "depends_on": ["b.py"]},
    ]


# ===========================================================================
# 41.1  TaskNode basics
# ===========================================================================


class TestTaskNode:
    """Tests for individual TaskNode behaviour."""

    def test_default_values(self) -> None:
        node = TaskNode(id="t0", title="Test task")
        assert node.status == TaskStatus.PENDING
        assert node.depends_on == []
        assert node.blocks == []
        assert node.file_path is None
        assert node.estimated_tokens == 0
        assert node.actual_tokens == 0
        assert node.error is None
        assert node.retry_count == 0

    def test_to_dict(self) -> None:
        node = TaskNode(
            id="p1_t0",
            title="Generate config",
            phase="1",
            file_path="app/config.py",
            estimated_tokens=200,
        )
        d = node.to_dict()
        assert d["id"] == "p1_t0"
        assert d["status"] == "pending"
        assert d["file_path"] == "app/config.py"
        assert d["estimated_tokens"] == 200
        assert d["error"] is None

    def test_repr(self) -> None:
        node = TaskNode(id="t0", title="X", file_path="a.py")
        r = repr(node)
        assert "t0" in r
        assert "pending" in r


# ===========================================================================
# 41.2  DAG from manifest
# ===========================================================================


class TestDAGFromManifest:
    """Tests for TaskDAG.from_manifest factory."""

    def test_linear_chain(self) -> None:
        manifest = _simple_manifest()
        dag = TaskDAG.from_manifest(manifest, phase_label="0")

        assert len(dag.nodes) == 3
        # Check node IDs
        assert "p0_t0" in dag.nodes
        assert "p0_t1" in dag.nodes
        assert "p0_t2" in dag.nodes

    def test_dependency_wiring(self) -> None:
        manifest = _simple_manifest()
        dag = TaskDAG.from_manifest(manifest, phase_label="0")

        # t1 (main.py) depends on t0 (config.py)
        assert "p0_t0" in dag.nodes["p0_t1"].depends_on
        # t2 (routes.py) depends on t1 (main.py)
        assert "p0_t1" in dag.nodes["p0_t2"].depends_on

    def test_blocks_wiring(self) -> None:
        manifest = _simple_manifest()
        dag = TaskDAG.from_manifest(manifest, phase_label="0")

        # t0 blocks t1 (config blocks main)
        assert "p0_t1" in dag.nodes["p0_t0"].blocks
        # t1 blocks t2 (main blocks routes)
        assert "p0_t2" in dag.nodes["p0_t1"].blocks

    def test_diamond_dependencies(self) -> None:
        manifest = _diamond_manifest()
        dag = TaskDAG.from_manifest(manifest)

        d_node = dag.nodes["t3"]  # d.py
        assert "t1" in d_node.depends_on  # b.py
        assert "t2" in d_node.depends_on  # c.py

    def test_estimated_tokens(self) -> None:
        manifest = _simple_manifest()
        dag = TaskDAG.from_manifest(manifest)

        # estimated_lines * 4
        assert dag.nodes["t0"].estimated_tokens == 200  # 50 * 4
        assert dag.nodes["t1"].estimated_tokens == 320  # 80 * 4

    def test_file_path_assignment(self) -> None:
        manifest = _simple_manifest()
        dag = TaskDAG.from_manifest(manifest)

        assert dag.nodes["t0"].file_path == "app/config.py"
        assert dag.nodes["t1"].file_path == "app/main.py"
        assert dag.nodes["t2"].file_path == "app/routes.py"

    def test_no_deps_manifest(self) -> None:
        manifest = [
            {"path": "a.py", "depends_on": []},
            {"path": "b.py", "depends_on": []},
        ]
        dag = TaskDAG.from_manifest(manifest)
        assert len(dag.nodes) == 2
        assert dag.nodes["t0"].depends_on == []
        assert dag.nodes["t1"].depends_on == []

    def test_cyclic_dependency_raises(self) -> None:
        with pytest.raises(CyclicDependencyError):
            TaskDAG.from_manifest(_cyclic_manifest())


# ===========================================================================
# 41.3  Ready tasks
# ===========================================================================


class TestGetReadyTasks:
    """Tests for get_ready_tasks query."""

    def test_initial_ready_tasks(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        ready = dag.get_ready_tasks()

        # Only config.py (no deps) should be ready
        assert len(ready) == 1
        assert ready[0].file_path == "app/config.py"

    def test_ready_after_completion(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        # Complete config.py
        dag.mark_completed("t0")
        ready = dag.get_ready_tasks()

        assert len(ready) == 1
        assert ready[0].file_path == "app/main.py"

    def test_diamond_ready(self) -> None:
        dag = TaskDAG.from_manifest(_diamond_manifest())

        # Initially only a.py is ready
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].file_path == "a.py"

        # Complete a.py → b.py and c.py become ready
        dag.mark_completed("t0")
        ready = dag.get_ready_tasks()
        paths = {r.file_path for r in ready}
        assert paths == {"b.py", "c.py"}

        # Complete b.py only → d.py still blocked (needs c.py)
        dag.mark_completed("t1")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].file_path == "c.py"

        # Complete c.py → d.py ready
        dag.mark_completed("t2")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].file_path == "d.py"

    def test_no_ready_when_all_complete(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_completed("t0")
        dag.mark_completed("t1")
        dag.mark_completed("t2")
        assert dag.get_ready_tasks() == []

    def test_skipped_dep_enables_downstream(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        # Skip config.py (e.g., already on disk)
        dag.mark_skipped("t0")
        ready = dag.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].file_path == "app/main.py"


# ===========================================================================
# 41.3  Failure cascade
# ===========================================================================


class TestFailureCascade:
    """Tests for mark_failed and downstream blocking."""

    def test_direct_block(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        dag.mark_failed("t0", "Syntax error")
        assert dag.nodes["t0"].status == TaskStatus.FAILED
        assert dag.nodes["t0"].error == "Syntax error"

        # main.py should be blocked
        assert dag.nodes["t1"].status == TaskStatus.BLOCKED

    def test_transitive_cascade(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        dag.mark_failed("t0", "error")

        # Both main.py and routes.py should be blocked
        assert dag.nodes["t1"].status == TaskStatus.BLOCKED
        assert dag.nodes["t2"].status == TaskStatus.BLOCKED

    def test_get_blocked_by(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        blocked = dag.get_blocked_by("t0")
        blocked_ids = {b.id for b in blocked}
        assert "t1" in blocked_ids
        assert "t2" in blocked_ids

    def test_diamond_partial_failure(self) -> None:
        dag = TaskDAG.from_manifest(_diamond_manifest())
        dag.mark_completed("t0")  # a.py done

        # b.py fails → d.py blocked, but c.py still pending
        dag.mark_failed("t1", "error")
        assert dag.nodes["t2"].status == TaskStatus.PENDING  # c.py unaffected
        assert dag.nodes["t3"].status == TaskStatus.BLOCKED  # d.py blocked

    def test_failure_sets_completed_at(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_failed("t0", "error")
        assert dag.nodes["t0"].completed_at is not None


# ===========================================================================
# 41.3  Recovery (unblock)
# ===========================================================================


class TestRecovery:
    """Tests for failed task recovery and unblocking."""

    def test_unblock_after_fix(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        # Fail config → main and routes blocked
        dag.mark_failed("t0", "error")
        assert dag.nodes["t1"].status == TaskStatus.BLOCKED

        # "Fix" config → complete it, then unblock
        dag.nodes["t0"].status = TaskStatus.COMPLETED
        dag.nodes["t0"].error = None
        unblocked = dag.unblock_downstream("t0")

        # main.py should be unblocked (its only dep, t0, is completed)
        unblocked_ids = {n.id for n in unblocked}
        assert "t1" in unblocked_ids
        assert dag.nodes["t1"].status == TaskStatus.PENDING

    def test_deep_cascade_unblock(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())

        dag.mark_failed("t0", "error")
        assert dag.nodes["t2"].status == TaskStatus.BLOCKED  # routes blocked

        # Fix config
        dag.nodes["t0"].status = TaskStatus.COMPLETED
        dag.nodes["t0"].error = None
        unblocked_1 = dag.unblock_downstream("t0")

        # main.py unblocked but routes.py still blocked (needs main.py)
        assert dag.nodes["t1"].status == TaskStatus.PENDING
        assert dag.nodes["t2"].status == TaskStatus.BLOCKED

        # Now complete main.py and unblock routes.py
        dag.mark_completed("t1")
        unblocked_2 = dag.unblock_downstream("t1")
        assert dag.nodes["t2"].status == TaskStatus.PENDING


# ===========================================================================
# 41.5  Topological order
# ===========================================================================


class TestTopologicalOrder:
    """Tests for topological ordering."""

    def test_linear_order(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        order = dag.topological_order()

        paths = [n.file_path for n in order]
        assert paths.index("app/config.py") < paths.index("app/main.py")
        assert paths.index("app/main.py") < paths.index("app/routes.py")

    def test_diamond_order(self) -> None:
        dag = TaskDAG.from_manifest(_diamond_manifest())
        order = dag.topological_order()

        paths = [n.file_path for n in order]
        # a.py must come before b.py and c.py
        assert paths.index("a.py") < paths.index("b.py")
        assert paths.index("a.py") < paths.index("c.py")
        # d.py must come after both b.py and c.py
        assert paths.index("d.py") > paths.index("b.py")
        assert paths.index("d.py") > paths.index("c.py")

    def test_no_deps_order(self) -> None:
        manifest = [
            {"path": "x.py", "depends_on": []},
            {"path": "y.py", "depends_on": []},
            {"path": "z.py", "depends_on": []},
        ]
        dag = TaskDAG.from_manifest(manifest)
        order = dag.topological_order()
        assert len(order) == 3

    def test_cycle_raises(self) -> None:
        # Build DAG manually to bypass from_manifest cycle check
        dag = TaskDAG()
        dag.add_task(TaskNode(id="a", title="A", depends_on=["c"]))
        dag.add_task(TaskNode(id="b", title="B", depends_on=["a"]))
        dag.add_task(TaskNode(id="c", title="C", depends_on=["b"]))

        with pytest.raises(CyclicDependencyError):
            dag.topological_order()


# ===========================================================================
# 41.5  Progress tracking
# ===========================================================================


class TestDAGProgress:
    """Tests for get_progress accuracy."""

    def test_initial_progress(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        p = dag.get_progress()

        assert p.total == 3
        assert p.completed == 0
        assert p.pending == 3
        assert p.percentage == 0.0
        assert p.estimated_remaining_tokens > 0

    def test_partial_progress(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_completed("t0")

        p = dag.get_progress()
        assert p.completed == 1
        assert p.pending == 2
        assert p.percentage == pytest.approx(33.3, abs=0.1)

    def test_all_complete(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_completed("t0")
        dag.mark_completed("t1")
        dag.mark_completed("t2")

        p = dag.get_progress()
        assert p.completed == 3
        assert p.percentage == 100.0
        assert p.estimated_remaining_tokens == 0

    def test_with_failures(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_failed("t0", "error")

        p = dag.get_progress()
        assert p.failed == 1
        assert p.blocked == 2
        assert p.percentage == 0.0

    def test_skipped_counts_toward_done(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_skipped("t0")

        p = dag.get_progress()
        # Skipped counts as "done" for percentage
        assert p.skipped == 1
        assert p.percentage == pytest.approx(33.3, abs=0.1)

    def test_estimated_cost(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        p = dag.get_progress()

        # Should have a non-zero cost estimate
        assert p.estimated_remaining_cost_usd > 0


# ===========================================================================
# 41.2  Serialisation
# ===========================================================================


class TestDAGSerialisation:
    """Tests for to_dict serialisation."""

    def test_to_dict_structure(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        d = dag.to_dict()

        assert "nodes" in d
        assert "progress" in d
        assert len(d["nodes"]) == 3

    def test_to_dict_after_transitions(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_completed("t0")
        dag.mark_in_progress("t1")

        d = dag.to_dict()
        assert d["nodes"]["t0"]["status"] == "completed"
        assert d["nodes"]["t1"]["status"] == "in_progress"
        assert d["progress"]["completed"] == 1

    def test_repr(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        r = repr(dag)
        assert "tasks=3" in r
        assert "done=0" in r


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge case tests for DAG operations."""

    def test_empty_manifest(self) -> None:
        dag = TaskDAG.from_manifest([])
        assert len(dag.nodes) == 0
        p = dag.get_progress()
        assert p.total == 0
        assert p.percentage == 0.0

    def test_single_task_manifest(self) -> None:
        manifest = [{"path": "only.py", "depends_on": []}]
        dag = TaskDAG.from_manifest(manifest)
        assert len(dag.nodes) == 1
        ready = dag.get_ready_tasks()
        assert len(ready) == 1

    def test_unknown_dependency_ignored(self) -> None:
        manifest = [
            {"path": "a.py", "depends_on": ["nonexistent.py"]},
        ]
        dag = TaskDAG.from_manifest(manifest)
        # Dependency on unknown file should be ignored in dep_ids
        assert dag.nodes["t0"].depends_on == []

    def test_wire_blocks_idempotent(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.wire_blocks()
        dag.wire_blocks()  # second call should not duplicate

        assert dag.nodes["t0"].blocks.count("t1") == 1

    def test_mark_in_progress_sets_started_at(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_in_progress("t0")
        assert dag.nodes["t0"].started_at is not None
        assert dag.nodes["t0"].status == TaskStatus.IN_PROGRESS

    def test_mark_completed_sets_timestamps(self) -> None:
        dag = TaskDAG.from_manifest(_simple_manifest())
        dag.mark_in_progress("t0")
        dag.mark_completed("t0", actual_tokens=500)

        node = dag.nodes["t0"]
        assert node.status == TaskStatus.COMPLETED
        assert node.actual_tokens == 500
        assert node.completed_at is not None
        assert node.started_at is not None
