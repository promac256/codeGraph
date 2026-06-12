"""Base class and result type for all language parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from codegraph.models import (
    ClassNode,
    EdgeKind,
    FileNode,
    FunctionNode,
    GraphEdge,
    TypeNode,
)

# Method names that overwhelmingly belong to language/stdlib types rather than
# project code. Calls like ``path.exists()`` or ``buf.read()`` would otherwise
# be mis-bound to a same-named project function. Only applied to ``attr``-scope
# calls (unknown receiver). Deliberately conservative: ambiguous names that are
# also common project methods (get/set/update/add/run/build/...) are excluded.
_BUILTIN_METHODS = frozenset({
    # containers / iterables
    "append", "extend", "keys", "values", "items", "setdefault", "popitem",
    # io / files
    "read", "readline", "readlines", "write", "writelines", "flush",
    "seek", "tell",
    # strings
    "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines",
    "encode", "decode", "lower", "upper", "title", "capitalize",
    "startswith", "endswith", "isdigit", "isalpha", "isalnum", "isspace",
    # pathlib / os
    "exists", "is_file", "is_dir", "mkdir", "resolve", "glob", "rglob",
    "unlink", "iterdir", "absolute", "as_posix",
    "read_text", "write_text", "read_bytes", "write_bytes",
})


@dataclass
class ParseResult:
    file_node: FileNode
    functions: list[FunctionNode] = field(default_factory=list)
    classes: list[ClassNode] = field(default_factory=list)
    types: list[TypeNode] = field(default_factory=list)
    imports: list[GraphEdge] = field(default_factory=list)
    calls: list[GraphEdge] = field(default_factory=list)
    defines: list[GraphEdge] = field(default_factory=list)
    inherits: list[GraphEdge] = field(default_factory=list)
    exports: list[GraphEdge] = field(default_factory=list)
    todos: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class LanguageParser(ABC):
    EXTENSIONS: tuple[str, ...] = ()
    LANGUAGE_NAME: str = ""

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.EXTENSIONS

    @abstractmethod
    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        ...

    def _enclosing_function(self, line: int, functions: list[FunctionNode]):
        """Return the most tightly-scoped function whose span contains ``line``."""
        best = None
        for fn in functions:
            if fn.line_start <= line <= fn.line_end:
                if best is None or (fn.line_end - fn.line_start) < (
                    best.line_end - best.line_start
                ):
                    best = fn
        return best

    def _emit_call_edges(self, call_sites, result: "ParseResult") -> None:
        """Build CALLS edges from call sites to their enclosing functions.

        ``call_sites`` is an iterable of ``(callee_name, line)`` or
        ``(callee_name, line, scope)``. ``scope`` is one of:

        - ``"self"`` — call on the enclosing instance (``self``/``cls``/``this``
          or an unqualified method call); the builder binds it to the caller's
          own class first.
        - ``"attr"`` — call on some other receiver (``obj.method()``); the
          receiver's type is unknown, so a call to a well-known builtin/stdlib
          method name (``path.exists()``, ``f.read()``) is dropped rather than
          mis-bound to a project function that merely shares the name.
        - ``"free"`` — a bare ``func()`` call (default).

        ``scope`` also accepts a legacy ``bool`` where ``True`` means ``self``.
        Callees become unresolved ``func:?::name`` placeholders, bound in the
        builder's cross-file pass. Sites with no enclosing function and
        duplicate (caller, callee, line) triples are skipped.
        """
        seen: set[tuple[str, str, int]] = set()
        for site in call_sites:
            callee, line = site[0], site[1]
            scope = site[2] if len(site) > 2 else "free"
            if scope is True:
                scope = "self"
            elif scope is False:
                scope = "free"
            if not callee:
                continue
            if scope == "attr" and callee in _BUILTIN_METHODS:
                continue
            caller = self._enclosing_function(line, result.functions)
            if caller is None:
                continue
            key = (caller.node_id, callee, line)
            if key in seen:
                continue
            seen.add(key)
            meta = {"resolved": False, "line": line, "callee": callee}
            if scope == "self":
                meta["self_call"] = True
            result.calls.append(
                GraphEdge(
                    src=caller.node_id,
                    dst=f"func:?::{callee}",
                    kind=EdgeKind.CALLS,
                    meta=meta,
                )
            )

    def _compute_complexity(self, node) -> int:
        """Cyclomatic complexity: count branch nodes in AST subtree."""
        BRANCH_TYPES = {
            "if_statement", "elif_clause", "while_statement", "for_statement",
            "try_statement", "except_clause", "with_statement", "match_statement",
            "case_clause", "conditional_expression", "boolean_operator",
            "switch_case", "ternary_expression", "catch_clause", "logical_expression",
            "for_in_statement", "while_statement",
        }
        count = 1
        try:
            cursor = node.walk()
            reached_root = False
            while not reached_root:
                if cursor.node.type in BRANCH_TYPES:
                    count += 1
                if cursor.goto_first_child():
                    continue
                if cursor.goto_next_sibling():
                    continue
                while True:
                    if not cursor.goto_parent():
                        reached_root = True
                        break
                    if cursor.goto_next_sibling():
                        break
        except Exception:
            pass
        return count
