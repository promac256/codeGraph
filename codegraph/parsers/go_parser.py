"""Go parser using tree-sitter."""

from __future__ import annotations

import re
from pathlib import Path

from codegraph.models import (
    ClassNode,
    EdgeKind,
    FileNode,
    FunctionNode,
    GraphEdge,
    TypeNode,
)
from codegraph.parsers.base import LanguageParser, ParseResult
from codegraph.utils.hashing import (
    make_class_id,
    make_file_id,
    make_func_id,
    make_type_id,
    sha256_bytes,
)

try:
    from tree_sitter import Language, Parser
    import tree_sitter_go as tsgo

    _GO_LANGUAGE = Language(tsgo.language())
    _GO_PARSER = Parser(_GO_LANGUAGE)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _GO_LANGUAGE = _GO_PARSER = None  # type: ignore[assignment]

_TODO_RE = re.compile(
    r"//\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE
)
_TEST_FILE_RE = re.compile(r"_test\.go$")

# Branch nodes that each add +1 to cyclomatic complexity.
# Names confirmed from tree-sitter-go AST dump.
_BRANCH_NODES = frozenset({
    "if_statement",
    "for_statement",
    "expression_case",    # case clause in expression switch (not default)
    "type_case",          # case clause in type switch
    "communication_case", # case clause in select statement
})


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _iter_type(node, node_type: str):
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _iter_type(child, node_type)


def _count_branches(node) -> int:
    """Recursively count branch nodes (each adds +1 to cyclomatic complexity)."""
    total = 1 if node.type in _BRANCH_NODES else 0
    for child in node.children:
        total += _count_branches(child)
    return total


def _complexity(body_node) -> int:
    """Cyclomatic complexity of a function body: 1 (base) + branch count."""
    return max(1, 1 + _count_branches(body_node))


def _receiver_type_name(receiver_list, source: bytes) -> str:
    """Extract the bare type name from a method receiver parameter_list."""
    for child in receiver_list.children:
        if child.type == "parameter_declaration":
            for part in child.children:
                if part.type == "pointer_type":
                    for inner in part.children:
                        if inner.type == "type_identifier":
                            return _text(inner, source)
                elif part.type == "type_identifier":
                    return _text(part, source)
    return ""


def _func_signature(node, name: str, source: bytes) -> str:
    """Build a compact signature string for a function or method."""
    params = node.child_by_field_name("parameters")
    result = node.child_by_field_name("result")
    params_text = _text(params, source) if params else "()"
    result_text = " " + _text(result, source) if result else ""
    return f"{name}{params_text}{result_text}"[:200]


