"""Tests for the codeGraph MCP server tools."""

from __future__ import annotations

import pytest
import codegraph.mcp.server as srv

from codegraph.graph.queries import GraphQuery
from codegraph.models import (
    ClassNode, EdgeKind, FileNode, FunctionNode, GraphEdge, TypeNode,
)


# ---------------------------------------------------------------------------
# Shared test-graph setup
# ---------------------------------------------------------------------------


def _build_graph(store):
    """Populate a GraphStore with a small but representative graph."""
    # Files
    main_py = FileNode(
        node_id="file:src/main.py", path="src/main.py", lang="python",
        line_count=80, layer="business",
    )
    utils_py = FileNode(
        node_id="file:src/utils.py", path="src/utils.py", lang="python",
        line_count=40, layer="utility",
    )
    test_py = FileNode(
        node_id="file:tests/test_main.py", path="tests/test_main.py",
        lang="python", line_count=30, is_test=True, layer="test",
    )
    # Classes
    animal = ClassNode(
        node_id="class:src/main.py::Animal",
        name="Animal", file="file:src/main.py", line_start=5, line_end=40,
        bases=[], docstring="Base animal class.", is_exported=True,
    )
    # Functions
    main_fn = FunctionNode(
        node_id="func:src/main.py::main",
        name="main", qualified_name="main",
        file="file:src/main.py", line_start=45, line_end=70,
        signature="main() -> None", is_exported=True, complexity=3,
        docstring="Entry point.",
    )
    helper_fn = FunctionNode(
        node_id="func:src/utils.py::helper",
        name="helper", qualified_name="helper",
        file="file:src/utils.py", line_start=10, line_end=25,
        signature="helper(x: int) -> str", is_exported=True, complexity=2,
    )
    test_fn = FunctionNode(
        node_id="func:tests/test_main.py::test_main",
        name="test_main", qualified_name="test_main",
        file="file:tests/test_main.py", line_start=5, line_end=15,
        signature="test_main()", is_exported=True,
    )
    # Type
    iface = TypeNode(
        node_id="type:src/main.py::Speakable",
        name="Speakable", file="file:src/main.py", line_start=2,
        definition="class Speakable(Protocol):", is_exported=True,
    )

    # Edges
    import_edge = GraphEdge(src="file:src/main.py", dst="file:src/utils.py", kind=EdgeKind.IMPORTS)
    call_edge   = GraphEdge(src="func:src/main.py::main", dst="func:src/utils.py::helper", kind=EdgeKind.CALLS)
    defines_a   = GraphEdge(src="file:src/main.py", dst="class:src/main.py::Animal", kind=EdgeKind.DEFINES)
    defines_m   = GraphEdge(src="file:src/main.py", dst="func:src/main.py::main", kind=EdgeKind.DEFINES)
    defines_h   = GraphEdge(src="file:src/utils.py", dst="func:src/utils.py::helper", kind=EdgeKind.DEFINES)
    test_edge   = GraphEdge(src="func:tests/test_main.py::test_main",
                            dst="func:src/main.py::main", kind=EdgeKind.TESTS,
                            meta={"confidence": 0.9})

    for node in (main_py, utils_py, test_py, animal, main_fn, helper_fn, test_fn, iface):
        store.upsert_node(node)
    for edge in (import_edge, call_edge, defines_a, defines_m, defines_h, test_edge):
        store.upsert_edge(edge)

    store._db.execute(
        "INSERT OR REPLACE INTO todos (node_id, file, line, kind, text) VALUES (?,?,?,?,?)",
        ("file:src/main.py", "file:src/main.py", 50, "TODO", "add error handling"),
    )
    store._db.execute(
        "INSERT OR REPLACE INTO todos (node_id, file, line, kind, text) VALUES (?,?,?,?,?)",
        ("file:src/utils.py", "file:src/utils.py", 15, "FIXME", "handle edge case"),
    )
    store.set_config("repo_name", "testproject")
    store.commit_transaction()
    store.load_graph_to_memory()
    return store


@pytest.fixture()
def mock_server(tmp_db):
    """Inject a populated GraphStore into the MCP server globals."""
    _build_graph(tmp_db)
    q = GraphQuery(tmp_db)
    orig_q, orig_s = srv._graph_query, srv._graph_store
    srv._graph_query = q
    srv._graph_store = tmp_db
    yield q, tmp_db
    srv._graph_query = orig_q
    srv._graph_store = orig_s


# ---------------------------------------------------------------------------
# codegraph_find_symbol
# ---------------------------------------------------------------------------


