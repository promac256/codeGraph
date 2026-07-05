"""Tests for SessionNotesManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.context.session_notes import SessionNotesManager, _NOTE_RE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mgr(tmp_path) -> SessionNotesManager:
    return SessionNotesManager(tmp_path / "session_notes.md")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionNotesManager:
    def test_file_created_on_first_append(self, mgr, tmp_path):
        assert not mgr._path.exists()
        mgr.append("First note")
        assert mgr._path.exists()

    def test_append_creates_readable_entry(self, mgr):
        mgr.append("GraphBuilder is two-pass.", category="architecture")
        notes = mgr.read_recent()
        assert len(notes) == 1
        assert notes[0]["note"] == "GraphBuilder is two-pass."
        assert notes[0]["category"] == "architecture"

    def test_default_category_is_general(self, mgr):
        mgr.append("Some note")
        notes = mgr.read_recent()
        assert notes[0]["category"] == "general"

    def test_multiple_notes_returned_newest_first(self, mgr):
        mgr.append("First note", category="a")
        mgr.append("Second note", category="b")
        mgr.append("Third note", category="c")
        notes = mgr.read_recent()
        assert notes[0]["note"] == "Third note"
        assert notes[1]["note"] == "Second note"
        assert notes[2]["note"] == "First note"

    def test_max_notes_limit(self, mgr):
        for i in range(10):
            mgr.append(f"Note {i}")
        notes = mgr.read_recent(max_notes=3)
        assert len(notes) == 3

    def test_timestamp_format_parseable(self, mgr):
        mgr.append("note")
        notes = mgr.read_recent()
        ts = notes[0]["timestamp"]
        # Should match the regex pattern: YYYY-MM-DD HH:MM UTC
        assert _NOTE_RE.search(f"### {ts} · general") is not None

    def test_note_count(self, mgr):
        assert mgr.note_count() == 0
        mgr.append("A")
        mgr.append("B")
        assert mgr.note_count() == 2

    def test_exists_false_when_no_notes(self, mgr):
        assert not mgr.exists()

    def test_exists_true_after_append(self, mgr):
        mgr.append("Something")
        assert mgr.exists()

    def test_read_returns_empty_string_when_no_file(self, mgr):
        assert mgr.read() == ""

    def test_read_recent_empty_when_no_file(self, mgr):
        assert mgr.read_recent() == []

    def test_clear_removes_notes(self, mgr):
        mgr.append("A")
        mgr.append("B")
        mgr.clear()
        assert mgr.note_count() == 0
        assert not mgr.exists()

    def test_clear_keeps_header(self, mgr):
        mgr.append("X")
        mgr.clear()
        content = mgr.read()
        assert "Session Notes" in content

    def test_append_after_clear(self, mgr):
        mgr.append("Before clear")
        mgr.clear()
        mgr.append("After clear")
        notes = mgr.read_recent()
        assert len(notes) == 1
        assert notes[0]["note"] == "After clear"

    def test_multiline_note_preserved(self, mgr):
        multiline = "Line one.\n\nLine two.\n\nLine three."
        mgr.append(multiline)
        notes = mgr.read_recent()
        assert "Line one." in notes[0]["note"]
        assert "Line two." in notes[0]["note"]

    def test_note_with_markdown_preserved(self, mgr):
        md_note = "Use `snake_case` for functions. See **models.py** for examples."
        mgr.append(md_note)
        notes = mgr.read_recent()
        assert "`snake_case`" in notes[0]["note"]
        assert "**models.py**" in notes[0]["note"]

    def test_different_categories_preserved(self, mgr):
        mgr.append("naming convention", category="convention")
        mgr.append("avoid global state", category="warning")
        mgr.append("two-pass build", category="architecture")
        notes = mgr.read_recent()
        cats = {n["category"] for n in notes}
        assert {"convention", "warning", "architecture"} == cats


class TestGraphLayer:
    """Notes as first-class graph nodes with provenance + symbol links."""

    @pytest.fixture
    def store_with_symbol(self, tmp_db):
        from codegraph.models import ClassNode, FunctionNode

        tmp_db.upsert_node(
            FunctionNode(
                node_id="func:src/builder.py::GraphBuilder.build",
                name="build",
                qualified_name="GraphBuilder.build",
                file="file:src/builder.py",
                signature="build(self) -> dict",
            )
        )
        tmp_db.upsert_node(
            ClassNode(
                node_id="class:src/builder.py::GraphBuilder",
                name="GraphBuilder",
                file="file:src/builder.py",
            )
        )
        tmp_db.commit_transaction()
        return tmp_db

    def test_note_becomes_graph_node(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        mgr.append("Builder is two-pass.", category="architecture", source="session")

        rows = store_with_symbol._db.execute(
            "SELECT data FROM nodes WHERE kind='note'"
        ).fetchall()
        assert len(rows) == 1
        import orjson
        node = orjson.loads(rows[0][0])
        assert node["text"] == "Builder is two-pass."
        assert node["category"] == "architecture"
        assert node["source"] == "session"
        assert node["created_at"]

    def test_resolved_ref_creates_annotates_edge(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        result = mgr.append("Class note.", refs=["GraphBuilder"])

        assert result["resolved_refs"] == {
            "GraphBuilder": "class:src/builder.py::GraphBuilder"
        }
        edge = store_with_symbol._db.execute(
            "SELECT src, dst FROM edges WHERE kind='annotates'"
        ).fetchone()
        assert edge is not None
        assert edge[1] == "class:src/builder.py::GraphBuilder"
        assert edge[0].startswith("note:")
        # In-memory graph mirrors it
        assert store_with_symbol.graph.has_edge(
            edge[0], "class:src/builder.py::GraphBuilder"
        )

    def test_qualified_name_ref_resolves(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        result = mgr.append("Method note.", refs=["GraphBuilder.build"])
        assert (
            result["resolved_refs"]["GraphBuilder.build"]
            == "func:src/builder.py::GraphBuilder.build"
        )

    def test_unresolved_ref_recorded_on_node(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        result = mgr.append("Dangling note.", refs=["NoSuchSymbol"])
        assert result["unresolved_refs"] == ["NoSuchSymbol"]

        import orjson
        row = store_with_symbol._db.execute(
            "SELECT data FROM nodes WHERE kind='note'"
        ).fetchone()
        assert orjson.loads(row[0])["unresolved_refs"] == ["NoSuchSymbol"]

    def test_note_is_fts_searchable(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        mgr.append("Prefer orjson over stdlib json for serialization.")
        results = store_with_symbol.fts_search("orjson")
        assert any(r.get("kind") == "note" for r in results)

    def test_refs_and_source_roundtrip_markdown(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        mgr.append(
            "Two-pass build.", refs=["GraphBuilder", "GraphBuilder.build"],
            source="session",
        )
        notes = mgr.read_recent()
        assert notes[0]["note"] == "Two-pass build."
        assert notes[0]["refs"] == ["GraphBuilder", "GraphBuilder.build"]
        assert notes[0]["source"] == "session"

    def test_refs_parse_without_store(self, tmp_path):
        # Raw layer alone still records/parses refs & source
        mgr = SessionNotesManager(tmp_path / "notes.md")
        mgr.append("No-store note.", refs=["Foo.bar"], source="pr")
        notes = mgr.read_recent()
        assert notes[0]["refs"] == ["Foo.bar"]
        assert notes[0]["source"] == "pr"

    def test_clear_removes_note_nodes(self, store_with_symbol, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        mgr.append("A", refs=["GraphBuilder"])
        mgr.append("B")
        mgr.clear()
        count = store_with_symbol._db.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='note'"
        ).fetchone()[0]
        assert count == 0
        edges = store_with_symbol._db.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='annotates'"
        ).fetchone()[0]
        assert edges == 0

    def test_sync_graph_nodes_rebuilds_after_wipe(self, store_with_symbol, tmp_path):
        # Raw layer is ground truth: a full rebuild (init → clear_all) wipes
        # note nodes; sync_graph_nodes re-promotes them from markdown.
        mgr = SessionNotesManager(tmp_path / "notes.md", store=store_with_symbol)
        mgr.append("Linked.", refs=["GraphBuilder"], source="session")
        mgr.append("Plain.")

        # Simulate init: wipe everything, re-add only the code symbol
        store_with_symbol.clear_all()
        from codegraph.models import ClassNode
        store_with_symbol.upsert_node(
            ClassNode(
                node_id="class:src/builder.py::GraphBuilder",
                name="GraphBuilder",
                file="file:src/builder.py",
            )
        )
        store_with_symbol.commit_transaction()

        assert mgr.sync_graph_nodes() == 2
        count = store_with_symbol._db.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='note'"
        ).fetchone()[0]
        assert count == 2
        edge = store_with_symbol._db.execute(
            "SELECT dst FROM edges WHERE kind='annotates'"
        ).fetchone()
        assert edge[0] == "class:src/builder.py::GraphBuilder"

    def test_sync_without_store_is_noop(self, tmp_path):
        mgr = SessionNotesManager(tmp_path / "notes.md")
        mgr.append("A note.")
        assert mgr.sync_graph_nodes() == 0

    def test_legacy_notes_still_parse(self, tmp_path):
        # Pre-existing notes without refs/source metadata parse fine
        path = tmp_path / "notes.md"
        path.write_text(
            "# Session Notes\n\n### 2026-01-01 10:00 UTC · general\n\nOld note.\n\n---\n"
        )
        mgr = SessionNotesManager(path)
        notes = mgr.read_recent()
        assert notes[0]["note"] == "Old note."
        assert notes[0]["refs"] == []
        assert notes[0]["source"] == "manual"


class TestContextPackIntegration:
    """Verify session notes flow into the context pack."""

    def test_pack_includes_session_notes(self, tmp_db, tmp_path):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.context.session_notes import SessionNotesManager
        from codegraph.graph.queries import GraphQuery

        notes_path = tmp_path / "session_notes.md"
        mgr = SessionNotesManager(notes_path)
        mgr.append("GraphBuilder uses two-pass approach.", category="architecture")
        mgr.append("All MCP tools return dict, not Pydantic.", category="convention")

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000, notes_path=notes_path)
        pack = gen.generate()

        assert len(pack.session_notes) == 2
        assert any("two-pass" in n["note"] for n in pack.session_notes)

    def test_pack_empty_when_no_notes(self, tmp_db, tmp_path):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.graph.queries import GraphQuery

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(
            tmp_db, q, token_budget=8000, notes_path=tmp_path / "missing.md"
        )
        pack = gen.generate()
        assert pack.session_notes == []

    def test_claude_md_includes_notes(self, tmp_db, tmp_path):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.context.session_notes import SessionNotesManager
        from codegraph.graph.queries import GraphQuery

        notes_path = tmp_path / "session_notes.md"
        SessionNotesManager(notes_path).append("Two-pass build.", category="architecture")

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000, notes_path=notes_path)
        pack = gen.generate()
        md = gen.to_markdown(pack)

        assert "Session Notes" in md
        assert "Two-pass build." in md
        assert "architecture" in md

    def test_claude_md_renders_refs_and_source(self, tmp_db, tmp_path):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.context.session_notes import SessionNotesManager
        from codegraph.graph.queries import GraphQuery

        notes_path = tmp_path / "session_notes.md"
        SessionNotesManager(notes_path).append(
            "Linked note.", refs=["GraphBuilder.build"], source="session"
        )

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000, notes_path=notes_path)
        md = gen.to_markdown(gen.generate())

        assert "GraphBuilder.build" in md
        assert "source: session" in md

    def test_hot_path_notes_preferred(self, tmp_db, tmp_path):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.context.session_notes import SessionNotesManager
        from codegraph.graph.queries import GraphQuery
        from codegraph.models import FunctionNode

        # A hot symbol (high pagerank) the first note annotates
        tmp_db.upsert_node(
            FunctionNode(
                node_id="func:core.py::hot_fn",
                name="hot_fn",
                qualified_name="hot_fn",
                file="file:core.py",
                pagerank=0.9,
            )
        )
        tmp_db.commit_transaction()

        notes_path = tmp_path / "session_notes.md"
        mgr = SessionNotesManager(notes_path)
        mgr.append("Note about the hot function.", refs=["hot_fn"])
        for i in range(9):
            mgr.append(f"Unlinked filler note {i}.")

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000, notes_path=notes_path)
        pack = gen.generate()

        # The hot-linked note survives the cut to 8 despite being oldest
        assert len(pack.session_notes) == 8
        assert pack.session_notes[0]["note"] == "Note about the hot function."
