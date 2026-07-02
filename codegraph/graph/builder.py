"""Full graph construction from a repository."""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

log = logging.getLogger(__name__)

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


def _pagerank_power_iteration(
    edges: list[tuple[str, str]],
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1.0e-6,
) -> dict[str, float]:
    """Pure-Python PageRank via power iteration.

    NetworkX's ``pagerank`` requires scipy; this fallback keeps hot-path
    ranking working when scipy is not installed. Handles dangling nodes by
    redistributing their rank uniformly. Returns {node_id: score}.
    """
    out_links: dict[str, list[str]] = {}
    nodes: set[str] = set()
    for src, dst in edges:
        out_links.setdefault(src, []).append(dst)
        nodes.add(src)
        nodes.add(dst)

    n = len(nodes)
    if n == 0:
        return {}

    rank = {node: 1.0 / n for node in nodes}
    dangling = [node for node in nodes if node not in out_links]
    base = (1.0 - alpha) / n

    for _ in range(max_iter):
        prev = rank
        dangling_mass = alpha * sum(prev[node] for node in dangling) / n
        rank = {node: base + dangling_mass for node in nodes}
        for src, dsts in out_links.items():
            share = alpha * prev[src] / len(dsts)
            for dst in dsts:
                rank[dst] += share
        if sum(abs(rank[node] - prev[node]) for node in nodes) < tol:
            break
    return rank


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
                src_file = futures[future]
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
                            for err in result.errors:
                                log.debug("parse warning in %s: %s", src_file, err)
                except Exception as e:
                    stats["errors"] += 1
                    log.warning("failed to ingest %s: %s", src_file, e)

        self._resolve_cross_file_references()
        self._compute_derived_metrics()
        stats["commits"] = self._ingest_git_history()

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
        except Exception as e:
            log.warning("parser %s crashed on %s: %s", type(parser).__name__, path, e)
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
                self.store.insert_todo(
                    result.file_node.node_id,
                    result.file_node.path,
                    todo["line"],
                    todo["kind"],
                    todo["text"],
                )

        # Update files table for change tracking
        self.store.record_file(
            result.file_node.path,
            result.file_node.lang,
            result.file_node.sha256,
            result.file_node.line_count,
        )

    def _resolve_cross_file_references(self) -> None:
        """
        Second pass: bind placeholder edge targets to real node IDs.

        - ``class:?::Name`` (inherits) resolves to a uniquely-named class.
        - ``func:?::name`` (calls) resolves to a function by name, preferring
          a same-file candidate when the name is ambiguous; call placeholders
          that can't be bound are dropped rather than left as phantom nodes.
        """
        G = self.store.graph
        class_index: dict[str, list[str]] = {}
        func_index: dict[str, list[str]] = {}
        for nid, data in G.nodes(data=True):
            name = data.get("name", "")
            if not name:
                continue
            kind = data.get("kind")
            if kind == NodeKind.CLASS:
                class_index.setdefault(name, []).append(nid)
            elif kind == NodeKind.FUNCTION:
                func_index.setdefault(name, []).append(nid)

        to_update = []  # (src, old_dst, key, new_dst, data)
        to_remove = []  # (src, dst, key) — unresolvable call placeholders
        for src, dst, key in list(G.edges(keys=True)):
            if not isinstance(dst, str):
                continue
            if dst.startswith("class:?::"):
                candidates = class_index.get(dst[len("class:?::"):], [])
                if len(candidates) == 1:
                    raw = dict(G.edges[src, dst, key])
                    edge_data = {k: v for k, v in raw.items() if k != "resolved"}
                    to_update.append((src, dst, key, candidates[0], edge_data))
            elif dst.startswith("func:?::"):
                callee = dst[len("func:?::"):]
                raw = dict(G.edges[src, dst, key])
                chosen = self._pick_call_target(
                    src, callee, func_index.get(callee, []),
                    self_call=bool(raw.get("self_call")), nodes=G.nodes,
                )
                if chosen is not None and chosen != src:
                    edge_data = {k: v for k, v in raw.items() if k != "resolved"}
                    to_update.append((src, dst, key, chosen, edge_data))
                else:
                    to_remove.append((src, dst, key))

        for src, old_dst, key, new_dst, data in to_update:
            try:
                G.remove_edge(src, old_dst, key=key)
            except Exception:
                pass
            G.add_edge(src, new_dst, key=key, resolved=True, **data)
            self.store.db_delete_edge(src, old_dst, key)
            self.store.db_upsert_edge(src, new_dst, key, {**data, "resolved": True})

        for src, dst, key in to_remove:
            try:
                G.remove_edge(src, dst, key=key)
            except Exception:
                pass
            self.store.db_delete_edge(src, dst, key)

        # Drop placeholder nodes left isolated after resolution/removal.
        for nid in [
            n for n in G.nodes
            if isinstance(n, str) and "::?::" not in n and n.startswith(("func:?::", "class:?::"))
            and G.degree(n) == 0
        ]:
            G.remove_node(nid)

        self.store._db.commit()

    @staticmethod
    def _pick_call_target(
        src_id: str,
        callee_name: str,
        candidates: list[str],
        self_call: bool = False,
        nodes=None,
    ) -> str | None:
        """Choose a callee among same-named functions.

        Resolution order:
        1. ``self``/``this``/unqualified call → the same-named method on the
           caller's own class (derived from the caller's qualified name). This
           is the precise case: it never binds a ``self.read()`` to an
           unrelated module function named ``read``.
        2. Unique name across the repo → bind it.
        3. Ambiguous name → bind only if exactly one candidate lives in the
           caller's file; otherwise give up rather than invent a false edge.
        """
        if self_call and nodes is not None and src_id.startswith("func:"):
            body = src_id[len("func:"):]
            if "::" in body:
                rel, qualified = body.split("::", 1)
                if "." in qualified:  # caller is a method → has an owning class
                    owner = qualified.rsplit(".", 1)[0]
                    candidate = f"func:{rel}::{owner}.{callee_name}"
                    if candidate != src_id and candidate in nodes:
                        return candidate

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        if src_id.startswith("func:"):
            src_rel = src_id[len("func:"):].split("::", 1)[0]
            same_file = [
                c for c in candidates
                if c.startswith("func:")
                and c[len("func:"):].split("::", 1)[0] == src_rel
            ]
            if len(same_file) == 1:
                return same_file[0]
        return None

    def _compute_derived_metrics(self) -> None:
        """Compute PageRank for hot-path ranking.

        Prefers the call graph, but parsers do not yet emit ``calls`` edges
        for every language (Python, the bulk of most repos, emits none). When
        the call graph is empty we fall back to the structural dependency
        graph so ranking still reflects real centrality instead of leaving
        every node at PageRank 0 (which made hot-paths arbitrary dict order).
        """
        G = self.store.graph

        rank_edges = [
            (s, d)
            for s, d, k in G.edges(keys=True)
            if k == EdgeKind.CALLS
        ]
        if not rank_edges:
            structural = (
                EdgeKind.IMPORTS,
                EdgeKind.INHERITS,
                EdgeKind.IMPLEMENTS,
                EdgeKind.DEFINES,
            )
            rank_edges = [
                (s, d)
                for s, d, k in G.edges(keys=True)
                if k in structural
            ]
        if not rank_edges:
            return

        rank_graph = nx.DiGraph(rank_edges)
        try:
            # NetworkX delegates pagerank to scipy; fall back to a
            # dependency-free power iteration when scipy is absent rather
            # than silently leaving every node at PageRank 0.
            pr = nx.pagerank(rank_graph, alpha=0.85)
        except ImportError:
            log.debug("scipy not installed; using pure-Python PageRank fallback")
            pr = _pagerank_power_iteration(rank_edges, alpha=0.85)
        except Exception as e:
            log.warning("PageRank computation failed: %s", e)
            return

        self.store.set_node_attr_bulk(
            "pagerank", [(score, nid) for nid, score in pr.items() if nid in G.nodes]
        )

    def _ingest_git_history(self, max_commits: int = 1000) -> int:
        """Walk git history to populate commit rows, MODIFIES edges, and the
        per-file ``commit_count`` churn signal used by hot-path ranking.

        A fresh ``init`` previously never touched git (only ``update`` did),
        so every node scored 0 commits and the churn half of the hot-path
        score was always empty. Also seeds ``last_indexed_sha`` so the first
        subsequent ``update`` diffs from HEAD rather than re-walking from the
        repo root. Returns the number of commits ingested.
        """
        import collections

        from codegraph.git.local_repo import LocalRepo
        from codegraph.models import EdgeKind, GraphEdge

        repo = LocalRepo(self.repo_root)
        commits = repo.get_commits_since(None, limit=max_commits)
        if not commits:
            log.debug("no git history found at %s", self.repo_root)
            return 0

        G = self.store.graph
        counts: collections.Counter = collections.Counter()
        with self.store.transaction():
            for c in commits:
                self.store.insert_commit(c)
                commit_id = f"commit:{c['sha']}"
                for file_path in c.get("files_changed", []):
                    file_id = f"file:{file_path}"
                    if file_id not in G.nodes:
                        continue  # file deleted since, or not a parsed source file
                    self.store.link_file_commit(file_id, c["sha"])
                    self.store.upsert_edge(
                        GraphEdge(
                            src=commit_id,
                            dst=file_id,
                            kind=EdgeKind.MODIFIES,
                            meta={},
                        )
                    )
                    counts[file_id] += 1

            self.store.set_config("last_indexed_sha", commits[0]["sha"])

        self.store.set_node_attr_bulk(
            "commit_count", [(n, fid) for fid, n in counts.items()]
        )
        return len(commits)
