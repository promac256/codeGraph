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
