"""Tests for GraphStore."""

from __future__ import annotations

import pytest

from codegraph.models import ClassNode, EdgeKind, FileNode, FunctionNode, GraphEdge, NodeKind


class TestGraphStore:
    def test_open_creates_schema(self, tmp_db):
        cur = tmp_db._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur}
        assert "nodes" in tables
        assert "edges" in tables
        assert "todos" in tables
        assert "config" in tables

    def test_upsert_and_find_node(self, tmp_db):
        node = FileNode(
            node_id="file:src/foo.py",
            path="src/foo.py",
            lang="python",
            line_count=50,
        )
        tmp_db.upsert_node(node)
        tmp_db.commit_transaction()

        results = tmp_db.find_by_name("foo.py")
        # FileNode has no 'name' field — look by node_id
        assert "file:src/foo.py" in tmp_db.graph

    def test_upsert_and_find_function(self, tmp_db):
        fn = FunctionNode(
            node_id="func:src/foo.py::my_func",
            name="my_func",
            qualified_name="my_func",
            file="file:src/foo.py",
            line_start=10,
            line_end=20,
            signature="my_func(x: int) -> str",
        )
        tmp_db.upsert_node(fn)
        tmp_db.commit_transaction()

        results = tmp_db.find_by_name("my_func")
        assert len(results) == 1
        assert results[0]["signature"] == "my_func(x: int) -> str"

    def test_upsert_edge(self, tmp_db):
        edge = GraphEdge(
            src="file:src/a.py",
            dst="module:os",
            kind=EdgeKind.IMPORTS,
        )
        tmp_db.upsert_edge(edge)
        tmp_db.commit_transaction()

        assert tmp_db.graph.has_edge("file:src/a.py", "module:os", EdgeKind.IMPORTS)

    def test_remove_file_nodes(self, tmp_db):
        fn = FunctionNode(
            node_id="func:src/foo.py::bar",
            name="bar",
            qualified_name="bar",
            file="file:src/foo.py",
            line_start=1,
            line_end=5,
        )
        tmp_db.upsert_node(fn)
        tmp_db.commit_transaction()

        assert "func:src/foo.py::bar" in tmp_db.graph
        tmp_db.remove_file_nodes("file:src/foo.py")
        tmp_db.commit_transaction()
        assert "func:src/foo.py::bar" not in tmp_db.graph

    def test_config_get_set(self, tmp_db):
        tmp_db.set_config("repo_name", "myrepo")
        assert tmp_db.get_config("repo_name") == "myrepo"
        assert tmp_db.get_config("missing_key", "default") == "default"

    def test_load_graph_to_memory(self, tmp_db):
        fn = FunctionNode(
            node_id="func:src/foo.py::bar",
            name="bar",
            qualified_name="bar",
            file="file:src/foo.py",
            line_start=1,
            line_end=5,
        )
        tmp_db.upsert_node(fn)
        tmp_db.commit_transaction()

        # Simulate a fresh load
        import networkx as nx
        from codegraph.graph.store import GraphStore

        tmp_db.graph = nx.MultiDiGraph()  # reset
        tmp_db.load_graph_to_memory()
        assert "func:src/foo.py::bar" in tmp_db.graph
