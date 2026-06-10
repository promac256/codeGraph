"""Dual-layer graph storage: SQLite (durable, FTS) + NetworkX (traversal)."""

from __future__ import annotations

import gzip
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import networkx as nx
import orjson

from codegraph.models import BaseNode, EdgeKind, GraphEdge, NodeKind

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    name    TEXT,
    file    TEXT,
    data    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    src  TEXT NOT NULL,
    dst  TEXT NOT NULL,
    kind TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, dst, kind)
);
CREATE TABLE IF NOT EXISTS files (
    path          TEXT PRIMARY KEY,
    lang          TEXT,
    sha256        TEXT,
    last_analyzed INTEGER,
    line_count    INTEGER
);
CREATE TABLE IF NOT EXISTS commits (
    sha     TEXT PRIMARY KEY,
    author  TEXT,
    ts      INTEGER,
    message TEXT,
    data    TEXT
);
CREATE TABLE IF NOT EXISTS file_commits (
    file TEXT,
    sha  TEXT,
    PRIMARY KEY (file, sha)
);
CREATE TABLE IF NOT EXISTS todos (
    node_id TEXT,
    file    TEXT,
    line    INTEGER,
    kind    TEXT,
    text    TEXT
);
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    node_id UNINDEXED,
    name,
    docstring
);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file);
CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_fc_file    ON file_commits(file);
CREATE INDEX IF NOT EXISTS idx_fc_sha     ON file_commits(sha);
"""


class GraphStore:
    """
    Dual-layer graph storage.

    SQLite provides durable indexed symbol lookup and FTS5 full-text search.
    NetworkX MultiDiGraph provides traversal algorithms (PageRank, BFS, etc.).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: sqlite3.Connection | None = None
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()

    def open(self) -> None:
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA cache_size=-65536")
        self._db.executescript(SCHEMA_SQL)
        self._db.commit()

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    def load_graph_to_memory(self) -> None:
        cur = self._db.execute("SELECT node_id, kind, data FROM nodes")
        for node_id, kind, data in cur:
            attrs = orjson.loads(data)
            # attrs already contains 'kind'; avoid passing it twice
            attrs.setdefault("kind", kind)
            self.graph.add_node(node_id, **attrs)

        cur = self._db.execute("SELECT src, dst, kind, meta FROM edges")
        for src, dst, kind, meta in cur:
            self.graph.add_edge(src, dst, key=kind, **orjson.loads(meta))

    def save_snapshot(self, snapshot_path: Path) -> None:
        data = nx.node_link_data(self.graph)
        with gzip.open(snapshot_path, "wb") as f:
            f.write(orjson.dumps(data))

    def load_snapshot(self, snapshot_path: Path) -> None:
        with gzip.open(snapshot_path, "rb") as f:
            data = orjson.loads(f.read())
        self.graph = nx.node_link_graph(data)

    def upsert_node(self, node: BaseNode) -> None:
        data = node.model_dump_json()
        name = getattr(node, "name", None)
        self._db.execute(
            "INSERT OR REPLACE INTO nodes(node_id,kind,name,file,data) VALUES(?,?,?,?,?)",
            (node.node_id, node.kind, name, getattr(node, "file", None), data),
        )
        # Keep FTS index in sync (standalone, not a content table)
        docstring = (getattr(node, "docstring", None) or "")[:500]
        self._db.execute("DELETE FROM symbols_fts WHERE node_id=?", (node.node_id,))
        self._db.execute(
            "INSERT INTO symbols_fts(node_id, name, docstring) VALUES(?,?,?)",
            (node.node_id, name or "", docstring),
        )
        self.graph.add_node(node.node_id, **node.model_dump())

    def upsert_edge(self, edge: GraphEdge) -> None:
        meta_json = orjson.dumps(edge.meta).decode()
        self._db.execute(
            "INSERT OR REPLACE INTO edges(src,dst,kind,meta) VALUES(?,?,?,?)",
            (edge.src, edge.dst, edge.kind, meta_json),
        )
        self.graph.add_edge(edge.src, edge.dst, key=edge.kind, **edge.meta)

    def remove_file_nodes(self, file_id: str) -> None:
        """Remove all nodes belonging to a file before re-indexing it."""
        cur = self._db.execute(
            "SELECT node_id FROM nodes WHERE file=? OR node_id=?",
            (file_id, file_id),
        )
        ids = [row[0] for row in cur]
        for nid in ids:
            self._db.execute("DELETE FROM nodes WHERE node_id=?", (nid,))
            self._db.execute("DELETE FROM symbols_fts WHERE node_id=?", (nid,))
            if nid in self.graph:
                self.graph.remove_node(nid)
        self._db.execute(
            "DELETE FROM edges WHERE src=? OR dst=?", (file_id, file_id)
        )
        self._db.execute("DELETE FROM todos WHERE file=?", (file_id,))

    def clear_all(self) -> None:
        """Wipe all indexed data for a fresh rebuild."""
        tables = ["nodes", "edges", "files", "commits", "file_commits", "todos"]
        for t in tables:
            self._db.execute(f"DELETE FROM {t}")
        try:
            self._db.execute("DELETE FROM symbols_fts")
        except sqlite3.OperationalError:
            pass
        self._db.commit()
        self.graph.clear()

    def commit_transaction(self) -> None:
        self._db.commit()

    @contextmanager
    def transaction(self):
        try:
            yield
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

    # --- Query helpers ---

    def find_by_name(self, name: str, kind: str | None = None) -> list[dict]:
        if kind:
            cur = self._db.execute(
                "SELECT data FROM nodes WHERE name=? AND kind=?", (name, kind)
            )
        else:
            cur = self._db.execute("SELECT data FROM nodes WHERE name=?", (name,))
        return [orjson.loads(row[0]) for row in cur]

    def find_by_name_prefix(self, prefix: str, kind: str | None = None, limit: int = 20) -> list[dict]:
        pattern = prefix + "%"
        if kind:
            cur = self._db.execute(
                "SELECT data FROM nodes WHERE name LIKE ? AND kind=? LIMIT ?",
                (pattern, kind, limit),
            )
        else:
            cur = self._db.execute(
                "SELECT data FROM nodes WHERE name LIKE ? LIMIT ?", (pattern, limit)
            )
        return [orjson.loads(row[0]) for row in cur]

    def fts_search(self, query: str, limit: int = 20) -> list[dict]:
        try:
            cur = self._db.execute(
                """SELECT n.data FROM symbols_fts f
                   JOIN nodes n ON f.node_id = n.node_id
                   WHERE symbols_fts MATCH ? LIMIT ?""",
                (query, limit),
            )
            return [orjson.loads(row[0]) for row in cur]
        except sqlite3.OperationalError:
            return []

    def get_callers(self, func_id: str) -> list[str]:
        result = []
        if func_id not in self.graph:
            return result
        for src, _, key in self.graph.in_edges(func_id, keys=True):
            if key == EdgeKind.CALLS:
                result.append(src)
        return result

    def get_callees(self, func_id: str) -> list[str]:
        result = []
        if func_id not in self.graph:
            return result
        for _, dst, key in self.graph.out_edges(func_id, keys=True):
            if key == EdgeKind.CALLS:
                result.append(dst)
        return result

    def get_file_dependencies(self, file_id: str, depth: int = 2) -> list[str]:
        visited: set[str] = set()
        frontier = {file_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                if node not in self.graph:
                    continue
                for _, dst, key in self.graph.out_edges(node, keys=True):
                    if key == EdgeKind.IMPORTS and dst not in visited:
                        next_frontier.add(dst)
            visited.update(frontier)
            frontier = next_frontier
        return list(visited - {file_id})

    def get_config(self, key: str, default: str = "") -> str:
        row = self._db.execute(
            "SELECT value FROM config WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else default

    def set_config(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, value)
        )
        self._db.commit()

    def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for kind in NodeKind:
            row = self._db.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind=?", (kind,)
            ).fetchone()
            stats[f"{kind}_count"] = row[0] if row else 0
        row = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()
        stats["edge_count"] = row[0] if row else 0
        return stats
