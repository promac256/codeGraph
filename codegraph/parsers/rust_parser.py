"""Rust parser using tree-sitter."""

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
    import tree_sitter_rust as tsrust

    _RUST_LANGUAGE = Language(tsrust.language())
    _RUST_PARSER = Parser(_RUST_LANGUAGE)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _RUST_LANGUAGE = _RUST_PARSER = None  # type: ignore[assignment]

_TODO_RE = re.compile(
    r"//+\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE
)
_TEST_FILE_RE = re.compile(r"(^|/)tests?/|_test\.rs$", re.IGNORECASE)

# Branch nodes that each add +1 to cyclomatic complexity.
_BRANCH_NODES = frozenset({
    "if_expression",
    "if_let_expression",
    "while_expression",
    "while_let_expression",
    "for_expression",
    "loop_expression",
    "match_arm",       # each match arm = +1
})


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _iter_type(node, node_type: str):
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _iter_type(child, node_type)


def _count_branches(node) -> int:
    total = 1 if node.type in _BRANCH_NODES else 0
    for child in node.children:
        total += _count_branches(child)
    return total


def _complexity(body_node) -> int:
    return max(1, 1 + _count_branches(body_node))


def _is_pub(node) -> bool:
    return any(c.type == "visibility_modifier" for c in node.children)


def _is_async(node) -> bool:
    for child in node.children:
        if child.type == "function_modifiers":
            return any(c.type == "async" for c in child.children)
    return False


def _func_signature(node, source: bytes) -> str:
    """Everything from 'fn' to the opening brace, collapsed to one line."""
    # Find body block to know where signature ends
    body = None
    for child in node.children:
        if child.type == "block":
            body = child
            break
    end = body.start_byte if body else node.end_byte
    raw = source[node.start_byte:end].decode("utf-8", errors="replace").strip()
    return " ".join(raw.split())[:200]


def _doc_comment(node, source: bytes) -> str | None:
    """Extract /// doc comment lines immediately above a node, skipping attributes."""
    lines = source.decode("utf-8", errors="replace").splitlines()
    target_line = node.start_point[0]
    doc_lines: list[str] = []
    for i in range(target_line - 1, max(-1, target_line - 20), -1):
        s = lines[i].strip()
        if s.startswith("///"):
            doc_lines.insert(0, s[3:].strip())
        elif s.startswith("#[") or s.startswith("#!["):
            continue  # skip attributes between doc comment and item
        elif s == "":
            if not doc_lines:
                continue  # allow blank line before first comment line
            break
        else:
            break
    return " ".join(doc_lines) if doc_lines else None


def _impl_parts(node, source: bytes) -> tuple[str | None, str]:
    """
    Parse an impl_item and return (trait_name_or_None, implementing_type_name).

    Handles:
      impl Dog { ... }              → (None, "Dog")
      impl Speaker for Dog { ... }  → ("Speaker", "Dog")
      impl<T> Container<T> { ... }  → (None, "Container")
    """
    has_for = any(c.type == "for" for c in node.children)
    type_ids = [
        c for c in node.children
        if c.type in ("type_identifier", "generic_type", "scoped_type_identifier")
    ]

    def bare_name(n) -> str:
        if n.type == "type_identifier":
            return _text(n, source)
        # generic_type or scoped_type_identifier — grab first type_identifier child
        for inner in _iter_type(n, "type_identifier"):
            return _text(inner, source)
        return _text(n, source)

    if has_for and len(type_ids) >= 2:
        return bare_name(type_ids[0]), bare_name(type_ids[1])
    if type_ids:
        return None, bare_name(type_ids[0])
    return None, ""


def _extract_todos(lines: list[str]) -> list[dict]:
    todos = []
    for i, line in enumerate(lines, 1):
        m = _TODO_RE.search(line)
        if m:
            todos.append({"line": i, "kind": m.group(1).upper(), "text": m.group(2).strip()})
    return todos


# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------

