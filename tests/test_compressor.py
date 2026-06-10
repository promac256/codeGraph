"""Tests for the ContextCompressor (focus-file + role-aware compression)."""

from __future__ import annotations

import pytest

from codegraph.context.compressor import ContextCompressor
from codegraph.context.pack_generator import ContextPack, ContextPackGenerator
from codegraph.graph.queries import GraphQuery
from codegraph.models import ClassNode, FileNode, FunctionNode, GraphEdge, EdgeKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pack(**kwargs) -> ContextPack:
    defaults = dict(
        repo_name="testrepo",
        generated_at="2026-01-01T00:00:00",
        token_budget=8000,
        repo_overview={"files": 10},
        hot_paths=[
            {"node_id": "file:a.py", "name": "a.py", "kind": "file", "file": "file:a.py", "complexity": 5, "commit_count": 3, "score": 0.5},
            {"node_id": "func:a.py::foo", "name": "foo", "kind": "function", "file": "file:a.py", "complexity": 3, "commit_count": 1, "score": 0.3},
            {"node_id": "func:b.py::bar", "name": "bar", "kind": "function", "file": "file:b.py", "complexity": 10, "commit_count": 5, "score": 0.4},
        ],
        public_api_summary=[
            {"name": "foo", "kind": "function", "file": "a.py", "sig": "foo() -> None"},
            {"name": "bar", "kind": "function", "file": "b.py", "sig": "bar() -> int"},
            {"name": "Alpha", "kind": "class",    "file": "a.py", "sig": ""},
        ],
        key_classes=[
            {"name": "Alpha", "file": "a.py", "docstring": "class alpha", "bases": []},
            {"name": "Beta",  "file": "b.py", "docstring": "class beta",  "bases": ["Alpha"]},
        ],
        todos=[
            {"file": "a.py", "line": 10, "kind": "TODO",  "text": "fix this"},
            {"file": "b.py", "line": 20, "kind": "FIXME", "text": "broken"},
        ],
        recent_changes=[
            {"sha": "abc", "author": "alice", "message": "update a.py"},
            {"sha": "def", "author": "bob",   "message": "update b.py"},
            {"sha": "ghi", "author": "carol", "message": "update c.py"},
        ],
    )
    defaults.update(kwargs)
    return ContextPack(**defaults)


def _build_store(tmp_db):
    """Populate a GraphStore with a/b.py files plus functions and an import edge."""
    file_a = FileNode(node_id="file:a.py", path="a.py", lang="python", layer="business")
    file_b = FileNode(node_id="file:b.py", path="b.py", lang="python", layer="business")
    fn_foo = FunctionNode(
        node_id="func:a.py::foo",
        name="foo",
        qualified_name="foo",
        file="file:a.py",
        line_start=5,
        signature="foo() -> None",
        complexity=3,
    )
    fn_bar = FunctionNode(
        node_id="func:b.py::bar",
        name="bar",
        qualified_name="bar",
        file="file:b.py",
        line_start=15,
        signature="bar() -> int",
        complexity=10,
    )
    cls_alpha = ClassNode(
        node_id="class:a.py::Alpha",
        name="Alpha",
        file="file:a.py",
        line_start=20,
        docstring="class alpha",
    )
    import_edge = GraphEdge(src="file:b.py", dst="file:a.py", kind=EdgeKind.IMPORTS)
    calls_edge  = GraphEdge(src="func:b.py::bar", dst="func:a.py::foo", kind=EdgeKind.CALLS)

    for node in (file_a, file_b, fn_foo, fn_bar, cls_alpha):
        tmp_db.upsert_node(node)
    for edge in (import_edge, calls_edge):
        tmp_db.upsert_edge(edge)
    tmp_db.set_config("repo_name", "testrepo")
    tmp_db.commit_transaction()
    tmp_db.load_graph_to_memory()
    return tmp_db


# ---------------------------------------------------------------------------
# Focus-file: list re-ordering
# ---------------------------------------------------------------------------


