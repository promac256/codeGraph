"""Tests for GraphLinter — graph health checks and safe repairs."""

from __future__ import annotations

import orjson
import pytest

from codegraph.graph.lint import GraphLinter
from codegraph.models import EdgeKind, FileNode, FunctionNode, GraphEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_function(store, node_id, name, qualified_name=None, **kwargs):
    store.upsert_node(
        FunctionNode(
            node_id=node_id,
            name=name,
            qualified_name=qualified_name or name,
            file=kwargs.pop("file", "file:src/a.py"),
            **kwargs,
        )
    )


def _checks(result, check):
    return [f for f in result["findings"] if f["check"] == check]


@pytest.fixture
def linter(tmp_db):
    return GraphLinter(tmp_db)


# ---------------------------------------------------------------------------
# Dangling edges
# ---------------------------------------------------------------------------


class TestDanglingEdges:
    def test_clean_graph_has_no_findings(self, tmp_db, linter):
        _add_function(tmp_db, "func:a", "a")
        _add_function(tmp_db, "func:b", "b", qualified_name="b")
        tmp_db.upsert_edge(GraphEdge(src="func:a", dst="func:b", kind=EdgeKind.CALLS))
        tmp_db.commit_transaction()

        result = linter.lint()
        assert not _checks(result, "dangling_edge")

    def test_detects_dangling_edge(self, tmp_db, linter):
        _add_function(tmp_db, "func:a", "a")
        tmp_db.upsert_edge(
            GraphEdge(src="func:a", dst="func:gone", kind=EdgeKind.CALLS)
        )
        tmp_db.commit_transaction()

        result = linter.lint()
        findings = _checks(result, "dangling_edge")
        assert len(findings) == 1
        assert findings[0]["severity"] == "error"
        assert "func:gone" in findings[0]["subject"]

    def test_fix_removes_dangling_edge(self, tmp_db, linter):
        _add_function(tmp_db, "func:a", "a")
        tmp_db.upsert_edge(
            GraphEdge(src="func:a", dst="func:gone", kind=EdgeKind.CALLS)
        )
        tmp_db.commit_transaction()

        result = linter.lint(fix=True)
        assert result["fixed"].get("dangling_edge") == 1
        # Second run is clean
        assert not _checks(linter.lint(), "dangling_edge")

    def test_synthetic_commit_edges_are_info_not_error(self, tmp_db, linter):
        tmp_db.upsert_node(FileNode(node_id="file:src/a.py", path="src/a.py"))
        tmp_db.upsert_edge(
            GraphEdge(src="commit:abc123", dst="file:src/a.py", kind=EdgeKind.MODIFIES)
        )
        tmp_db.commit_transaction()

        result = linter.lint()
        assert not _checks(result, "dangling_edge")
        info = _checks(result, "synthetic_commit_edges")
        assert len(info) == 1
        assert info[0]["severity"] == "info"

    def test_external_module_edges_are_info_not_error(self, tmp_db, linter):
        tmp_db.upsert_node(FileNode(node_id="file:src/a.py", path="src/a.py"))
        tmp_db.upsert_edge(
            GraphEdge(src="file:src/a.py", dst="module:pytest", kind=EdgeKind.IMPORTS)
        )
        tmp_db.commit_transaction()

        result = linter.lint(fix=True)
        assert not _checks(result, "dangling_edge")
        info = _checks(result, "external_module_edges")
        assert len(info) == 1 and info[0]["severity"] == "info"
        # Never repaired — dependency queries rely on these edges
        row = tmp_db._db.execute(
            "SELECT COUNT(*) FROM edges WHERE dst='module:pytest'"
        ).fetchone()
        assert row[0] == 1

    def test_fix_preserves_note_ref_before_dropping_edge(self, tmp_db, linter):
        from codegraph.models import NoteNode

        tmp_db.upsert_node(
            NoteNode(node_id="note:n1", name="n", text="about a symbol")
        )
        tmp_db.upsert_edge(
            GraphEdge(
                src="note:n1",
                dst="func:deleted",
                kind=EdgeKind.ANNOTATES,
                meta={"ref": "deleted_fn"},
            )
        )
        tmp_db.commit_transaction()

        linter.lint(fix=True)
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='note:n1'"
        ).fetchone()
        assert "deleted_fn" in orjson.loads(row[0])["unresolved_refs"]


# ---------------------------------------------------------------------------
# Duplicate qualified names
# ---------------------------------------------------------------------------


class TestDuplicateQualifiedNames:
    def test_detects_duplicates(self, tmp_db, linter):
        _add_function(tmp_db, "func:src/a.py::run", "run", file="file:src/a.py")
        _add_function(tmp_db, "func:src/b.py::run", "run", file="file:src/b.py")
        tmp_db.commit_transaction()

        findings = _checks(linter.lint(), "duplicate_qualified_name")
        assert len(findings) == 1
        assert findings[0]["subject"] == "run"

    def test_unique_names_pass(self, tmp_db, linter):
        _add_function(tmp_db, "func:a", "alpha")
        _add_function(tmp_db, "func:b", "beta")
        tmp_db.commit_transaction()
        assert not _checks(linter.lint(), "duplicate_qualified_name")