class RustParser(LanguageParser):
    EXTENSIONS = (".rs",)
    LANGUAGE_NAME = "rust"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".rs"

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_RE.search(rel))

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang="rust",
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )
        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-rust not available")
            result.todos = _extract_todos(lines)
            return result

        try:
            tree = _RUST_PARSER.parse(source)
            root = tree.root_node
            self._extract_structs(root, file_id, rel, source, result)
            self._extract_enums(root, file_id, rel, source, result)
            self._extract_traits(root, file_id, rel, source, result)
            self._extract_type_aliases(root, file_id, rel, source, result)
            self._extract_free_functions(root, file_id, rel, source, result)
            self._extract_impl_blocks(root, file_id, rel, source, result)
            self._extract_imports(root, file_id, rel, source, result)
            self._extract_calls(root, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = _extract_todos(lines)
        return result

    # ------------------------------------------------------------------
    # Structs → ClassNode
    # ------------------------------------------------------------------

    def _extract_structs(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "struct_item"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                docstring=_doc_comment(node, source),
                is_exported=_is_pub(node),
            )
            result.classes.append(cn)
            result.defines.append(
                GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Enums → ClassNode  (Rust enums are algebraic data types)
    # ------------------------------------------------------------------

    def _extract_enums(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "enum_item"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                docstring=_doc_comment(node, source),
                is_exported=_is_pub(node),
            )
            result.classes.append(cn)
            result.defines.append(
                GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Traits → TypeNode  (analogous to Go interfaces)
    # ------------------------------------------------------------------

    def _extract_traits(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "trait_item"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            type_id = make_type_id(rel, name)

            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=line_s,
                definition=_text(node, source)[:300],
                docstring=_doc_comment(node, source),
                is_exported=_is_pub(node),
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Type aliases → TypeNode
    # ------------------------------------------------------------------

    def _extract_type_aliases(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "type_item"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            type_id = make_type_id(rel, name)

            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=line_s,
                definition=_text(node, source)[:200],
                is_exported=_is_pub(node),
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Free functions (not in impl blocks) → FunctionNode
    # ------------------------------------------------------------------

    def _extract_free_functions(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in root.children:
            if node.type == "function_item":
                self._ingest_function(node, file_id, file_id, rel, source, result, qualified_prefix=None)

    # ------------------------------------------------------------------
    # impl blocks → FunctionNode for each method
    # ------------------------------------------------------------------

    def _extract_impl_blocks(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "impl_item"):
            trait_name, type_name = _impl_parts(node, source)
            if not type_name:
                continue

            class_id = make_class_id(rel, type_name)

            # If this is a trait impl, add an IMPLEMENTS edge
            if trait_name:
                trait_id = make_type_id(rel, trait_name)
                result.exports.append(
                    GraphEdge(
                        src=class_id,
                        dst=trait_id,
                        kind=EdgeKind.IMPLEMENTS,
                        meta={"trait": trait_name, "resolved": False},
                    )
                )

            # Extract methods from the declaration_list
            decl_list = node.child_by_field_name("body")
            if decl_list is None:
                # Try finding the declaration_list directly
                for child in node.children:
                    if child.type == "declaration_list":
                        decl_list = child
                        break
            if decl_list is None:
                continue

            for fn_node in decl_list.children:
                if fn_node.type == "function_item":
                    self._ingest_function(
                        fn_node, file_id, class_id, rel, source, result,
                        qualified_prefix=type_name,
                    )

    def _ingest_function(
        self, node, file_id: str, src_id: str, rel: str,
        source: bytes, result: ParseResult, qualified_prefix: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _text(name_node, source)
        qualified = f"{qualified_prefix}.{name}" if qualified_prefix else name
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
            signature=_func_signature(node, source),
            is_async=_is_async(node),
            complexity=compl,
            docstring=_doc_comment(node, source),
            is_exported=_is_pub(node),
        )
        result.functions.append(fn)
        result.defines.append(
            GraphEdge(src=src_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )
        # Also attach to file so cross-file symbol lookup always works
        if src_id != file_id:
            result.defines.append(
                GraphEdge(src=file_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Use declarations → IMPORTS edges
    # ------------------------------------------------------------------

    def _extract_imports(self, root, file_id, rel, source, result: ParseResult) -> None:
        for node in _iter_type(root, "use_declaration"):
            # The import path is the first non-keyword, non-visibility child
            for child in node.children:
                if child.type in ("use", ";", "visibility_modifier"):
                    continue
                path = _text(child, source).strip()
                if path:
                    is_relative = path.startswith("crate::") or path.startswith("super::") or path.startswith("self::")
                    result.imports.append(
                        GraphEdge(
                            src=file_id,
                            dst=f"module:{path}",
                            kind=EdgeKind.IMPORTS,
                            meta={"module": path, "is_relative": is_relative},
                        )
                    )
                break

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _extract_calls(self, root, result: ParseResult) -> None:
        if not result.functions:
            return
        sites = []
        # Free / associated calls: foo(), Type::new(), x.field() etc.
        for call in _iter_type(root, "call_expression"):
            fn = call.child_by_field_name("function")
            name = self._callee_name(fn) if fn is not None else None
            if name:
                sites.append((name, call.start_point[0] + 1))
        # Method calls: receiver.method(args)
        for call in _iter_type(root, "method_call_expression"):
            method = call.child_by_field_name("method")
            if method is not None:
                sites.append(
                    (method.text.decode("utf-8", errors="replace"), call.start_point[0] + 1)
                )
        self._emit_call_edges(sites, result)

    def _callee_name(self, fn) -> str | None:
        t = fn.type
        if t == "identifier":
            return fn.text.decode("utf-8", errors="replace")
        if t == "field_expression":
            field = fn.child_by_field_name("field")
            if field is not None:
                return field.text.decode("utf-8", errors="replace")
        if t == "scoped_identifier":
            name = fn.child_by_field_name("name")
            if name is not None:
                return name.text.decode("utf-8", errors="replace")
        if t == "generic_function":
            inner = fn.child_by_field_name("function")
            if inner is not None:
                return self._callee_name(inner)
        return None
