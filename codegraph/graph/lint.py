"""Graph health checks — the maintenance pass that keeps derived knowledge honest.

Unmaintained knowledge graphs rot: file re-ingestion leaves dangling edges in
SQLite, notes outlive the symbols they annotate, LLM summaries drift from the
code they describe, and the index falls behind HEAD. ``GraphLinter`` detects
these, and ``fix=True`` applies the safe repairs (dropping dangling edges,
re-resolving note refs). Destructive repairs are never automatic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson

from codegraph.models import EdgeKind, NodeKind

# Severity levels
ERROR = "error"
WARNING = "warning"
INFO = "info"


class GraphLinter:
    """Consistency and staleness checks over a :class:`GraphStore`."""

    def __init__(
        self,
        store,
        repo_root: Path | None = None,
        codegraph_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.codegraph_dir = codegraph_dir

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def lint(self, fix: bool = False) -> dict[str, Any]:
        """Run all checks. Returns findings, per-check counts, and fixes applied."""
        findings: list[dict] = []
        fixed: dict[str, int] = {}

        findings += self._check_dangling_edges(fix, fixed)
        findings += self._check_duplicate_qualified_names()
        findings += self._check_orphan_nodes()
        findings += self._check_note_refs(fix, fixed)
        findings += self._check_missing_files()
        findings += self._check_sha_drift()
        findings += self._check_enrichment_staleness()

        if fixed:
            self.store.commit_transaction()

        summary: dict[str, int] = {}
        for f in findings:
            summary[f["check"]] = summary.get(f["check"], 0) + 1

        return {
            "findings": findings,
            "summary": summary,
            "total": len(findings),
            "fixed": fixed,
        }

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _node_exists(self, node_id: str) -> bool:
        row = self.store._db.execute(
            "SELECT 1 FROM nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        return row is not None

    def _check_dangling_edges(self, fix: bool, fixed: dict) -> list[dict]:
        """Edges whose src/dst node no longer exists (excluding synthetic commit srcs)."""
        findings: list[dict] = []
        cur = self.store._db.execute(
            """
            SELECT e.src, e.dst, e.kind, e.meta FROM edges e
            LEFT JOIN nodes ns ON e.src = ns.node_id
            LEFT JOIN nodes nd ON e.dst = nd.node_id
            WHERE ns.node_id IS NULL OR nd.node_id IS NULL
            """
        )
        commit_edge_count = 0
        module_edge_count = 0
        unresolved_base_count = 0
        relative_import_count = 0
        for src, dst, kind, meta in cur.fetchall():
            src_missing = not self._node_exists(src)
            dst_missing = not self._node_exists(dst)
            # Synthetic commit:{sha} endpoints are a known builder pattern —
            # count them separately instead of flagging each one.
            if src_missing and src.startswith("commit:"):
                commit_edge_count += 1
                continue
            # IMPORTS edges to unresolved module:{name} endpoints represent
            # external/stdlib imports by design — dependency queries rely on
            # them, so they are informational, never repaired.
            if dst_missing and not src_missing and dst.startswith("module:"):
                module_edge_count += 1
                continue
            # INHERITS edges to class:?::{name} placeholders mark bases the
            # builder could not resolve (external libs). The node's `bases`
            # field is authoritative; the placeholder edge is by design.
            if dst_missing and not src_missing and dst.startswith("class:?::"):
                unresolved_base_count += 1
                continue
            # IMPORTS edges whose dst is still a relative path (file:./x,
            # file:../x) are a cross-file-resolution gap, but the path itself
            # still carries the import information — report, never delete.
            if dst_missing and not src_missing and (
                dst.startswith("file:./") or dst.startswith("file:../")
            ):
                relative_import_count += 1
                continue
            findings.append(
                {
                    "check": "dangling_edge",
                    "severity": ERROR,
                    "subject": f"{src} -[{kind}]-> {dst}",
                    "message": "edge endpoint no longer exists",
                    "fixable": True,
                }
            )
            if fix:
                # A dead ANNOTATES target means the note's ref went stale —
                # preserve the ref string on the note before dropping the edge.
                if kind == EdgeKind.ANNOTATES and src.startswith("note:"):
                    ref = (orjson.loads(meta) or {}).get("ref")
                    if ref:
                        self._push_unresolved_ref(src, ref)
                self.store._db.execute(
                    "DELETE FROM edges WHERE src=? AND dst=? AND kind=?",
                    (src, dst, kind),
                )
                if self.store.graph.has_edge(src, dst, key=kind):
                    self.store.graph.remove_edge(src, dst, key=kind)
                fixed["dangling_edge"] = fixed.get("dangling_edge", 0) + 1

        if commit_edge_count:
            findings.append(
                {
                    "check": "synthetic_commit_edges",
                    "severity": INFO,
                    "subject": f"{commit_edge_count} MODIFIES edges",
                    "message": (
                        "edges from synthetic commit:{sha} endpoints with no "
                        "backing node (known builder pattern, harmless)"
                    ),
                    "fixable": False,
                }
            )
        if module_edge_count:
            findings.append(
                {
                    "check": "external_module_edges",
                    "severity": INFO,
                    "subject": f"{module_edge_count} IMPORTS edges",
                    "message": (
                        "imports of external/stdlib modules with no backing "
                        "node (by design, used by dependency queries)"
                    ),
                    "fixable": False,
                }
            )
        if unresolved_base_count:
            findings.append(
                {
                    "check": "unresolved_base_classes",
                    "severity": INFO,
                    "subject": f"{unresolved_base_count} INHERITS edges",
                    "message": (
                        "inheritance from unresolved/external base classes "
                        "(placeholder endpoints, by design)"
                    ),
                    "fixable": False,
                }
            )
        if relative_import_count:
            findings.append(
                {
                    "check": "unresolved_relative_imports",
                    "severity": INFO,
                    "subject": f"{relative_import_count} IMPORTS edges",
                    "message": (
                        "import targets left as relative paths by cross-file "
                        "resolution — path info preserved, never deleted"
                    ),
                    "fixable": False,
                }
            )
        return findings

    def _check_duplicate_qualified_names(self) -> list[dict]:
        """Multiple nodes sharing one qualified name — ambiguous MCP lookups."""
        findings: list[dict] = []
        seen: dict[str, list[str]] = {}
        cur = self.store._db.execute(
            "SELECT node_id, data FROM nodes WHERE kind=?", (NodeKind.FUNCTION,)
        )
        for node_id, data in cur:
            qn = orjson.loads(data).get("qualified_name")
            if qn:
                seen.setdefault(qn, []).append(node_id)
        for qn, ids in seen.items():
            if len(ids) > 1:
                findings.append(
                    {
                        "check": "duplicate_qualified_name",
                        "severity": INFO,
                        "subject": qn,
                        "message": f"defined by {len(ids)} nodes: {', '.join(ids[:4])}",
                        "fixable": False,
                    }
                )
        return findings

    def _check_orphan_nodes(self) -> list[dict]:
        """Symbol nodes with no edges at all (never linked during resolution)."""
        findings: list[dict] = []
        G = self.store.graph
        symbol_kinds = {NodeKind.CLASS, NodeKind.TYPE, NodeKind.MODULE}
        orphans = [
            nid
            for nid, data in G.nodes(data=True)
            if data.get("kind") in symbol_kinds and G.degree(nid) == 0
        ]
        if orphans:
            findings.append(
                {
                    "check": "orphan_nodes",
                    "severity": INFO,
                    "subject": f"{len(orphans)} nodes",
                    "message": (
                        "symbol nodes with no edges (first: "
                        + ", ".join(orphans[:5])
                        + ")"
                    ),
                    "fixable": False,
                }
            )
        return findings

    def _check_note_refs(self, fix: bool, fixed: dict) -> list[dict]:
        """Notes with refs that never resolved to a symbol. Fix retries resolution."""
        from codegraph.models import GraphEdge

        findings: list[dict] = []
        cur = self.store._db.execute(
            "SELECT node_id, data FROM nodes WHERE kind=?", (NodeKind.NOTE,)
        )
        for node_id, data in cur.fetchall():
            node = orjson.loads(data)
            unresolved = node.get("unresolved_refs") or []
            if not unresolved:
                continue
            still_unresolved = []
            for ref in unresolved:
                target = self._resolve_ref(ref) if fix else None
                if fix and target:
                    self.store.upsert_edge(
                        GraphEdge(
                            src=node_id,
                            dst=target,
                            kind=EdgeKind.ANNOTATES,
                            meta={"ref": ref},
                        )
                    )
                    fixed["note_ref_resolved"] = fixed.get("note_ref_resolved", 0) + 1
                else:
                    still_unresolved.append(ref)
            if fix and len(still_unresolved) != len(unresolved):
                node["unresolved_refs"] = still_unresolved
                self.store._db.execute(
                    "UPDATE nodes SET data=? WHERE node_id=?",
                    (orjson.dumps(node).decode(), node_id),
                )
                if node_id in self.store.graph:
                    self.store.graph.nodes[node_id]["unresolved_refs"] = still_unresolved
            for ref in still_unresolved:
                findings.append(
                    {
                        "check": "unresolved_note_ref",
                        "severity": WARNING,
                        "subject": node_id,
                        "message": f"note ref '{ref}' does not match any symbol",
                        "fixable": True,
                    }
                )
        return findings

    def _resolve_ref(self, ref: str) -> str | None:
        matches = self.store.find_by_name(ref)
        if not matches and "." in ref:
            candidates = self.store.find_by_name(ref.rsplit(".", 1)[-1])
            matches = [c for c in candidates if c.get("qualified_name") == ref]
        if not matches:
            return None
        matches.sort(key=lambda m: m.get("kind") == "file")
        return matches[0].get("node_id")

    def _push_unresolved_ref(self, note_id: str, ref: str) -> None:
        row = self.store._db.execute(
            "SELECT data FROM nodes WHERE node_id=?", (note_id,)
        ).fetchone()
        if not row:
            return
        node = orjson.loads(row[0])
        unresolved = node.get("unresolved_refs") or []
        if ref not in unresolved:
            unresolved.append(ref)
            node["unresolved_refs"] = unresolved
            self.store._db.execute(
                "UPDATE nodes SET data=? WHERE node_id=?",
                (orjson.dumps(node).decode(), note_id),
            )
            if note_id in self.store.graph:
                self.store.graph.nodes[note_id]["unresolved_refs"] = unresolved

    def _check_missing_files(self) -> list[dict]:
        """File nodes whose path no longer exists on disk (index behind reality)."""
        findings: list[dict] = []
        if not self.repo_root:
            return findings
        cur = self.store._db.execute(
            "SELECT node_id, data FROM nodes WHERE kind=?", (NodeKind.FILE,)
        )
        for node_id, data in cur:
            path = orjson.loads(data).get("path", "")
            if path and not (self.repo_root / path).exists():
                findings.append(
                    {
                        "check": "missing_file",
                        "severity": WARNING,
                        "subject": path,
                        "message": "indexed file no longer on disk — run `codegraph update`",
                        "fixable": False,
                    }
                )
        return findings

    def _check_sha_drift(self) -> list[dict]:
        """Is the index behind the repo HEAD?"""
        findings: list[dict] = []
        if not self.repo_root:
            return findings
        last = self.store.get_config("last_indexed_sha")
        if not last:
            return findings
        try:
            from codegraph.git.local_repo import LocalRepo

            head = LocalRepo(self.repo_root).get_head_sha()
        except Exception:
            return findings
        if head and head != last:
            findings.append(
                {
                    "check": "index_behind_head",
                    "severity": INFO,
                    "subject": f"{last[:8]} != HEAD {head[:8]}",
                    "message": "graph is behind HEAD — run `codegraph update`",
                    "fixable": False,
                }
            )
        return findings

    def _check_enrichment_staleness(self) -> list[dict]:
        """Summaries whose cache key no longer matches the symbol (stale), and
        enrichment coverage gaps. Skipped entirely if enrichment was never run."""
        findings: list[dict] = []
        if self.codegraph_dir and not (self.codegraph_dir / "llm_cache").exists():
            return findings

        from codegraph.enrichment.llm_enricher import _cache_key

        stale = 0
        missing = 0
        any_summary = False
        cur = self.store._db.execute(
            "SELECT data FROM nodes WHERE kind IN (?, ?)",
            (NodeKind.FUNCTION, NodeKind.CLASS),
        )
        for (data,) in cur:
            node = orjson.loads(data)
            summary = node.get("llm_summary")
            if not summary:
                if not node.get("docstring"):
                    missing += 1
                continue
            any_summary = True
            stored_key = node.get("llm_cache_key")
            if stored_key:
                sig = node.get("signature") or node.get("name", "")
                if _cache_key(node["node_id"], sig, node.get("docstring")) != stored_key:
                    stale += 1
                    findings.append(
                        {
                            "check": "stale_llm_summary",
                            "severity": WARNING,
                            "subject": node.get("qualified_name") or node.get("name", ""),
                            "message": "symbol changed since its LLM summary was generated",
                            "fixable": False,
                        }
                    )
        if any_summary and missing:
            findings.append(
                {
                    "check": "unenriched_symbols",
                    "severity": INFO,
                    "subject": f"{missing} symbols",
                    "message": (
                        "undocumented symbols without LLM summaries — "
                        "run `codegraph enrich` or `codegraph update --re-enrich`"
                    ),
                    "fixable": False,
                }
            )
        return findings
