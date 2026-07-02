"""Dual-layer graph storage: SQLite (durable, FTS) + NetworkX (traversal)."""

from __future__ import annotations

import gzip
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

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
        except sqlite3.OperationalError as e:
            log.warning("FTS search failed for %r: %s (run `codegraph doctor`)", query, e)
            return []

    # --- Public write/read helpers ------------------------------------------
    # These exist so callers outside this module never touch self._db directly:
    # the store stays the sole owner of the schema, callers stay unit-testable,
    # and every write funnels through one place.

    def iter_node_data(self, kinds: tuple[str, ...] | None = None) -> Iterator[dict]:
        """Yield deserialized node data dicts, optionally filtered by kind."""
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            cur = self._db.execute(
                f"SELECT data FROM nodes WHERE kind IN ({placeholders})", kinds
            )
        else:
            cur = self._db.execute("SELECT data FROM nodes")
        for (data,) in cur:
            yield orjson.loads(data)

    def get_node_data(self, node_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT data FROM nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        return orjson.loads(row[0]) if row else None

    def update_node_data(self, node_id: str, data: dict) -> None:
        """Replace a node's stored data dict (in-memory graph updated too)."""
        self._db.execute(
            "UPDATE nodes SET data=? WHERE node_id=?",
            (orjson.dumps(data).decode(), node_id),
        )
        if node_id in self.graph:
            self.graph.nodes[node_id].update(data)

    def set_node_attr_bulk(self, attr: str, updates: list[tuple[Any, str]]) -> None:
        """Set one JSON attribute on many nodes: updates = [(value, node_id)].

        The attr name is interpolated into the json_set path, so it must be a
        code-supplied identifier — never user input.
        """
        if not updates:
            return
        self._db.executemany(
            f"UPDATE nodes SET data=json_set(data,'$.{attr}',?) WHERE node_id=?",
            updates,
        )
        for value, node_id in updates:
            if node_id in self.graph:
                self.graph.nodes[node_id][attr] = value
        self._db.commit()

    def iter_edge_meta(self, kind: str) -> Iterator[dict]:
        cur = self._db.execute("SELECT meta FROM edges WHERE kind=?", (kind,))
        for (meta_json,) in cur:
            try:
                yield orjson.loads(meta_json)
            except orjson.JSONDecodeError:
                log.warning("corrupt edge meta for kind=%s skipped", kind)

    def db_delete_edge(self, src: str, dst: str, kind: str) -> None:
        """DB-only edge delete (in-memory graph managed separately by caller)."""
        self._db.execute(
            "DELETE FROM edges WHERE src=? AND dst=? AND kind=?", (src, dst, kind)
        )

    def db_upsert_edge(self, src: str, dst: str, kind: str, meta: dict) -> None:
        """DB-only edge upsert (in-memory graph managed separately by caller)."""
        self._db.execute(
            "INSERT OR REPLACE INTO edges(src,dst,kind,meta) VALUES(?,?,?,?)",
            (src, dst, kind, orjson.dumps(meta).decode()),
        )

    def insert_commit(self, commit: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO commits(sha,author,ts,message,data) VALUES(?,?,?,?,?)",
            (
                commit["sha"],
                commit.get("author", ""),
                commit.get("timestamp", 0),
                commit.get("message", ""),
                orjson.dumps(commit).decode(),
            ),
        )

    def link_file_commit(self, file_id: str, sha: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO file_commits(file,sha) VALUES(?,?)", (file_id, sha)
        )

    def insert_todo(self, node_id: str, file: str, line: int, kind: str, text: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO todos(node_id,file,line,kind,text) VALUES(?,?,?,?,?)",
            (node_id, file, line, kind, text),
        )

    def count_todos(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM todos").fetchone()[0]

    def todo_counts_by_kind(self) -> dict[str, int]:
        cur = self._db.execute(
            "SELECT kind, COUNT(*) FROM todos GROUP BY kind ORDER BY COUNT(*) DESC"
        )
        return {row[0]: row[1] for row in cur}

    def todo_hotspots(self, limit: int = 10) -> list[dict]:
        cur = self._db.execute(
            "SELECT file, COUNT(*) as cnt FROM todos GROUP BY file "
            "ORDER BY cnt DESC LIMIT ?",
            (limit,),
        )
        return [{"file": row[0], "count": row[1]} for row in cur]

    def get_file_sha(self, path: str) -> str | None:
        row = self._db.execute(
            "SELECT sha256 FROM files WHERE path=?", (path,)
        ).fetchone()
        return row[0] if row else None

    def record_file(self, path: str, lang: str, sha256: str, line_count: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO files(path,lang,sha256,last_analyzed,line_count) "
            "VALUES(?,?,?,strftime('%s','now'),?)",
            (path, lang, sha256, line_count),
        )

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
