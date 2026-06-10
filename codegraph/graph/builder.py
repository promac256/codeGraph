"""Full graph construction from a repository."""

from __future__ import annotations

import concurrent.futures
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

import networkx as nx

from codegraph.models import EdgeKind, NodeKind
from codegraph.parsers.base import ParseResult
from codegraph.utils.concurrency import cpu_bound_executor

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore
    from codegraph.parsers.base import LanguageParser
    from codegraph.parsers.registry import ParserRegistry

SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".tox", "vendor", "third_party", ".mypy_cache",
    ".pytest_cache", "coverage", ".coverage", "htmlcov", ".codegraph",
})
SKIP_EXTENSIONS = frozenset({
    ".min.js", ".min.css", ".lock", ".sum",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".whl", ".egg",
    ".pyc", ".pyo", ".so", ".dylib", ".dll",
    ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3",
    ".bin", ".dat", ".pkl", ".parquet",
})
MAX_FILE_SIZE = 512 * 1024  # 512 KB


class GraphBuilder:
    """Builds the complete knowledge graph from scratch."""

    def __init__(
        self,
        store: "GraphStore",
        registry: "ParserRegistry",
        repo_root: Path,
        max_workers: int = 8,
    ):
        self.store = store
        self.registry = registry
        self.repo_root = repo_root
        self.max_workers = max_workers

    def build(self, progress=None) -> dict:
        stats = {
            "files_scanned": 0,
            "files_parsed": 0,
            "files_skipped": 0,
            "nodes": 0,
            "edges": 0,
            "errors": 0,
        }

        all_files = list(self._enumerate_files())
        stats["files_scanned"] = len(all_files)

        task = None
        if progress is not None:
            task = progress.add_task("Parsing files...", total=len(all_files))

        with cpu_bound_executor(self.max_workers) as executor:
            futures = {
                executor.submit(self._parse_file, f): f for f in all_files
            }
            for future in concurrent.futures.as_completed(futures):
                if task is not None:
                    progress.advance(task)
                try:
                    result = future.result()
                    if result is None:
                        stats["files_skipped"] += 1
                    else:
                        with self.store.transaction():
                            self._ingest_parse_result(result)
                        stats["files_parsed"] += 1
                        if result.errors:
                            stats["errors"] += len(result.errors)
                except Exception:
                    stats["errors"] += 1

        self._resolve_cross_file_references()
        self._compute_derived_metrics()

        stats["nodes"] = self.store.graph.number_of_nodes()
        stats["edges"] = self.store.graph.number_of_edges()
        return stats

    def _enumerate_files(self) -> Iterator[Path]:
        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.relative_to(self.repo_root).parts)
            if parts & SKIP_DIRS:
                continue
            if any(path.name.endswith(ext) for ext in SKIP_EXTENSIONS):
                continue
            try:
                if path.stat().st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            yield path

    def _parse_file(self, path: Path) -> ParseResult | None:
        parser = self.registry.get_parser(path)
        if parser is None:
            return None
        try:
            source = path.read_bytes()
            return parser.parse(path, source, self.repo_root)
        except Exception:
            return None

    def _ingest_parse_result(self, result: ParseResult) -> None:
        self.store.upsert_node(result.file_node)

        for node in result.classes:
            self.store.upsert_node(node)
        for node in result.functions:
            self.store.upsert_node(node)
        for node in result.types:
            self.store.upsert_node(node)

        for edge in (
            result.imports
            + result.calls
            + result.defines
            + result.inherits
            + result.exports
        ):
            self.store.upsert_edge(edge)

        if result.todos:
            for todo in result.todos:
                self.store._db.execute(
                    "INSERT OR REPLACE INTO todos(node_id,file,line,kind,text) "
                    "VALUES(?,?,?,?,?)",
                    (
                        result.file_node.node_id,
                        result.file_node.path,
                        todo["line"],
                        todo["kind"],
                        todo["text"],
                    ),
                )

        # Update files table for change tracking
        self.store._db.execute(
            "INSERT OR REPLACE INTO files(path,lang,sha256,last_analyzed,line_count) "
            "VALUES(?,?,?,strftime('%s','now'),?)",
            (
                result.file_node.path,
                result.file_node.lang,
                result.file_node.sha256,
                result.file_node.line_count,
            ),
        )

    def _resolve_cross_file_references(self) -> None:
        """
        Second pass: resolve placeholder class IDs (class:?::ClassName)
        to real node IDs by building a name→ID index.
        """
        G = self.store.graph
        name_to_ids: dict[str, list[str]] = {}
        for nid, data in G.nodes(data=True):
            if data.get("kind") == NodeKind.CLASS:
                name = data.get("name", "")
                if name:
                    name_to_ids.setdefault(name, []).append(nid)

        to_update = []
        for src, dst, key in list(G.edges(keys=True)):
            if isinstance(dst, str) and dst.startswith("class:?::"):
                base_name = dst[len("class:?::"):]
                candidates = name_to_ids.get(base_name, [])
                if len(candidates) == 1:
                    raw = dict(G.edges[src, dst, key])
                    # strip keys that we'll set explicitly
                    edge_data = {k: v for k, v in raw.items() if k != "resolved"}
                    to_update.append((src, dst, key, candidates[0], edge_data))

        for src, old_dst, key, new_dst, data in to_update:
            try:
                G.remove_edge(src, old_dst, key=key)
            except Exception:
                pass
            G.add_edge(src, new_dst, key=key, resolved=True, **data)
            import orjson as _orjson
            meta_json = _orjson.dumps({**data, "resolved": True}).decode()
            self.store._db.execute(
                "DELETE FROM edges WHERE src=? AND dst=? AND kind=?",
                (src, old_dst, key),
            )
            self.store._db.execute(
                "INSERT OR REPLACE INTO edges(src,dst,kind,meta) VALUES(?,?,?,?)",
                (src, new_dst, key, meta_json),
            )
        self.store._db.commit()

    def _compute_derived_metrics(self) -> None:
        """Compute PageRank on call graph for hot-path ranking."""
        G = self.store.graph

        call_edges = [
            (s, d)
            for s, d, k in G.edges(keys=True)
            if k == EdgeKind.CALLS
        ]
        if not call_edges:
            return

        call_subgraph = nx.DiGraph(call_edges)
        try:
            pr = nx.pagerank(call_subgraph, alpha=0.85)
        except Exception:
            return

        updates = []
        for nid, score in pr.items():
            if nid in G.nodes:
                G.nodes[nid]["pagerank"] = score
                updates.append((score, nid))

        if updates:
            self.store._db.executemany(
                "UPDATE nodes SET data=json_set(data,'$.pagerank',?) WHERE node_id=?",
                updates,
            )
            self.store._db.commit()