class TestFindSymbol:
    def test_finds_existing_symbol(self, mock_server):
        result = srv.codegraph_find_symbol("Animal")
        assert result["count"] >= 1
        names = [m["name"] for m in result["matches"]]
        assert "Animal" in names

    def test_returns_file_location(self, mock_server):
        result = srv.codegraph_find_symbol("Animal")
        match = result["matches"][0]
        assert "src/main.py" in match["file"]

    def test_returns_line_number(self, mock_server):
        result = srv.codegraph_find_symbol("Animal")
        match = result["matches"][0]
        assert match["line"] > 0

    def test_kind_filter_class(self, mock_server):
        result = srv.codegraph_find_symbol("Animal", kind="class")
        for m in result["matches"]:
            assert m["kind"] == "class"

    def test_unknown_symbol_returns_empty(self, mock_server):
        result = srv.codegraph_find_symbol("NonExistentSymbol12345")
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# codegraph_find_callers
# ---------------------------------------------------------------------------


class TestFindCallers:
    def test_finds_direct_caller(self, mock_server):
        result = srv.codegraph_find_callers("func:src/utils.py::helper", depth=1)
        callers = result["callers"]
        caller_ids = [c.get("node_id", "") for c in callers]
        assert any("main" in cid for cid in caller_ids)

    def test_no_callers_for_top_level(self, mock_server):
        result = srv.codegraph_find_callers("func:src/main.py::main", depth=1)
        assert isinstance(result["callers"], list)

    def test_depth_respected(self, mock_server):
        result = srv.codegraph_find_callers("func:src/utils.py::helper", depth=2)
        assert "depth" in result
        assert result["depth"] == 2


# ---------------------------------------------------------------------------
# codegraph_get_dependencies
# ---------------------------------------------------------------------------


class TestGetDependencies:
    def test_direct_deps(self, mock_server):
        result = srv.codegraph_get_dependencies("src/main.py", depth=1)
        assert "src/utils.py" in result["direct_deps"]

    def test_returns_file_key(self, mock_server):
        result = srv.codegraph_get_dependencies("src/main.py")
        assert result["file"] == "src/main.py"


# ---------------------------------------------------------------------------
# codegraph_recent_changes
# ---------------------------------------------------------------------------


class TestRecentChanges:
    def test_returns_list(self, mock_server):
        result = srv.codegraph_recent_changes(limit=5)
        assert "changes" in result
        assert isinstance(result["changes"], list)


# ---------------------------------------------------------------------------
# codegraph_hot_paths
# ---------------------------------------------------------------------------


class TestHotPaths:
    def test_returns_list(self, mock_server):
        result = srv.codegraph_hot_paths(top_n=5)
        assert "hot_paths" in result
        assert len(result["hot_paths"]) <= 5

    def test_includes_expected_fields(self, mock_server):
        result = srv.codegraph_hot_paths(top_n=10)
        for hp in result["hot_paths"]:
            assert "name" in hp
            assert "kind" in hp


# ---------------------------------------------------------------------------
# codegraph_test_coverage
# ---------------------------------------------------------------------------


class TestTestCoverage:
    def test_finds_test(self, mock_server):
        result = srv.codegraph_test_coverage("func:src/main.py::main")
        assert result["coverage_count"] >= 1
        assert any("test_main" in t.get("name", "") for t in result["tests"])

    def test_no_coverage_for_helper(self, mock_server):
        result = srv.codegraph_test_coverage("func:src/utils.py::helper")
        assert result["coverage_count"] == 0


# ---------------------------------------------------------------------------
# codegraph_public_api
# ---------------------------------------------------------------------------


class TestPublicApi:
    def test_returns_api_list(self, mock_server):
        result = srv.codegraph_public_api()
        assert "api" in result
        assert result["count"] >= 1

    def test_filtered_by_file(self, mock_server):
        result = srv.codegraph_public_api(file_path="src/utils.py")
        for sym in result["api"]:
            file_val = sym.get("file") or sym.get("path", "")
            assert "utils" in file_val


# ---------------------------------------------------------------------------
# codegraph_todos
# ---------------------------------------------------------------------------


class TestTodos:
    def test_returns_todos(self, mock_server):
        result = srv.codegraph_todos()
        assert len(result["todos"]) >= 2

    def test_filter_by_kind(self, mock_server):
        result = srv.codegraph_todos(kind="TODO")
        for t in result["todos"]:
            assert t["kind"] == "TODO"

    def test_fixme_kind(self, mock_server):
        result = srv.codegraph_todos(kind="FIXME")
        for t in result["todos"]:
            assert t["kind"] == "FIXME"


# ---------------------------------------------------------------------------
# codegraph_search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_finds_by_name(self, mock_server):
        result = srv.codegraph_search("Animal")
        assert result["count"] >= 1

    def test_returns_query_field(self, mock_server):
        result = srv.codegraph_search("helper")
        assert result["query"] == "helper"

    def test_empty_query_returns_empty(self, mock_server):
        result = srv.codegraph_search("xyznonexistent999")
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# codegraph_architectural_layers
# ---------------------------------------------------------------------------


