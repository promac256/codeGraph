"""Session notes: persist architectural discoveries across coding sessions.

Two layers:
  * Raw layer  — ``.codegraph/session_notes.md``, append-only markdown that
    remains the human-readable ground truth (never rewritten in place).
  * Graph layer — when a :class:`GraphStore` is provided, every note is also
    upserted as a ``note`` node with ``ANNOTATES`` edges to the code symbols
    it references, so notes are queryable and linkable like any other node.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore

_HEADER = """\
# Session Notes

Architectural discoveries, conventions, and insights accumulated across sessions.
Add new notes via `codegraph notes --add "..."` or the MCP tool `codegraph_add_session_note`.

"""

_NOTE_RE = re.compile(
    r"^### (?P<ts>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC) · (?P<cat>.+?)$",
    re.MULTILINE,
)

_REFS_RE = re.compile(r"^_refs: (?P<refs>.+?)_$", re.MULTILINE)
_SOURCE_RE = re.compile(r"^_source: (?P<source>.+?)_$", re.MULTILINE)


class SessionNotesManager:
    """Read/write per-repo session notes stored in .codegraph/session_notes.md.

    When constructed with a ``store``, notes are additionally persisted as
    graph nodes (kind ``note``) linked to referenced symbols.
    """

    def __init__(self, notes_path: Path, store: "GraphStore | None" = None) -> None:
        self._path = notes_path
        self._store = store

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        note: str,
        category: str = "general",
        refs: list[str] | None = None,
        source: str = "manual",
    ) -> dict:
        """Append a timestamped note entry.

        Args:
            note:     Note text (markdown supported).
            category: Free-form category (suggested: general, architecture,
                      convention, warning, decision).
            refs:     Symbol names/qualified names this note is about. Each
                      resolved ref becomes an ANNOTATES edge in the graph.
            source:   Provenance of the note (e.g. manual, session, pr, commit).

        Returns a dict with ``resolved_refs`` and ``unresolved_refs``.
        """
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M UTC")
        refs = [r.strip() for r in (refs or []) if r.strip()]

        body = note.strip()
        meta_lines = []
        if source and source != "manual":
            meta_lines.append(f"_source: {source}_")
        if refs:
            meta_lines.append(f"_refs: {', '.join(refs)}_")
        entry_body = body + ("\n\n" + "\n".join(meta_lines) if meta_lines else "")
        entry = f"\n### {ts} · {category}\n\n{entry_body}\n\n---\n"
        if not self._path.exists():
            self._path.write_text(_HEADER)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry)

        resolved: dict[str, str] = {}
        unresolved: list[str] = []
        if self._store is not None:
            resolved, unresolved = self._upsert_graph_node(
                note=body, category=category, refs=refs, source=source, now=now
            )
        return {
            "timestamp": ts,
            "resolved_refs": resolved,
            "unresolved_refs": unresolved if self._store is not None else refs,
        }

    def clear(self) -> None:
        """Remove all notes, keeping the header. Also drops note graph nodes."""
        self._path.write_text(_HEADER)
        if self._store is not None:
            self._remove_all_note_nodes()

    # ------------------------------------------------------------------
    # Graph layer
    # ------------------------------------------------------------------

    def _upsert_graph_node(
        self,
        note: str,
        category: str,
        refs: list[str],
        source: str,
        now: datetime,
    ) -> tuple[dict[str, str], list[str]]:
        from codegraph.models import EdgeKind, GraphEdge, NoteNode

        resolved: dict[str, str] = {}
        unresolved: list[str] = []
        for ref in refs:
            node_id = self._resolve_ref(ref)
            if node_id:
                resolved[ref] = node_id
            else:
                unresolved.append(ref)

        digest = hashlib.sha256(note.encode()).hexdigest()[:8]
        note_id = f"note:{now.strftime('%Y%m%dT%H%M%S')}-{digest}"
        first_line = note.splitlines()[0] if note else ""
        node = NoteNode(
            node_id=note_id,
            name=first_line[:80] or note_id,
            text=note,
            category=category,
            source=source,
            created_at=now.isoformat(timespec="seconds"),
            refs=refs,
            unresolved_refs=unresolved,
            docstring=note[:500],
        )
        with self._store.transaction():
            self._store.upsert_node(node)
            for ref, target_id in resolved.items():
                self._store.upsert_edge(
                    GraphEdge(
                        src=note_id,
                        dst=target_id,
                        kind=EdgeKind.ANNOTATES,
                        meta={"ref": ref},
                    )
                )
        return resolved, unresolved

    def _resolve_ref(self, ref: str) -> str | None:
        """Resolve a symbol name / qualified name to a node_id."""
        if ":" in ref:  # already a node_id
            return ref if ref in self._store.graph else None
        matches = self._store.find_by_name(ref)
        if not matches and "." in ref:
            # Qualified name like ClassName.method — match on the bare name,
            # then filter by qualified_name.
            candidates = self._store.find_by_name(ref.rsplit(".", 1)[-1])
            matches = [c for c in candidates if c.get("qualified_name") == ref]
        if not matches:
            return None
        # Prefer code symbols over files if both match
        matches.sort(key=lambda m: m.get("kind") == "file")
        return matches[0].get("node_id")

    def sync_graph_nodes(self) -> int:
        """Rebuild note nodes from the raw markdown layer.

        The markdown file is the append-only ground truth; the graph layer
        is derived. Call after a full rebuild (``codegraph init`` wipes all
        nodes) to re-promote every raw note to a node with ANNOTATES edges.
        Returns the number of notes promoted.
        """
        if self._store is None or not self.exists():
            return 0
        self._remove_all_note_nodes()
        count = 0
        for n in reversed(self.read_recent(max_notes=1_000_000)):  # oldest first
            try:
                ts = datetime.strptime(n["timestamp"], "%Y-%m-%d %H:%M UTC").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                ts = datetime.now(timezone.utc)
            self._upsert_graph_node(
                note=n["note"],
                category=n["category"],
                refs=n.get("refs", []),
                source=n.get("source", "manual"),
                now=ts,
            )
            count += 1
        return count

    def _remove_all_note_nodes(self) -> None:
        cur = self._store._db.execute("SELECT node_id FROM nodes WHERE kind='note'")
        ids = [row[0] for row in cur]
        with self._store.transaction():
            for nid in ids:
                self._store._db.execute("DELETE FROM nodes WHERE node_id=?", (nid,))
                self._store._db.execute("DELETE FROM symbols_fts WHERE node_id=?", (nid,))
                self._store._db.execute(
                    "DELETE FROM edges WHERE src=? OR dst=?", (nid, nid)
                )
                if nid in self._store.graph:
                    self._store.graph.remove_node(nid)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(self) -> str:
        """Return the full raw markdown content."""
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def read_recent(self, max_notes: int = 10) -> list[dict]:
        """Return the most recent notes as dicts, newest first.

        Each dict has: timestamp (str), category (str), note (str),
        refs (list[str]), source (str).
        """
        text = self.read()
        if not text:
            return []

        notes: list[dict] = []
        matches = list(_NOTE_RE.finditer(text))

        for i, m in enumerate(matches):
            ts_str = m.group("ts")
            category = m.group("cat").strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip().strip("-").strip()

            source = "manual"
            refs: list[str] = []
            src_m = _SOURCE_RE.search(content)
            if src_m:
                source = src_m.group("source").strip()
                content = _SOURCE_RE.sub("", content).strip()
            refs_m = _REFS_RE.search(content)
            if refs_m:
                refs = [r.strip() for r in refs_m.group("refs").split(",") if r.strip()]
                content = _REFS_RE.sub("", content).strip()

            notes.append(
                {
                    "timestamp": ts_str,
                    "category": category,
                    "note": content,
                    "refs": refs,
                    "source": source,
                }
            )

        # Return newest first, capped to max_notes
        return list(reversed(notes))[:max_notes]

    def note_count(self) -> int:
        return len(_NOTE_RE.findall(self.read()))

    def exists(self) -> bool:
        return self._path.exists() and self.note_count() > 0
