"""Tests for hot-path derived metrics in GraphBuilder.

Covers the dependency-free PageRank fallback and the structural-edge
fallback used when the parsers emit no ``calls`` edges (which is currently
the case for Python — the bulk of most repos). These guard the two-part
hot-path score (``pagerank + commit_count``) from silently collapsing to
zero, which previously made hot-path ranking arbitrary dict order.
"""

from __future__ import annotations

from codegraph.graph.builder import GraphBuilder, _pagerank_power_iteration
from codegraph.models import EdgeKind, FileNode, FunctionNode, GraphEdge
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