class GoParser(LanguageParser):
    EXTENSIONS = (".go",)
    LANGUAGE_NAME = "go"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".go"

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_RE.search(rel))

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang="go",
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )
        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-go not available")
            result.todos = _extract_todos(lines)
            return result

        try:
            tree = _GO_PARSER.parse(source)
            root = tree.root_node
            self._extract_structs(root, file_id, rel, source, result)
            self._extract_interfaces(root, file_id, rel, source, result)
            self._extract_type_aliases(root, file_id, rel, source, result)
            self._extract_functions(root, file_id, rel, source, result)
            self._extract_methods(root, file_id, rel, source, result)
            self._extract_imports(root, file_id, rel, source, result)
            self._extract_calls(root, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = _extract_todos(lines)
        return result

    # ------------------------------------------------------------------
    # Structs  →  ClassNode
    # ------------------------------------------------------------------

    def _extract_structs(self, root, file_id, rel, source, result: ParseResult) -> None:
        for tdecl in _iter_type(root, "type_declaration"):
            for tspec in _iter_type(tdecl, "type_spec"):
                name_node = tspec.child_by_field_name("name")
                type_node = tspec.child_by_field_name("type")
                if not name_node or not type_node:
                    continue
                if type_node.type != "struct_type":
                    continue

                name = _text(name_node, source)
                line_s = tspec.start_point[0] + 1
                line_e = tspec.end_point[0] + 1
                class_id = make_class_id(rel, name)

                # Embedded types → bases (Go composition)
                bases: list[str] = []
                for field_list in _iter_type(type_node, "field_declaration_list"):
                    for fdecl in field_list.children:
                        if fdecl.type != "field_declaration":
                            continue
                        # An embedded field has a type but no separate field name
                        children = [c for c in fdecl.named_children if c.type not in ("comment",)]
                        if len(children) == 1:
                            embedded = children[0]
                            base_name = ""
                            if embedded.type == "type_identifier":
                                base_name = _text(embedded, source)
                            elif embedded.type == "pointer_type":
                                for inner in embedded.children:
                                    if inner.type == "type_identifier":
                                        base_name = _text(inner, source)
                                        break
                            if base_name:
                                bases.append(base_name)

                is_exported = name[0].isupper()
                cn = ClassNode(
                    node_id=class_id,
                    name=name,
                    file=file_id,
                    line_start=line_s,
                    line_end=line_e,
                    bases=bases,
                    docstring=_preceding_comment(tspec, source),
                    is_exported=is_exported,
                )
                result.classes.append(cn)
                result.defines.append(
                    GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
                )
                for base in bases:
                    result.inherits.append(
                        GraphEdge(
                            src=class_id,
                            dst=f"class:?::{base}",
                            kind=EdgeKind.INHERITS,
                            meta={"resolved": False},
                        )
                    )

    # ------------------------------------------------------------------
    # Interfaces  →  TypeNode
    # ------------------------------------------------------------------

    def _extract_interfaces(self, root, file_id, rel, source, result: ParseResult) -> None:
        for tdecl in _iter_type(root, "type_declaration"):
            for tspec in _iter_type(tdecl, "type_spec"):
                name_node = tspec.child_by_field_name("name")
                type_node = tspec.child_by_field_name("type")
                if not name_node or not type_node:
                    continue
                if type_node.type != "interface_type":
                    continue

                name = _text(name_node, source)
                line_s = tspec.start_point[0] + 1
                type_id = make_type_id(rel, name)
                tn = TypeNode(
                    node_id=type_id,
                    name=name,
                    file=file_id,
                    line_start=line_s,
                    definition=_text(tspec, source)[:300],
                    docstring=_preceding_comment(tspec, source),
                    is_exported=name[0].isupper(),
                )
                result.types.append(tn)
                result.defines.append(
                    GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
                )

    # ------------------------------------------------------------------
    # Type aliases / definitions  →  TypeNode
    # ------------------------------------------------------------------

    def _extract_type_aliases(self, root, file_id, rel, source, result: ParseResult) -> None:
        for tdecl in _iter_type(root, "type_declaration"):
            for tspec in _iter_type(tdecl, "type_spec"):
                name_node = tspec.child_by_field_name("name")
                type_node = tspec.child_by_field_name("type")
                if not name_node or not type_node:
                    continue
                if type_node.type in ("struct_type", "interface_type"):
                    continue  # handled above

                name = _text(name_node, source)
                line_s = tspec.start_point[0] + 1
                type_id = make_type_id(rel, name)
                tn = TypeNode(
                    node_id=type_id,
                    name=name,
                    file=file_id,
                    line_start=line_s,
                    definition=_text(tspec, source)[:200],
                    is_exported=name[0].isupper(),
                )
                result.types.append(tn)
                result.defines.append(
                    GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
                )

    # ------------------------------------------------------------------
    # Top-level functions  →  FunctionNode
    # ------------------------------------------------------------------

    def _extract_functions(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in root.children:
            if node.type != "function_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            func_id = make_func_id(rel, name)

            body = node.child_by_field_name("body")
            compl = _complexity(body) if body else 1

            fn = FunctionNode(
                node_id=func_id,
                name=name,
                qualified_name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                signature=_func_signature(node, name, source),
                is_async=False,  # Go has goroutines, not async/await
                complexity=compl,
                docstring=_preceding_comment(node, source),
                is_exported=name[0].isupper(),
            )
            result.functions.append(fn)
            result.defines.append(
                GraphEdge(src=file_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Methods  →  FunctionNode  (qualified as ReceiverType.Name)
    # ------------------------------------------------------------------

    def _extract_methods(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in root.children:
            if node.type != "method_declaration":
                continue
            receiver = node.child_by_field_name("receiver")
            name_node = node.child_by_field_name("name")
            if not receiver or not name_node:
                continue

            recv_type = _receiver_type_name(receiver, source)
            name = _text(name_node, source)
            qualified = f"{recv_type}.{name}" if recv_type else name
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            func_id = make_func_id(rel, qualified)

            body = node.child_by_field_name("body")
            compl = _complexity(body) if body else 1

            fn = FunctionNode(
                node_id=func_id,
                name=name,
                qualified_name=qualified,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                signature=_func_signature(node, qualified, source),
                is_async=False,
                complexity=compl,
                docstring=_preceding_comment(node, source),
                is_exported=name[0].isupper(),
            )
            result.functions.append(fn)

            # Prefer edges from the receiver's class node if it exists in this file
            class_id = make_class_id(rel, recv_type) if recv_type else file_id
            result.defines.append(
                GraphEdge(src=class_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )
            # Also attach to file so cross-file queries work
            result.defines.append(
                GraphEdge(src=file_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "import_declaration"):
            for spec in _iter_type(node, "import_spec"):
                path_node = spec.child_by_field_name("path")
                if not path_node:
                    continue
                raw = _text(path_node, source).strip('"')
                dst = f"module:{raw}"
                result.imports.append(
                    GraphEdge(
                        src=file_id,
                        dst=dst,
                        kind=EdgeKind.IMPORTS,
                        meta={"module": raw},
                    )
                )

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _extract_calls(self, root, result: ParseResult) -> None:
        if not result.functions:
            return
        sites = []
        for call in _iter_type(root, "call_expression"):
            fn = call.child_by_field_name("function")
            if fn is None:
                continue
            name = self._callee_name(fn)
            if name:
                sites.append((name, call.start_point[0] + 1))
        self._emit_call_edges(sites, result)

    def _callee_name(self, fn) -> str | None:
        # foo() -> foo ; pkg.Foo() / x.Method() -> Foo / Method
        if fn.type == "identifier":
            return fn.text.decode("utf-8", errors="replace")
        if fn.type == "selector_expression":
            field = fn.child_by_field_name("field")
            if field is not None:
                return field.text.decode("utf-8", errors="replace")
        return None


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _preceding_comment(node, source: bytes) -> str | None:
    """Return the last // comment block immediately above a node, if any."""
    lines = source.decode("utf-8", errors="replace").splitlines()
    target_line = node.start_point[0]  # 0-indexed
    comment_lines: list[str] = []
    for i in range(target_line - 1, max(-1, target_line - 10), -1):
        stripped = lines[i].strip()
        if stripped.startswith("//"):
            comment_lines.insert(0, stripped[2:].strip())
        else:
            break
    return " ".join(comment_lines) if comment_lines else None


def _extract_todos(lines: list[str]) -> list[dict]:
    todos = []
    for i, line in enumerate(lines, 1):
        m = _TODO_RE.search(line)
        if m:
            todos.append({"line": i, "kind": m.group(1).upper(), "text": m.group(2).strip()})
    return todos
