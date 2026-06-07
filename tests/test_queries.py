"""Tests for GraphQuery."""

from __future__ import annotations

import pytest

from codegraph.graph.queries import GraphQuery
from codegraph.models import EdgeKind, FileNode, FunctionNode, GraphEdge


def _populate(store):
    """Insert minimal test data."""
    f1 = FileNode(node_id="file:src/a.py", path="src/a.py", lang="python", line_count=30)
    f2 = FileNode(node_id="file:src/b.py", path="src/b.py", lang="python", line_count=20)
    fn1 = FunctionNode(
        node_id="func:src/a.py::do_thing",
        name="do_thing",
        qualified_name="do_thing",
        file="file:src/a.py",
        line_start=5,
        line_end=15,
        signature="do_thing(x: int) -> bool",
    )
    fn2 = FunctionNode(
        node_id="func:src/b.py::call_do",
        name="call_do",
        qualified_name="call_do",
        file="file:src/b.py",
        line_start=3,
        line_end=8,
    )
    for node in [f1, f2, fn1, fn2]:
        store.upsert_node(node)

    store.upsert_edge(GraphEdge(src="file:src/b.py", dst="file:src/a.py", kind=EdgeKind.IMPORTS))
    store.upsert_edge(
        GraphEdge(
            src="func:src/b.py::call_do",
            dst="func:src/a.py::do_thing",
            kind=EdgeKind.CALLS,
        )
    )
    store.commit_transaction()


class TestGraphQuery:
    def test_find_definition_exact(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        results = q.find_definition("do_thing")
        assert len(results) == 1
        assert results[0].name == "do_thing"
        assert results[0].line_start == 5

    def test_find_definition_kind_filter(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        results = q.find_definition("do_thing", kind="class")
        assert len(results) == 0

    def test_get_callers(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        callers = q.get_callers("func:src/a.py::do_thing", depth=1)
        assert len(callers) == 1
        assert callers[0]["name"] == "call_do"

    def test_get_dependencies(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        deps = q.get_dependencies("file:src/b.py", depth=1)
        assert "src/a.py" in deps["direct_deps"]

    def test_get_overview(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        ov = q.get_overview()
        assert ov["files"] == 2
        assert ov["functions"] == 2

    def test_impact_analysis(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        result = q.impact_analysis("func:src/a.py::do_thing")
        assert result["blast_radius"] >= 1
