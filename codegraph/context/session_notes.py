"""Session notes: persist architectural discoveries across coding sessions."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

_HEADER = """\
# Session Notes

Architectural discoveries, conventions, and insights accumulated across sessions.
Add new notes via `codegraph notes --add "..."` or the MCP tool `codegraph_add_session_note`.

"""

_NOTE_RE = re.compile(
    r"^### (?P<ts>[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} UTC) · (?P<cat>.+?)$",
    re.MULTILINE,
)


class SessionNotesManager:
    """Read/write per-repo session notes stored in .codegraph/session_notes.md."""

    def __init__(self, notes_path: Path) -> None:
        self._path = notes_path

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(self, note: str, category: str = "general") -> None:
        """Append a timestamped note entry."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"\n### {ts} · {category}\n\n{note.strip()}\n\n---\n"
        if not self._path.exists():
            self._path.write_text(_HEADER)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def clear(self) -> None:
        """Remove all notes, keeping the header."""
        self._path.write_text(_HEADER)

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

        Each dict has: timestamp (str), category (str), note (str).
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
            notes.append({"timestamp": ts_str, "category": category, "note": content})

        # Return newest first, capped to max_notes
        return list(reversed(notes))[:max_notes]

    def note_count(self) -> int:
        return len(_NOTE_RE.findall(self.read()))

    def exists(self) -> bool:
        return self._path.exists() and self.note_count() > 0
