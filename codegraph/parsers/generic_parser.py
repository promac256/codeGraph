"""Regex-based fallback parser for unsupported file types."""

from __future__ import annotations

import re
from pathlib import Path

from codegraph.models import EdgeKind, FileNode, FunctionNode, GraphEdge, NodeKind
from codegraph.parsers.base import LanguageParser, ParseResult
from codegraph.utils.hashing import make_file_id, make_func_id, sha256_bytes

_TODO_RE = re.compile(r"[/#*]+\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE)


class GenericParser(LanguageParser):
    """
    Minimal parser: extracts file metadata and TODO/FIXME comments.
    Used as a fallback for any file type without a dedicated parser.
    """

    EXTENSIONS: tuple[str, ...] = ()
    LANGUAGE_NAME = "generic"

    def can_parse(self, path: Path) -> bool:
        # Accepts any text file as a fallback
        return True

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        lines = source.decode("utf-8", errors="replace").splitlines()

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang=path.suffix.lstrip(".") or "text",
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
        )

        result = ParseResult(file_node=file_node)

        for i, line in enumerate(lines, 1):
            m = _TODO_RE.search(line)
            if m:
                result.todos.append(
                    {"line": i, "kind": m.group(1).upper(), "text": m.group(2).strip()}
                )

        return result
