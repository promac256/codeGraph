"""Cross-codebase TODO/FIXME/HACK/BUG aggregation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore


class TodoExtractor:
    def get_summary(self, store: "GraphStore") -> dict:
        """Return grouped counts of all TODO-style comments."""
        cur = store._db.execute(
            "SELECT kind, COUNT(*) FROM todos GROUP BY kind ORDER BY COUNT(*) DESC"
        )
        by_kind = {row[0]: row[1] for row in cur}

        cur2 = store._db.execute(
            "SELECT file, COUNT(*) as cnt FROM todos GROUP BY file "
            "ORDER BY cnt DESC LIMIT 10"
        )
        hottest_files = [{"file": row[0], "count": row[1]} for row in cur2]

        total = sum(by_kind.values())
        return {
            "total": total,
            "by_kind": by_kind,
            "hottest_files": hottest_files,
        }