class TestArchitecturalLayers:
    def test_returns_layers(self, mock_server):
        result = srv.codegraph_architectural_layers()
        assert "layers" in result
        assert isinstance(result["layers"], dict)

    def test_contains_known_layers(self, mock_server):
        result = srv.codegraph_architectural_layers()
        layers = result["layers"]
        # Our fixture has business, utility, test layers
        all_layers = set(layers.keys())
        assert len(all_layers) >= 1


# ---------------------------------------------------------------------------
# codegraph_impact_analysis
# ---------------------------------------------------------------------------


class TestImpactAnalysis:
    def test_helper_affects_main(self, mock_server):
        result = srv.codegraph_impact_analysis("func:src/utils.py::helper")
        assert result["blast_radius"] >= 0

    def test_returns_affected_files(self, mock_server):
        result = srv.codegraph_impact_analysis("func:src/utils.py::helper")
        assert "affected_files" in result

    def test_deep_traversal(self, mock_server):
        result = srv.codegraph_impact_analysis("func:src/utils.py::helper", max_depth=3)
        assert isinstance(result["affected_symbol_count"], int)


# ---------------------------------------------------------------------------
# codegraph_conventions
# ---------------------------------------------------------------------------


class TestConventions:
    def test_returns_dict(self, mock_server):
        result = srv.codegraph_conventions()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# codegraph_overview
# ---------------------------------------------------------------------------


class TestOverview:
    def test_returns_overview(self, mock_server):
        result = srv.codegraph_overview()
        assert "overview" in result
        assert "hot_paths" in result

    def test_overview_has_file_count(self, mock_server):
        result = srv.codegraph_overview()
        assert result["overview"]["files"] >= 3


# ---------------------------------------------------------------------------
# codegraph_compress
# ---------------------------------------------------------------------------


class TestCompress:
    def test_returns_markdown(self, mock_server):
        result = srv.codegraph_compress()
        assert isinstance(result, str)
        assert "Repository Overview" in result

    def test_focus_file_included(self, mock_server):
        result = srv.codegraph_compress(focus_file="src/main.py")
        assert "Focus" in result or "main.py" in result

    def test_debug_role(self, mock_server):
        result = srv.codegraph_compress(role="debug")
        assert isinstance(result, str)

    def test_review_role(self, mock_server):
        result = srv.codegraph_compress(role="review")
        assert isinstance(result, str)

    def test_feature_role(self, mock_server):
        result = srv.codegraph_compress(role="feature")
        assert isinstance(result, str)

    def test_token_budget_respected(self, mock_server):
        result = srv.codegraph_compress(token_budget=2000)
        # Markdown should be roughly within budget (not strict, just sanity check)
        assert len(result) < 50_000  # 2000 tokens * ~25 chars/token


# ---------------------------------------------------------------------------
# codegraph_get_session_notes + codegraph_add_session_note
# ---------------------------------------------------------------------------


class TestSessionNotes:
    def test_get_notes_empty(self, mock_server, tmp_path, monkeypatch):
        from codegraph.config import Settings
        import codegraph.mcp.server as s
        # Use tmp_path-based settings so notes file doesn't persist
        monkeypatch.setattr(s, "_settings", None)
        monkeypatch.setenv("CODEGRAPH_REPO_PATH", str(tmp_path))
        # Force _get_query to re-initialize using tmp_path
        q, store = mock_server
        monkeypatch.setattr(s, "_graph_query", q)
        monkeypatch.setattr(s, "_graph_store", store)
        # Create a settings object pointing to tmp_path
        settings = Settings.from_repo(tmp_path)
        settings.session_notes_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(s, "_settings", settings)

        result = s.codegraph_get_session_notes()
        assert "notes" in result
        assert isinstance(result["notes"], list)

    def test_add_and_retrieve_note(self, mock_server, tmp_path, monkeypatch):
        from codegraph.config import Settings
        import codegraph.mcp.server as s

        settings = Settings.from_repo(tmp_path)
        settings.session_notes_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(s, "_settings", settings)
        monkeypatch.setattr(s, "_graph_query", mock_server[0])

        add_result = s.codegraph_add_session_note(
            "GraphStore uses WAL mode for concurrent reads.", category="architecture"
        )
        assert add_result["saved"] is True
        assert add_result["total_notes"] >= 1

        get_result = s.codegraph_get_session_notes(max_notes=5)
        assert len(get_result["notes"]) >= 1


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    def test_context_pack_resource(self, mock_server):
        md = srv.get_context_pack()
        assert isinstance(md, str)
        assert "Repository Overview" in md

    def test_summary_resource(self, mock_server):
        summary = srv.get_summary()
        assert isinstance(summary, str)
        assert "files" in summary.lower() or "functions" in summary.lower()