class TestFocusFileReordering:
    def test_hot_paths_focus_file_first(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        compressor = ContextCompressor(store, q)
        out = compressor.compress(pack, focus_file="a.py")

        # a.py entries should appear before b.py entries
        a_indices = [i for i, h in enumerate(out.hot_paths) if "a.py" in h.get("file", "") or h.get("node_id","") == "file:a.py"]
        b_indices = [i for i, h in enumerate(out.hot_paths) if "b.py" in h.get("file", "") or h.get("node_id","") == "file:b.py"]
        if a_indices and b_indices:
            assert min(a_indices) < min(b_indices)

    def test_public_api_focus_file_first(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        compressor = ContextCompressor(store, q)
        out = compressor.compress(pack, focus_file="a.py")

        a_api = [a for a in out.public_api_summary if a["file"] == "a.py"]
        b_api = [a for a in out.public_api_summary if a["file"] == "b.py"]
        if a_api and b_api:
            assert out.public_api_summary.index(a_api[0]) < out.public_api_summary.index(b_api[0])

    def test_key_classes_focus_file_first(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        compressor = ContextCompressor(store, q)
        out = compressor.compress(pack, focus_file="a.py")

        if out.key_classes:
            assert out.key_classes[0]["file"] == "a.py"

    def test_todos_focus_file_first(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        compressor = ContextCompressor(store, q)
        out = compressor.compress(pack, focus_file="a.py")

        if out.todos:
            assert "a.py" in out.todos[0]["file"]


# ---------------------------------------------------------------------------
# Focus-file: focus_context structure
# ---------------------------------------------------------------------------


class TestFocusContext:
    def test_focus_context_populated(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        assert out.focus_context != {}

    def test_focus_context_file_field(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        assert out.focus_context["file"] == "a.py"

    def test_focus_context_symbols_include_function(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        names = [s["name"] for s in out.focus_context["symbols"]]
        assert "foo" in names

    def test_focus_context_symbols_include_class(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        names = [s["name"] for s in out.focus_context["symbols"]]
        assert "Alpha" in names

    def test_focus_context_symbols_sorted_by_line(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        lines = [s["line_start"] for s in out.focus_context["symbols"]]
        assert lines == sorted(lines)

    def test_focus_context_imported_by(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        # b.py imports a.py, so it should appear in imported_by
        assert "b.py" in out.focus_context["imported_by"]

    def test_focus_context_imports(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="b.py")
        # b.py imports a.py
        assert "a.py" in out.focus_context["imports"]

    def test_focus_context_callers(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        # bar in b.py calls foo in a.py
        callers_str = " ".join(out.focus_context.get("callers", []))
        assert "bar" in callers_str or "b.py" in callers_str

    def test_focus_context_empty_without_focus(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack)
        assert out.focus_context == {}

    def test_unknown_file_graceful(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="nonexistent.py")
        # Should produce an empty symbols list but not raise
        assert "file" in out.focus_context
        assert out.focus_context["symbols"] == []


# ---------------------------------------------------------------------------
# Token budget override
# ---------------------------------------------------------------------------


class TestTokenBudgetOverride:
    def test_token_budget_override(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack(token_budget=8000)
        out = ContextCompressor(store, q).compress(pack, token_budget=2000)
        assert out.token_budget == 2000

    def test_token_budget_preserved_when_none(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack(token_budget=6000)
        out = ContextCompressor(store, q).compress(pack)
        assert out.token_budget == 6000


# ---------------------------------------------------------------------------
# Role: debug
# ---------------------------------------------------------------------------


class TestRoleDebug:
    def test_debug_sorts_by_complexity(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, role="debug")
        complexities = [h.get("complexity", 1) * (1 + h.get("commit_count", 0)) for h in out.hot_paths]
        assert complexities == sorted(complexities, reverse=True)

    def test_debug_limits_hot_paths_to_10(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        long_hot_paths = [
            {"node_id": f"func:f{i}.py::fn", "name": f"fn{i}", "kind": "function",
             "file": f"file:f{i}.py", "complexity": i, "commit_count": i, "score": float(i)}
            for i in range(20)
        ]
        pack = _make_pack(hot_paths=long_hot_paths)
        out = ContextCompressor(store, q).compress(pack, role="debug")
        assert len(out.hot_paths) <= 10


# ---------------------------------------------------------------------------
# Role: review
# ---------------------------------------------------------------------------


class TestRoleReview:
    def test_review_sorts_api_alphabetically(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, role="review")
        names = [a["name"] for a in out.public_api_summary]
        assert names == sorted(names)

    def test_review_limits_hot_paths_to_5(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        long_hot_paths = [
            {"node_id": f"func:f{i}.py::fn", "name": f"fn{i}", "kind": "function",
             "file": f"file:f{i}.py", "complexity": 1, "commit_count": 0, "score": 0.0}
            for i in range(15)
        ]
        pack = _make_pack(hot_paths=long_hot_paths)
        out = ContextCompressor(store, q).compress(pack, role="review")
        assert len(out.hot_paths) <= 5

    def test_review_preserves_todos(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, role="review")
        assert len(out.todos) >= len(pack.todos)


# ---------------------------------------------------------------------------
# Role: feature
# ---------------------------------------------------------------------------


class TestRoleFeature:
    def test_feature_trims_recent_changes(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, role="feature")
        assert len(out.recent_changes) <= 3

    def test_feature_with_focus_adds_similar_files(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py", role="feature")
        # b.py is in the same layer (business), should appear in similar_files
        assert "similar_files" in out.focus_context
        assert "b.py" in out.focus_context["similar_files"]

    def test_feature_without_focus_no_similar_files(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, role="feature")
        # No focus → no similar_files key added
        assert "similar_files" not in out.focus_context


# ---------------------------------------------------------------------------
# CLAUDE.md rendering with focus_context
# ---------------------------------------------------------------------------


class TestClaudeMdFocusContext:
    def test_focus_section_rendered(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        from codegraph.context.claude_md import ClaudeMdWriter
        md = ClaudeMdWriter(out).render()
        assert "Focus: `a.py`" in md

    def test_focus_symbols_in_table(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        from codegraph.context.claude_md import ClaudeMdWriter
        md = ClaudeMdWriter(out).render()
        assert "foo" in md

    def test_no_focus_section_without_focus(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack)
        from codegraph.context.claude_md import ClaudeMdWriter
        md = ClaudeMdWriter(out).render()
        assert "Focus:" not in md

    def test_imported_by_in_output(self, tmp_db):
        store = _build_store(tmp_db)
        q = GraphQuery(store)
        pack = _make_pack()
        out = ContextCompressor(store, q).compress(pack, focus_file="a.py")
        from codegraph.context.claude_md import ClaudeMdWriter
        md = ClaudeMdWriter(out).render()
        assert "Imported by" in md and "b.py" in md
