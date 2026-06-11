"""Tests for hot-path derived metrics in GraphBuilder.

Covers the dependency-free PageRank fallback and the structural-edge
fallback used when the parsers emit no ``calls`` edges (which is currently
the case for Python — the bulk of most repos). These guard the two-part
hot-path score (``pagerank + commit_count``) from silently collapsing to
zero, which previously made hot-path ranking arbitrary dict order.
"""

from __future__ import annotations

from codegraph.graph.builder import GraphBuilder, _pagerank_power_iteration
from codegraph.graph.queries import GraphQuery
from codegraph.models import EdgeKind, FileNode, FunctionNode, GraphEdge
from codegraph.parsers.python_parser import PythonParser
from codegraph.parsers.registry import ParserRegistry


def test_pagerank_power_iteration_basic():
    # b -> a, c -> a : 'a' is pointed at by two nodes, should rank highest.
    edges = [("b", "a"), ("c", "a"), ("b", "c")]
    pr = _pagerank_power_iteration(edges, alpha=0.85)

    assert set(pr) == {"a", "b", "c"}
    assert pr["a"] == max(pr.values())
    # PageRank is a probability distribution — masses sum to ~1.
    assert abs(sum(pr.values()) - 1.0) < 1e-6


def test_pagerank_power_iteration_handles_dangling_and_empty():
    # 'a' is dangling (no out-links); its mass must redistribute, not vanish.
    pr = _pagerank_power_iteration([("b", "a")], alpha=0.85)
    assert abs(sum(pr.values()) - 1.0) < 1e-6
    assert _pagerank_power_iteration([]) == {}


def test_compute_derived_metrics_falls_back_to_structural_edges(tmp_db):
    """With no ``calls`` edges, PageRank should still rank via structure."""
    store = tmp_db
    a = FileNode(node_id="file:a.py", path="a.py", lang="python", line_count=10)
    b = FileNode(node_id="file:b.py", path="b.py", lang="python", line_count=10)
    fn = FunctionNode(
        node_id="func:a.py::core",
        name="core",
        qualified_name="core",
        file="file:a.py",
        line_start=1,
        line_end=5,
    )
    for node in (a, b, fn):
        store.upsert_node(node)
    # Only structural edges — no CALLS. 'core' is both defined and imported.
    store.upsert_edge(GraphEdge(src="file:a.py", dst="func:a.py::core", kind=EdgeKind.DEFINES))
    store.upsert_edge(GraphEdge(src="file:b.py", dst="func:a.py::core", kind=EdgeKind.IMPORTS))
    store.commit_transaction()

    builder = GraphBuilder(store, ParserRegistry.default(), repo_root=store.db_path.parent)
    builder._compute_derived_metrics()

    assert store.graph.nodes["func:a.py::core"].get("pagerank", 0) > 0


def test_python_parser_emits_call_edges(tmp_path):
    src = (
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def driver():\n"
        "    return helper()\n"
    )
    path = tmp_path / "mod.py"
    path.write_bytes(src.encode())
    result = PythonParser().parse(path, src.encode(), tmp_path)

    callees = {e.meta.get("callee") for e in result.calls}
    assert "helper" in callees
    # The call is attributed to its enclosing function, not module scope.
    call = next(e for e in result.calls if e.meta.get("callee") == "helper")
    assert call.src == "func:mod.py::driver"


def test_builder_resolves_calls_so_find_callers_works(tmp_db):
    """End-to-end: parsed call placeholders resolve to real callees."""
    store = tmp_db
    caller = FunctionNode(
        node_id="func:m.py::driver", name="driver", qualified_name="driver",
        file="file:m.py", line_start=1, line_end=2,
    )
    callee = FunctionNode(
        node_id="func:m.py::helper", name="helper", qualified_name="helper",
        file="file:m.py", line_start=4, line_end=5,
    )
    store.upsert_node(caller)
    store.upsert_node(callee)
    store.upsert_edge(
        GraphEdge(
            src="func:m.py::driver",
            dst="func:?::helper",
            kind=EdgeKind.CALLS,
            meta={"resolved": False, "callee": "helper"},
        )
    )
    store.commit_transaction()

    builder = GraphBuilder(store, ParserRegistry.default(), repo_root=store.db_path.parent)
    builder._resolve_cross_file_references()

    # Placeholder is gone; a resolved edge to the real callee exists.
    assert "func:?::helper" not in store.graph.nodes
    callers = GraphQuery(store).get_callers("func:m.py::helper", depth=1)
    assert any(c.get("node_id") == "func:m.py::driver" for c in callers)


def test_builder_drops_unresolvable_call_placeholder(tmp_db):
    store = tmp_db
    caller = FunctionNode(
        node_id="func:m.py::driver", name="driver", qualified_name="driver",
        file="file:m.py", line_start=1, line_end=2,
    )
    store.upsert_node(caller)
    store.upsert_edge(
        GraphEdge(
            src="func:m.py::driver",
            dst="func:?::nonexistent",
            kind=EdgeKind.CALLS,
            meta={"resolved": False, "callee": "nonexistent"},
        )
    )
    store.commit_transaction()

    builder = GraphBuilder(store, ParserRegistry.default(), repo_root=store.db_path.parent)
    builder._resolve_cross_file_references()

    # No phantom placeholder node and no dangling call edge survive.
    assert "func:?::nonexistent" not in store.graph.nodes
    calls = [k for _, _, k in store.graph.edges(keys=True) if k == EdgeKind.CALLS]
    assert calls == []
