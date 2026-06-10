"""High-level query API over the knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import networkx as nx

from codegraph.models import EdgeKind, NodeKind

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore


@dataclass
class SymbolLocation:
    node_id: str
    kind: str
    name: str
    file: str
    line_start: int
    signature: str | None = None
    docstring: str | None = None


class GraphQuery:
    """High-level query API — used by CLI commands and MCP tools."""

    def __init__(self, store: "GraphStore"):
        self.store = store

    def find_definition(
        self, symbol_name: str, kind: str = "any"
    ) -> list[SymbolLocation]:
        """Where is X defined? Exact match, then FTS fallback."""
        k = kind if kind != "any" else None
        results = self.store.find_by_name(symbol_name, k)

        if not results:
            results = self.store.find_by_name_prefix(symbol_name, k, limit=10)

        if not results:
            fts = self.store.fts_search(symbol_name, limit=10)
            if k:
                fts = [r for r in fts if r.get("kind") == k]
            results = fts

        return [self._to_location(r) for r in results]

    def get_callers(self, func_id: str, depth: int = 1) -> list[dict]:
        """What calls function Z? Traverses depth levels."""
        G = self.store.graph
        callers: list[dict] = []
        visited: set[str] = set()
        frontier = {func_id}

        for _ in range(depth):
            next_f: set[str] = set()
            for node in frontier:
                if node not in G:
                    continue
                for src, _, key in G.in_edges(node, keys=True):
                    if key == EdgeKind.CALLS and src not in visited:
                        node_data = dict(G.nodes.get(src, {}))
                        callers.append(node_data)
                        next_f.add(src)
                        visited.add(src)
            frontier = next_f

        return callers

    def get_dependencies(self, file_id: str, depth: int = 2) -> dict:
        """What does module Y depend on?"""
        all_deps = self.store.get_file_dependencies(file_id, depth)
        G = self.store.graph
        direct = []
        if file_id in G:
            for _, dst, key in G.out_edges(file_id, keys=True):
                if key == EdgeKind.IMPORTS:
                    direct.append(dst)

        return {
            "file": file_id.replace("file:", ""),
            "direct_deps": [d.replace("file:", "").replace("module:", "") for d in direct],
            "transitive_deps": [d.replace("file:", "").replace("module:", "") for d in all_deps],
            "dep_count": len(all_deps),
        }

    def get_recent_changes(self, limit: int = 10) -> list[dict]:
        """Recent commits with impacted symbols."""
        import orjson

        cur = self.store._db.execute(
            "SELECT data FROM commits ORDER BY ts DESC LIMIT ?", (limit,)
        )
        commits = [orjson.loads(row[0]) for row in cur]
        for c in commits:
            files = c.get("files_changed", [])
            impact_nodes: list[dict] = []
            for f in files[:5]:
                cur2 = self.store._db.execute(
                    "SELECT node_id, kind, name FROM nodes WHERE file=?",
                    (f"file:{f}",),
                )
                impact_nodes.extend(
                    {"node_id": r[0], "kind": r[1], "name": r[2]} for r in cur2
                )
            c["impacted_symbols"] = impact_nodes[:20]
        return commits

    def get_hot_paths(self, top_n: int = 20) -> list[dict]:
        """Most active files/functions by PageRank + commit frequency."""
        G = self.store.graph
        results = []
        for nid, data in G.nodes(data=True):
            if data.get("kind") not in (NodeKind.FUNCTION, NodeKind.FILE):
                continue
            score = data.get("pagerank", 0.0) + data.get("commit_count", 0) * 0.01
            display_name = data.get("name") or data.get("path", "")
            results.append(
                {
                    "node_id": nid,
                    "name": display_name,
                    "kind": data.get("kind", ""),
                    "file": data.get("file", data.get("path", "")),
                    "score": score,
                    "commit_count": data.get("commit_count", 0),
                    "pagerank": data.get("pagerank", 0.0),
                    "complexity": data.get("complexity", 1),
                }
            )
        return sorted(results, key=lambda x: x["score"], reverse=True)[:top_n]

    def get_test_coverage(self, symbol_id: str) -> dict:
        """What tests cover symbol Z?"""
        G = self.store.graph
        tests: list[dict] = []
        if symbol_id in G:
            for src, _, key, data in G.in_edges(symbol_id, data=True, keys=True):
                if key == EdgeKind.TESTS:
                    node_data = dict(G.nodes.get(src, {}))
                    tests.append({**node_data, "confidence": data.get("confidence", 0.0)})
        return {"symbol": symbol_id, "tests": tests, "coverage_count": len(tests)}

    def get_public_api(self, file_id: str | None = None) -> list[dict]:
        """Public (non-underscore-prefixed) exported symbols."""
        G = self.store.graph
        public: list[dict] = []
        for nid, data in G.nodes(data=True):
            if file_id and data.get("file") != file_id:
                continue
            if data.get("kind") not in (NodeKind.FUNCTION, NodeKind.CLASS, NodeKind.TYPE):
                continue
            name = data.get("name", "")
            if name.startswith("_"):
                continue
            qualified = data.get("qualified_name", name)
            if "." in qualified and not data.get("kind") == NodeKind.CLASS:
                continue  # skip private methods appearing as public
            public.append(data)
        return public

    def get_architectural_layers(self) -> dict[str, list[str]]:
        """Files grouped by architectural layer."""
        layers: dict[str, list[str]] = {}
        for nid, data in self.store.graph.nodes(data=True):
            if data.get("kind") == NodeKind.FILE:
                layer = data.get("layer") or "unknown"
                layers.setdefault(layer, []).append(nid)
        return layers

    def get_todos(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        """All TODO/FIXME/HACK/BUG comments."""
        if kind:
            cur = self.store._db.execute(
                "SELECT file, line, kind, text FROM todos WHERE kind=? ORDER BY file LIMIT ?",
                (kind.upper(), limit),
            )
        else:
            cur = self.store._db.execute(
                "SELECT file, line, kind, text FROM todos ORDER BY file LIMIT ?",
                (limit,),
            )
        return [
            {"file": r[0], "line": r[1], "kind": r[2], "text": r[3]} for r in cur
        ]

    def impact_analysis(self, symbol_id: str, max_depth: int = 3) -> dict:
        """Blast radius: what code would be affected if this symbol changes?"""
        G = self.store.graph
        affected: set[str] = set()
        frontier = {symbol_id}

        for _ in range(max_depth):
            next_f: set[str] = set()
            for node in frontier:
                if node not in G:
                    continue
                for src, _, key in G.in_edges(node, keys=True):
                    if key in (EdgeKind.CALLS, EdgeKind.TESTS, EdgeKind.IMPORTS):
                        if src not in affected:
                            affected.add(src)
                            next_f.add(src)
            frontier = next_f

        affected_files = {
            G.nodes[n].get("file", "")
            for n in affected
            if n in G.nodes and G.nodes[n].get("file")
        }
        affected_tests = [
            n for n in affected
            if n in G.nodes and G.nodes[n].get("kind") == NodeKind.TEST
        ]

        return {
            "symbol": symbol_id,
            "affected_symbol_count": len(affected),
            "affected_files": [f.replace("file:", "") for f in affected_files if f],
            "affected_tests": affected_tests,
            "blast_radius": len(affected),
        }

    def get_overview(self) -> dict[str, Any]:
        G = self.store.graph
        file_count = sum(1 for _, d in G.nodes(data=True) if d.get("kind") == NodeKind.FILE)
        func_count = sum(1 for _, d in G.nodes(data=True) if d.get("kind") == NodeKind.FUNCTION)
        class_count = sum(1 for _, d in G.nodes(data=True) if d.get("kind") == NodeKind.CLASS)
        test_count = sum(1 for _, d in G.nodes(data=True) if d.get("is_test"))
        langs: dict[str, int] = {}
        for _, data in G.nodes(data=True):
            if data.get("kind") == NodeKind.FILE and data.get("lang"):
                lang = data["lang"]
                langs[lang] = langs.get(lang, 0) + 1
        return {
            "files": file_count,
            "functions": func_count,
            "classes": class_count,
            "test_files": test_count,
            "languages": langs,
            "edge_count": G.number_of_edges(),
            "node_count": G.number_of_nodes(),
        }

    def _to_location(self, data: dict) -> SymbolLocation:
        return SymbolLocation(
            node_id=data.get("node_id", ""),
            kind=data.get("kind", ""),
            name=data.get("name", ""),
            file=data.get("file", data.get("path", "")),
            line_start=data.get("line_start", 0),
            signature=data.get("signature"),
            docstring=data.get("docstring"),
        )