# ---------------------------------------------------------------------------
# Note refs
# ---------------------------------------------------------------------------


class TestNoteRefs:
    def test_unresolved_ref_flagged(self, tmp_db, linter, tmp_path):
        from codegraph.context.session_notes import SessionNotesManager

        mgr = SessionNotesManager(tmp_path / "notes.md", store=tmp_db)
        mgr.append("Dangling.", refs=["MissingSymbol"])

        findings = _checks(linter.lint(), "unresolved_note_ref")
        assert len(findings) == 1
        assert "MissingSymbol" in findings[0]["message"]

    def test_fix_reresolves_when_symbol_appears(self, tmp_db, linter, tmp_path):
        from codegraph.context.session_notes import SessionNotesManager

        mgr = SessionNotesManager(tmp_path / "notes.md", store=tmp_db)
        mgr.append("Early note.", refs=["LateSymbol"])

        # Symbol shows up later (e.g. after the next update)
        _add_function(tmp_db, "func:late", "LateSymbol")
        tmp_db.commit_transaction()

        result = linter.lint(fix=True)
        assert result["fixed"].get("note_ref_resolved") == 1
        edge = tmp_db._db.execute(
            "SELECT dst FROM edges WHERE kind='annotates'"
        ).fetchone()
        assert edge[0] == "func:late"
        # Second run is clean
        assert not _checks(linter.lint(), "unresolved_note_ref")


# ---------------------------------------------------------------------------
# Missing files / SHA drift
# ---------------------------------------------------------------------------


class TestRepoDrift:
    def test_missing_file_flagged(self, tmp_db, tmp_path):
        tmp_db.upsert_node(FileNode(node_id="file:gone.py", path="gone.py"))
        tmp_db.commit_transaction()

        linter = GraphLinter(tmp_db, repo_root=tmp_path)
        findings = _checks(linter.lint(), "missing_file")
        assert len(findings) == 1
        assert findings[0]["subject"] == "gone.py"

    def test_existing_file_passes(self, tmp_db, tmp_path):
        (tmp_path / "here.py").write_text("x = 1\n")
        tmp_db.upsert_node(FileNode(node_id="file:here.py", path="here.py"))
        tmp_db.commit_transaction()

        linter = GraphLinter(tmp_db, repo_root=tmp_path)
        assert not _checks(linter.lint(), "missing_file")

    def test_no_repo_root_skips_file_checks(self, tmp_db, linter):
        tmp_db.upsert_node(FileNode(node_id="file:gone.py", path="gone.py"))
        tmp_db.commit_transaction()
        assert not _checks(linter.lint(), "missing_file")


# ---------------------------------------------------------------------------
# Enrichment staleness
# ---------------------------------------------------------------------------


class TestEnrichmentStaleness:
    def test_stale_summary_flagged(self, tmp_db, linter):
        from codegraph.enrichment.llm_enricher import _cache_key

        # Summary generated for the OLD signature; node now has a new one
        old_key = _cache_key("func:s", "old_sig()", None)
        node = FunctionNode(
            node_id="func:s",
            name="s",
            qualified_name="s",
            file="file:a.py",
            signature="new_sig(x)",
            llm_summary="Outdated.",
        )
        tmp_db.upsert_node(node)
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='func:s'"
        ).fetchone()
        data = orjson.loads(row[0])
        data["llm_cache_key"] = old_key
        tmp_db._db.execute(
            "UPDATE nodes SET data=? WHERE node_id=?",
            (orjson.dumps(data).decode(), "func:s"),
        )
        tmp_db.commit_transaction()

        findings = _checks(linter.lint(), "stale_llm_summary")
        assert len(findings) == 1
        assert findings[0]["severity"] == "warning"

    def test_fresh_summary_passes(self, tmp_db, linter):
        from codegraph.enrichment.llm_enricher import _cache_key

        key = _cache_key("func:f", "sig()", None)
        node = FunctionNode(
            node_id="func:f",
            name="f",
            qualified_name="f",
            file="file:a.py",
            signature="sig()",
            llm_summary="Fresh.",
        )
        tmp_db.upsert_node(node)
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='func:f'"
        ).fetchone()
        data = orjson.loads(row[0])
        data["llm_cache_key"] = key
        tmp_db._db.execute(
            "UPDATE nodes SET data=? WHERE node_id=?",
            (orjson.dumps(data).decode(), "func:f"),
        )
        tmp_db.commit_transaction()

        assert not _checks(linter.lint(), "stale_llm_summary")


# ---------------------------------------------------------------------------
# Result shape / healthy graph
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_empty_graph_is_healthy(self, linter):
        result = linter.lint()
        assert result["total"] == 0
        assert result["findings"] == []
        assert result["fixed"] == {}

    def test_summary_counts_by_check(self, tmp_db, linter):
        _add_function(tmp_db, "func:a", "a")
        tmp_db.upsert_edge(GraphEdge(src="func:a", dst="func:g1", kind=EdgeKind.CALLS))
        tmp_db.upsert_edge(GraphEdge(src="func:a", dst="func:g2", kind=EdgeKind.CALLS))
        tmp_db.commit_transaction()

        result = linter.lint()
        assert result["summary"]["dangling_edge"] == 2
        assert result["total"] >= 2
