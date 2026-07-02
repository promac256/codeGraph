"""Cross-codebase TODO/FIXME/HACK/BUG aggregation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore


class TodoExtractor:
    def get_summary(self, store: "GraphStore") -> dict:
        """Return grouped counts of all TODO-style comments."""
        by_kind = store.todo_counts_by_kind()

        hottest_files = store.todo_hotspots(limit=10)

        total = sum(by_kind.values())
        return {
            "total": total,
            "by_kind": by_kind,
            "hottest_files": hottest_files,
        }
