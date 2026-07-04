"""Focus-file and role-aware context compressor."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from codegraph.models import EdgeKind, NodeKind

if TYPE_CHECKING:
    from codegraph.context.pack_generator import ContextPack
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore


ROLES = frozenset({"general", "debug", "review", "feature"})


class ContextCompressor:
    """
    Produces a focused ContextPack suited for a specific file or role.

    Two orthogonal axes:
      focus_file — narrow every list to entries relevant to this file first
      role       — re-weight and re-sort content to suit the task at hand
                   general | debug | review | feature
    """

    def __init__(self, store: "GraphStore", query: "GraphQuery") -> None:
        self._store = store
        self._query = query

    def compress(
        self,
        pack: "ContextPack",
        focus_file: str | None = None,
        role: str = "general",
        token_budget: int | None = None,
    ) -> "ContextPack":
        """Return a new ContextPack with focus/role adjustments applied."""
        # Copy EVERY mutable field explicitly. copy.copy() alone aliases the
        # nested dicts/lists, so mutating the compressed pack would silently
        # corrupt the original (and vice versa).
        out = copy.copy(pack)
        out.repo_overview = dict(pack.repo_overview)
        out.architectural_layers = {k: list(v) for k, v in pack.architectural_layers.items()}
        out.hot_paths = list(pack.hot_paths)
        out.recent_changes = list(pack.recent_changes)
        out.public_api_summary = list(pack.public_api_summary)
        out.top_modules = list(pack.top_modules)
        out.key_classes = list(pack.key_classes)
        out.todos = list(pack.todos)
        out.session_notes = list(pack.session_notes)
        out.pr_patterns = dict(pack.pr_patterns)
        out.focus_context = {}

        if token_budget is not None:
            out.token_budget = token_budget

        if focus_file:
            out = self._apply_focus(out, focus_file)

        role = role if role in ROLES else "general"
        if role != "general":
            out = self._apply_role(out, role, focus_file)

        return out

    # ------------------------------------------------------------------
    # Focus-file pass
    # ------------------------------------------------------------------

    def _apply_focus(self, pack: "ContextPack", focus_file: str) -> "ContextPack":
        G = self._store.graph
        file_id = focus_file if focus_file.startswith("file:") else f"file:{focus_file}"
        bare = file_id.removeprefix("file:")

        # Nodes defined in this file
        file_node_ids: set[str] = {
            nid for nid, data in G.nodes(data=True)
            if data.get("file") == file_id
        }

        # Symbols (functions, classes, types) in this file sorted by line
        symbols: list[dict] = []
        for nid in file_node_ids:
            data = G.nodes[nid]
            if data.get("kind") not in (NodeKind.FUNCTION, NodeKind.CLASS, NodeKind.TYPE):
                continue
            symbols.append({
                "node_id": nid,
                "name": data.get("name", ""),
                "kind": data.get("kind", ""),
                "qualified_name": data.get("qualified_name", data.get("name", "")),
                "line_start": data.get("line_start", 0),
                "signature": (data.get("signature") or "")[:120],
                "complexity": data.get("complexity", 1),
                "docstring": (data.get("docstring") or "")[:100],
            })
        symbols.sort(key=lambda s: s["line_start"])

        # Files that import this file (reverse imports)
        imported_by: list[str] = []
        if file_id in G:
            for src, _, key in G.in_edges(file_id, keys=True):
                if key == EdgeKind.IMPORTS:
                    imported_by.append(src.removeprefix("file:"))

        # Files/modules this file imports
        direct_imports: list[str] = []
        if file_id in G:
            for _, dst, key in G.out_edges(file_id, keys=True):
                if key == EdgeKind.IMPORTS:
                    direct_imports.append(dst.removeprefix("file:").removeprefix("module:"))

        # Callers of public functions in this file (1 hop)
        callers: list[str] = []
        for nid in file_node_ids:
            data = G.nodes.get(nid, {})
            if data.get("kind") == NodeKind.FUNCTION and not (data.get("name") or "").startswith("_"):
                for src, _, key in G.in_edges(nid, keys=True):
                    if key == EdgeKind.CALLS:
                        caller_data = G.nodes.get(src, {})
                        caller_file = caller_data.get("file", "")
                        if caller_file != file_id and caller_file:
                            callers.append(
                                f"{caller_data.get('qualified_name', caller_data.get('name', src))} "
                                f"({caller_file.removeprefix('file:')})"
                            )

        pack.focus_context = {
            "file": bare,
            "symbols": symbols,
            "imported_by": imported_by[:10],
            "imports": direct_imports[:10],
            "callers": sorted(set(callers))[:10],
        }

        # Re-sort hot_paths: focus-file entries first
        focus_ids = file_node_ids | {file_id}
        hp_focus = [h for h in pack.hot_paths if h.get("node_id") in focus_ids or h.get("file") == file_id]
        hp_other = [h for h in pack.hot_paths if h not in hp_focus]
        pack.hot_paths = hp_focus + hp_other[:max(0, 10 - len(hp_focus))]

        # Re-sort public_api_summary: focus-file entries first
        api_focus = [a for a in pack.public_api_summary if a.get("file") in (bare, file_id)]
        api_other = [a for a in pack.public_api_summary if a not in api_focus]
        pack.public_api_summary = api_focus + api_other[:max(0, 15 - len(api_focus))]

        # Re-sort key_classes: focus-file classes first
        cls_focus = [c for c in pack.key_classes if c.get("file") in (bare, file_id)]
        cls_other = [c for c in pack.key_classes if c not in cls_focus]
        pack.key_classes = cls_focus + cls_other[:max(0, 10 - len(cls_focus))]

        # Re-sort todos: focus-file todos first
        t_focus = [t for t in pack.todos if bare in t.get("file", "")]
        t_other = [t for t in pack.todos if t not in t_focus]
        pack.todos = t_focus + t_other[:max(0, 10 - len(t_focus))]

        return pack

    # ------------------------------------------------------------------
    # Role pass
    # ------------------------------------------------------------------

    def _apply_role(self, pack: "ContextPack", role: str, focus_file: str | None) -> "ContextPack":
        if role == "debug":
            # Sort hot_paths by complexity × commit_count — the most complex,
            # frequently-changed code is where bugs tend to hide.
            pack.hot_paths = sorted(
                pack.hot_paths,
                key=lambda h: h.get("complexity", 1) * (1 + h.get("commit_count", 0)),
                reverse=True,
            )[:10]
            # Keep full todos — bug markers matter when debugging.

        elif role == "review":
            # Reviewers care about the public API surface and outstanding TODOs.
            # Trim hot_paths to 5 (save budget for API and todos).
            pack.hot_paths = pack.hot_paths[:5]
            pack.todos = pack.todos[:25]
            # Sort public_api_summary alphabetically for stable reading.
            pack.public_api_summary = sorted(pack.public_api_summary, key=lambda a: a.get("name", ""))

        elif role == "feature":
            # Feature work: orient toward tests and similar patterns.
            # Demote recent_changes (less relevant), surface test-related classes.
            pack.recent_changes = pack.recent_changes[:3]
            # Move test-file entries to the front of top_modules if present.
            if hasattr(pack, "top_modules"):
                test_mods = [m for m in pack.top_modules if m.get("path", "").startswith("tests")]
                other_mods = [m for m in pack.top_modules if m not in test_mods]
                pack.top_modules = test_mods + other_mods

            # Add similar-file hints (same architectural layer as focus file)
            if focus_file:
                pack.focus_context = self._add_similar_files(pack.focus_context, focus_file)

        return pack

    def _add_similar_files(self, ctx: dict, focus_file: str) -> dict:
        G = self._store.graph
        file_id = focus_file if focus_file.startswith("file:") else f"file:{focus_file}"
        if file_id not in G:
            return ctx

        layer = G.nodes[file_id].get("layer", "unknown")
        similar: list[str] = []
        for nid, data in G.nodes(data=True):
            if (
                data.get("kind") == NodeKind.FILE
                and data.get("layer") == layer
                and nid != file_id
            ):
                similar.append(data.get("path", nid.removeprefix("file:")))

        ctx["similar_files"] = similar[:8]
        return ctx
