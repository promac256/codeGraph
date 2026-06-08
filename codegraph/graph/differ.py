"""Graph-aware diff: compare symbol-level changes between two git refs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codegraph.git.local_repo import LocalRepo

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore
    from codegraph.parsers.registry import ParserRegistry
    from codegraph.parsers.base import ParseResult


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


@dataclass
class SymbolChange:
    kind: str             # "function" | "class" | "type"
    name: str             # short name
    qualified_name: str   # qualified (e.g. "Animal.new")
    file: str             # relative path
    change_type: str      # "added" | "removed" | "modified"
    detail: str | None = None  # what changed (e.g. "signature changed")


@dataclass
class FileDiff:
    path: str
    status: str           # A | M | D
    changes: list[SymbolChange] = field(default_factory=list)


@dataclass
class DiffResult:
    sha1: str
    sha2: str
    file_diffs: list[FileDiff] = field(default_factory=list)
    blast_radius: dict[str, list[str]] = field(default_factory=dict)
    # blast_radius: qualified_name → list[caller qualified names]

    @property
    def all_changes(self) -> list[SymbolChange]:
        return [c for fd in self.file_diffs for c in fd.changes]

    @property
    def summary(self) -> dict[str, int]:
        changes = self.all_changes
        return {
            "files": len(self.file_diffs),
            "added": sum(1 for c in changes if c.change_type == "added"),
            "removed": sum(1 for c in changes if c.change_type == "removed"),
            "modified": sum(1 for c in changes if c.change_type == "modified"),
            "affected": sum(len(v) for v in self.blast_radius.values()),
        }


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------


def _func_key(fn) -> str:
    return getattr(fn, "qualified_name", fn.name)


def _diff_functions(before: list, after: list) -> list[SymbolChange]:
    """Compare two lists of FunctionNode, returning SymbolChange entries."""
    changes: list[SymbolChange] = []

    before_map = {_func_key(f): f for f in before}
    after_map = {_func_key(f): f for f in after}

    for key, fn in after_map.items():
        if key not in before_map:
            changes.append(SymbolChange(
                kind="function", name=fn.name, qualified_name=key,
                file=getattr(fn, "file", ""), change_type="added",
            ))
        else:
            prev = before_map[key]
            details = []
            if getattr(fn, "signature", "") != getattr(prev, "signature", ""):
                details.append("signature changed")
            if getattr(fn, "is_async", False) != getattr(prev, "is_async", False):
                details.append("async changed")
            prev_cx = getattr(prev, "complexity", 1) or 1
            curr_cx = getattr(fn, "complexity", 1) or 1
            if abs(curr_cx - prev_cx) >= 3:
                details.append(f"complexity {prev_cx}→{curr_cx}")
            if details:
                changes.append(SymbolChange(
                    kind="function", name=fn.name, qualified_name=key,
                    file=getattr(fn, "file", ""), change_type="modified",
                    detail=", ".join(details),
                ))

    for key, fn in before_map.items():
        if key not in after_map:
            changes.append(SymbolChange(
                kind="function", name=fn.name, qualified_name=key,
                file=getattr(fn, "file", ""), change_type="removed",
            ))

    return changes


def _diff_classes(before: list, after: list) -> list[SymbolChange]:
    """Compare two lists of ClassNode, returning SymbolChange entries."""
    changes: list[SymbolChange] = []

    before_map = {c.name: c for c in before}
    after_map = {c.name: c for c in after}

    for name, cn in after_map.items():
        if name not in before_map:
            changes.append(SymbolChange(
                kind="class", name=name, qualified_name=name,
                file=getattr(cn, "file", ""), change_type="added",
            ))
        else:
            prev = before_map[name]
            details = []
            if getattr(cn, "bases", []) != getattr(prev, "bases", []):
                details.append("inheritance changed")
            if getattr(cn, "is_abstract", False) != getattr(prev, "is_abstract", False):
                details.append("abstract flag changed")
            if details:
                changes.append(SymbolChange(
                    kind="class", name=name, qualified_name=name,
                    file=getattr(cn, "file", ""), change_type="modified",
                    detail=", ".join(details),
                ))

    for name in before_map:
        if name not in after_map:
            cn = before_map[name]
            changes.append(SymbolChange(
                kind="class", name=name, qualified_name=name,
                file=getattr(cn, "file", ""), change_type="removed",
            ))

    return changes


def _diff_types(before: list, after: list) -> list[SymbolChange]:
    """Compare two lists of TypeNode."""
    changes: list[SymbolChange] = []
    before_map = {t.name: t for t in before}
    after_map = {t.name: t for t in after}

    for name, tn in after_map.items():
        if name not in before_map:
            changes.append(SymbolChange(
                kind="type", name=name, qualified_name=name,
                file=getattr(tn, "file", ""), change_type="added",
            ))

    for name, tn in before_map.items():
        if name not in after_map:
            changes.append(SymbolChange(
                kind="type", name=name, qualified_name=name,
                file=getattr(tn, "file", ""), change_type="removed",
            ))

    return changes


# ---------------------------------------------------------------------------
# GraphDiffer
# ---------------------------------------------------------------------------


class GraphDiffer:
    """Compare symbol-level changes between two git refs."""

    def __init__(
        self,
        repo_root: Path,
        registry: "ParserRegistry",
        store: "GraphStore | None" = None,
    ) -> None:
        self._root = repo_root
        self._registry = registry
        self._store = store

    def diff(self, sha1: str, sha2: str) -> DiffResult:
        repo = LocalRepo(self._root)
        changed = repo.get_changed_files_between(sha1, sha2)

        result = DiffResult(sha1=sha1, sha2=sha2)

        for fc in changed:
            path_str = fc["path"]
            status = fc["status"]
            path = Path(path_str)

            parser = self._registry.get_parser(path)
            if parser is None:
                continue

            fd = FileDiff(path=path_str, status=status)

            if status == "D":
                # File deleted: all symbols removed
                content_before = repo.get_file_at_sha(sha1, path_str)
                if content_before:
                    r = parser.parse(self._root / path, content_before, self._root)
                    for fn in r.functions:
                        fd.changes.append(SymbolChange(
                            kind="function", name=fn.name,
                            qualified_name=getattr(fn, "qualified_name", fn.name),
                            file=path_str, change_type="removed",
                        ))
                    for cn in r.classes:
                        fd.changes.append(SymbolChange(
                            kind="class", name=cn.name, qualified_name=cn.name,
                            file=path_str, change_type="removed",
                        ))

            elif status == "A":
                # File added: all symbols added
                content_after = repo.get_file_at_sha(sha2, path_str)
                if content_after:
                    r = parser.parse(self._root / path, content_after, self._root)
                    for fn in r.functions:
                        fd.changes.append(SymbolChange(
                            kind="function", name=fn.name,
                            qualified_name=getattr(fn, "qualified_name", fn.name),
                            file=path_str, change_type="added",
                        ))
                    for cn in r.classes:
                        fd.changes.append(SymbolChange(
                            kind="class", name=cn.name, qualified_name=cn.name,
                            file=path_str, change_type="added",
                        ))

            else:
                # File modified: symbol-level diff
                content_before = repo.get_file_at_sha(sha1, path_str)
                content_after = repo.get_file_at_sha(sha2, path_str)

                fake_path = self._root / path
                r_before = (
                    parser.parse(fake_path, content_before, self._root)
                    if content_before else _empty_result()
                )
                r_after = (
                    parser.parse(fake_path, content_after, self._root)
                    if content_after else _empty_result()
                )

                fd.changes.extend(_diff_functions(r_before.functions, r_after.functions))
                fd.changes.extend(_diff_classes(r_before.classes, r_after.classes))
                fd.changes.extend(_diff_types(r_before.types, r_after.types))

            if fd.changes:
                result.file_diffs.append(fd)

        if self._store:
            result.blast_radius = self._compute_blast_radius(result.all_changes)

        return result

    def _compute_blast_radius(self, changes: list[SymbolChange]) -> dict[str, list[str]]:
        """For each removed/modified symbol, find its callers in the current graph."""
        from codegraph.models import EdgeKind

        radius: dict[str, list[str]] = {}
        G = self._store.graph

        for change in changes:
            if change.change_type == "added":
                continue  # additions don't break existing callers

            callers = []
            # Search for nodes whose qualified_name matches
            for nid, data in G.nodes(data=True):
                qn = data.get("qualified_name") or data.get("name", "")
                if qn == change.qualified_name:
                    # Walk in-edges for CALLS and IMPORTS
                    for src, _, key in G.in_edges(nid, keys=True):
                        if key in (EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.DEFINES):
                            src_data = G.nodes.get(src, {})
                            caller_name = (
                                src_data.get("qualified_name")
                                or src_data.get("name")
                                or src
                            )
                            if caller_name and caller_name != change.qualified_name:
                                callers.append(caller_name)
                    break

            if callers:
                radius[change.qualified_name] = list(dict.fromkeys(callers))  # dedup, order-preserving

        return radius


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _empty_result():
    from codegraph.parsers.base import ParseResult
    from codegraph.models import FileNode
    from codegraph.utils.hashing import sha256_bytes

    fn = FileNode(
        node_id="file:__empty__",
        path="__empty__",
        sha256=sha256_bytes(b""),
    )
    return ParseResult(file_node=fn)
