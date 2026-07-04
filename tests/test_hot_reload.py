"""The MCP server hot-reloads when another process updates the graph on disk."""

from __future__ import annotations

from codegraph.graph.store import GraphStore
from codegraph.models import FunctionNode


def test_data_version_detects_external_write(tmp_path):
    db = tmp_path / "g.db"
    reader = GraphStore(db)
    reader.open()
    reader.load_graph_to_memory()
    v0 = reader.data_version()

    # A separate connection (simulating `codegraph update`) commits a node.
    writer = GraphStore(db)
    writer.open()
    writer.upsert_node(
        FunctionNode(
            node_id="func:x.py::added_later", name="added_later",
            qualified_name="added_later", file="file:x.py",
            line_start=1, line_end=2,
        )
    )
    writer.commit_transaction()
    writer.close()

    # The reader's data_version must advance, signalling a needed reload.
    assert reader.data_version() != v0
    assert "func:x.py::added_later" not in reader.graph  # not seen yet

    reader.reload_graph()
    assert "func:x.py::added_later" in reader.graph  # picked up after reload
    reader.close()


def test_server_get_query_hot_reloads(tmp_path, monkeypatch):
    """The live MCP server picks up an external `update` without a restart."""
    import codegraph.mcp.server as srv

    repo = tmp_path
    (repo / ".codegraph").mkdir()
    db = repo / ".codegraph" / "graph.db"
    seed = GraphStore(db)
    seed.open()
    seed.upsert_node(
        FunctionNode(node_id="func:m.py::a", name="a", qualified_name="a",
                     file="file:m.py", line_start=1, line_end=2)
    )
    seed.commit_transaction()
    seed.close()

    monkeypatch.setenv("CODEGRAPH_REPO_PATH", str(repo))
    srv._graph_query = srv._graph_store = srv._settings = srv._data_version = None
    try:
        srv._get_query()
        assert "func:m.py::a" in srv._graph_store.graph
        assert "func:m.py::b" not in srv._graph_store.graph

        # Another process commits a new symbol (simulating `codegraph update`).
        writer = GraphStore(db)
        writer.open()
        writer.upsert_node(
            FunctionNode(node_id="func:m.py::b", name="b", qualified_name="b",
                         file="file:m.py", line_start=3, line_end=4)
        )
        writer.commit_transaction()
        writer.close()

        srv._get_query()  # staleness probe should trigger a hot reload
        assert "func:m.py::b" in srv._graph_store.graph
    finally:
        if srv._graph_store:
            srv._graph_store.close()
        srv._graph_query = srv._graph_store = srv._settings = srv._data_version = None


def test_reload_reflects_deletions(tmp_path):
    db = tmp_path / "g.db"
    store = GraphStore(db)
    store.open()
    store.upsert_node(
        FunctionNode(
            node_id="func:x.py::gone", name="gone", qualified_name="gone",
            file="file:x.py", line_start=1, line_end=2,
        )
    )
    store.commit_transaction()
    store.reload_graph()
    assert "func:x.py::gone" in store.graph

    # Remove it via a separate connection, then reload.
    other = GraphStore(db)
    other.open()
    other.remove_file_nodes("file:x.py")
    other.commit_transaction()
    other.close()

    store.reload_graph()
    assert "func:x.py::gone" not in store.graph  # reload drops deleted nodes
    store.close()
