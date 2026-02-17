"""Tests for forge_ide.relevance — relevance scoring functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forge_ide.file_index import FileMetadata
from forge_ide.relevance import (
    RelatedFile,
    find_related,
    score_directory_proximity,
    score_import_graph,
    score_name_similarity,
    score_recency,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture()
def simple_import_graph():
    """Forward import graph: a imports b, b imports c."""
    return {
        "src/a.py": ["src/b.py"],
        "src/b.py": ["src/c.py"],
        "src/c.py": [],
        "src/d.py": ["src/a.py"],
    }


@pytest.fixture()
def simple_reverse_graph():
    """Reverse import graph matching simple_import_graph."""
    return {
        "src/a.py": ["src/d.py"],
        "src/b.py": ["src/a.py"],
        "src/c.py": ["src/b.py"],
        "src/d.py": [],
    }


NOW = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def _meta(path: str, mtime: datetime | None = None) -> FileMetadata:
    return FileMetadata(path=path, language="python", last_modified=mtime)


# ===================================================================
# score_import_graph
# ===================================================================


class TestScoreImportGraph:
    def test_direct_import(self, simple_import_graph, simple_reverse_graph):
        """Target imports candidate → 1.0."""
        assert score_import_graph("src/a.py", "src/b.py", simple_import_graph, simple_reverse_graph) == 1.0

    def test_reverse_import(self, simple_import_graph, simple_reverse_graph):
        """Candidate imports target → 0.8."""
        assert score_import_graph("src/a.py", "src/d.py", simple_import_graph, simple_reverse_graph) == 0.8

    def test_transitive_two_hop(self, simple_import_graph, simple_reverse_graph):
        """Target → X → candidate (2 hops) → 0.5."""
        assert score_import_graph("src/a.py", "src/c.py", simple_import_graph, simple_reverse_graph) == 0.5

    def test_no_relation(self, simple_import_graph, simple_reverse_graph):
        """No import relation → 0.0."""
        assert score_import_graph("src/c.py", "src/d.py", simple_import_graph, simple_reverse_graph) == 0.0

    def test_self_import(self, simple_import_graph, simple_reverse_graph):
        """Self → 0.0 (excluded)."""
        assert score_import_graph("src/a.py", "src/a.py", simple_import_graph, simple_reverse_graph) == 0.0

    def test_empty_graphs(self):
        """Empty import graphs → 0.0."""
        assert score_import_graph("a.py", "b.py", {}, {}) == 0.0

    def test_candidate_not_in_graph(self, simple_import_graph, simple_reverse_graph):
        """Unknown candidate → 0.0."""
        assert score_import_graph("src/a.py", "unknown.py", simple_import_graph, simple_reverse_graph) == 0.0


# ===================================================================
# score_directory_proximity
# ===================================================================


class TestScoreDirectoryProximity:
    def test_same_directory(self):
        assert score_directory_proximity("src/a.py", "src/b.py") == 0.3

    def test_parent_child(self):
        assert score_directory_proximity("src/a.py", "src/sub/b.py") == 0.2

    def test_child_parent(self):
        assert score_directory_proximity("src/sub/a.py", "src/b.py") == 0.2

    def test_grandparent(self):
        assert score_directory_proximity("src/a.py", "src/sub/deep/b.py") == 0.1

    def test_sibling_dirs_at_root(self):
        # src/ and lib/ are 1 part each, common=0, distance=2 → grandparent → 0.1
        assert score_directory_proximity("src/a.py", "lib/b.py") == 0.1

    def test_far_apart(self):
        assert score_directory_proximity("src/deep/a.py", "lib/other/b.py") == 0.0

    def test_root_level_same(self):
        assert score_directory_proximity("a.py", "b.py") == 0.3

    def test_deep_same(self):
        assert score_directory_proximity("a/b/c/x.py", "a/b/c/y.py") == 0.3

    def test_backslash_normalised(self):
        assert score_directory_proximity("src\\a.py", "src\\b.py") == 0.3


# ===================================================================
# score_name_similarity
# ===================================================================


class TestScoreNameSimilarity:
    def test_test_impl_prefix(self):
        """test_foo ↔ foo → 0.4."""
        assert score_name_similarity("tests/test_foo.py", "src/foo.py") == 0.4

    def test_impl_test_prefix(self):
        """foo ↔ test_foo → 0.4."""
        assert score_name_similarity("src/foo.py", "tests/test_foo.py") == 0.4

    def test_test_impl_suffix(self):
        """foo_test ↔ foo → 0.4."""
        assert score_name_similarity("tests/foo_test.py", "src/foo.py") == 0.4

    def test_shared_prefix_long(self):
        """Same 4+ char prefix → 0.2."""
        assert score_name_similarity("src/handler_auth.py", "src/handler_build.py") == 0.2

    def test_shared_prefix_short(self):
        """Only 3-char prefix → 0.0."""
        assert score_name_similarity("src/abc.py", "src/abd.py") == 0.0

    def test_no_similarity(self):
        assert score_name_similarity("src/alpha.py", "src/beta.py") == 0.0

    def test_identical_files(self):
        """Same stem → counts as 4+ char prefix but equals itself."""
        # Same stem exact match: prefix_len == len(stem), but t_stem == c_stem
        # so the check `t_stem != c_stem` blocks it → 0.0
        assert score_name_similarity("src/foo.py", "lib/foo.py") == 0.0


# ===================================================================
# score_recency
# ===================================================================


class TestScoreRecency:
    def test_same_time(self):
        """Same modification time → max 0.3."""
        assert score_recency(NOW, NOW) == 0.3

    def test_half_window(self):
        """12 hours apart in 24-hour window → ~0.15."""
        half = NOW + timedelta(hours=12)
        result = score_recency(NOW, half)
        assert 0.14 <= result <= 0.16

    def test_at_boundary(self):
        """Exactly at window → 0.0."""
        boundary = NOW + timedelta(hours=24)
        assert score_recency(NOW, boundary) == 0.0

    def test_outside_window(self):
        """Beyond window → 0.0."""
        far = NOW + timedelta(hours=48)
        assert score_recency(NOW, far) == 0.0

    def test_none_target(self):
        assert score_recency(None, NOW) == 0.0

    def test_none_candidate(self):
        assert score_recency(NOW, None) == 0.0

    def test_both_none(self):
        assert score_recency(None, None) == 0.0

    def test_zero_window(self):
        assert score_recency(NOW, NOW, window_hours=0) == 0.0

    def test_naive_datetimes(self):
        """Naive datetimes treated as UTC."""
        naive = datetime(2025, 1, 15, 12, 0, 0)
        assert score_recency(naive, naive) == 0.3


# ===================================================================
# find_related
# ===================================================================


class TestFindRelated:
    def test_combines_factors(self):
        """Scores from multiple factors are additive."""
        files = [
            _meta("src/foo.py", NOW),
            _meta("src/test_foo.py", NOW),  # name similarity + same dir + recency
        ]
        imports = {"src/foo.py": ["src/test_foo.py"]}
        importers = {"src/test_foo.py": ["src/foo.py"]}
        result = find_related("src/foo.py", files, imports, importers)
        assert len(result) == 1
        r = result[0]
        # direct import (1.0) + dir proximity (0.3) + name (0.4) + recency (0.3)
        assert r.score == pytest.approx(2.0, abs=0.01)
        assert "direct import" in r.reasons

    def test_excludes_self(self):
        files = [_meta("a.py"), _meta("b.py")]
        result = find_related("a.py", files, {}, {})
        assert all(r.path != "a.py" for r in result)

    def test_max_results(self):
        files = [_meta(f"src/f{i}.py", NOW) for i in range(20)]
        result = find_related("src/f0.py", files, {}, {}, max_results=5)
        assert len(result) <= 5

    def test_empty_file_list(self):
        assert find_related("a.py", [], {}, {}) == []

    def test_single_file(self):
        """Only the target file in the list → empty results."""
        files = [_meta("a.py")]
        assert find_related("a.py", files, {}, {}) == []

    def test_sorted_descending(self):
        files = [
            _meta("src/a.py"),      # same dir only → 0.3
            _meta("src/b.py"),      # same dir only → 0.3
            _meta("lib/c.py"),      # different dir, no relation
        ]
        imports = {"src/target.py": ["src/a.py"]}
        importers = {"src/a.py": ["src/target.py"]}
        result = find_related("src/target.py", files, imports, importers)
        scores = [r.score for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_zero_score_excluded(self):
        """Files with no relation at all are excluded."""
        files = [_meta("a/b/c/unrelated.py")]
        result = find_related("x/y/z/target.py", files, {}, {})
        assert result == []

    def test_related_file_model_frozen(self):
        rf = RelatedFile(path="a.py", score=1.0, reasons=["test"])
        with pytest.raises(Exception):
            rf.path = "b.py"  # type: ignore[misc]
