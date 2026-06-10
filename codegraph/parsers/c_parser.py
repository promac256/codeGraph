"""C and C++ parser using tree-sitter-cpp."""

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
    import tree_sitter_cpp as tscpp

    _CPP_LANGUAGE = Language(tscpp.language())
    _CPP_PARSER = Parser(_CPP_LANGUAGE)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _CPP_LANGUAGE = _CPP_PARSER = None  # type: ignore[assignment]

_C_EXTENSIONS = frozenset({".c", ".h"})
_CPP_EXTENSIONS = frozenset({".cpp", ".hpp", ".cc", ".cxx", ".hh", ".hxx"})
_ALL_EXTENSIONS = _C_EXTENSIONS | _CPP_EXTENSIONS

_TODO_RE = re.compile(
    r"(?://+|\*)\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE
)
_TEST_FILE_RE = re.compile(r"(^|/)tests?/|[Tt]est[s]?\.|_test\.(c|cpp|cc|cxx|h|hpp)$")

_BRANCH_NODES = frozenset({
    "if_statement",
    "for_statement",
    "while_statement",
    "do_statement",
    "switch_statement",
    "case_statement",
    "conditional_expression",
    "catch_clause",
})

_DOC_COMMENT_END_RE = re.compile(r"\*/\s*$")
_DOC_COMMENT_START_RE = re.compile(r"/\*+")
_DOC_LINE_CLEAN_RE = re.compile(r"^\s*\*?\s*")


# ---------------------------------------------------------------------------
# Helpers
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


def _is_static_node(node) -> bool:
    for child in node.children:
        if child.type == "storage_class_specifier":
            return any(c.type == "static" for c in child.children)
    return False


def _is_public_member(node) -> bool:
    """Checks if a function_definition inside a class body follows a public: specifier."""
    return True  # C++ visibility is complex; treat everything as exported for simplicity


def _func_declarator_name(func_decl, source: bytes) -> str | None:
    """Return the function name from a function_declarator node."""
    for child in func_decl.children:
        if child.type in ("identifier", "field_identifier"):
            return _text(child, source)
        if child.type == "destructor_name":
            return None  # skip destructors
        if child.type == "qualified_identifier":
            for sub in reversed(child.children):
                if sub.type in ("identifier", "field_identifier"):
                    return _text(sub, source)
    return None


def _find_func_declarator(node) -> object | None:
    """Find the first function_declarator in node's immediate children (and 1 level deeper)."""
    for child in node.children:
        if child.type == "function_declarator":
            return child
        # Recurse one level into pointer/reference/abstract declarators
        if child.type in ("pointer_declarator", "reference_declarator",
                           "abstract_function_declarator", "abstract_declarator"):
            for sub in child.children:
                if sub.type == "function_declarator":
                    return sub
    return None


def _method_signature(node, source: bytes) -> str:
    """Everything from node start through the parameter_list, one line."""
    param = None
    for decl in _iter_type(node, "function_declarator"):
        for child in decl.children:
            if child.type == "parameter_list":
                param = child
                break
        if param:
            break
    end = param.end_byte if param else node.end_byte
    raw = source[node.start_byte:end].decode("utf-8", errors="replace").strip()
    return " ".join(raw.split())[:250]


def _doc_comment(node, lines: list[str]) -> str | None:
    """Return the /** ... */ doc comment immediately above this node."""
    target = node.start_point[0]  # 0-indexed
    i = target - 1
    while i >= 0 and lines[i].strip() == "":
        i -= 1
    if i < 0:
        return None
    line = lines[i].strip()
    # Could be a single-line doc: /** ... */
    if line.startswith("/**") and line.endswith("*/"):
        raw = re.sub(r"^/\*+", "", line).rstrip("*/").strip()
        return raw if raw else None
    # Multi-line block comment ending with */
    if not line.endswith("*/"):
        return None
    j = i
    while j >= 0 and "/**" not in lines[j]:
        j -= 1
    if j < 0 or "/**" not in lines[j]:
        return None
    doc_lines: list[str] = []
    for k in range(j, i + 1):
        raw = lines[k].strip()
        raw = _DOC_COMMENT_START_RE.sub("", raw).strip()
        raw = _DOC_COMMENT_END_RE.sub("", raw).strip()
        raw = re.sub(r"^\*\s*", "", raw).strip()
        if raw:
            doc_lines.append(raw)
    return " ".join(doc_lines) if doc_lines else None


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


class CParser(LanguageParser):
    EXTENSIONS = tuple(_ALL_EXTENSIONS)
    LANGUAGE_NAME = "c/cpp"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in _ALL_EXTENSIONS

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_RE.search(rel))
        lang = "c" if path.suffix.lower() in _C_EXTENSIONS else "cpp"

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang=lang,
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )
        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-cpp not available")
            result.todos = _extract_todos(lines)
            return result

        try:
            tree = _CPP_PARSER.parse(source)
            root = tree.root_node
            self._walk_declarations(root, file_id, rel, source, lines, result,
                                    namespace_prefix="")
            self._extract_includes(root, file_id, rel, source, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = _extract_todos(lines)
        return result

    # ------------------------------------------------------------------
    # Top-level declaration walk
    # ------------------------------------------------------------------

    def _walk_declarations(
        self, node, file_id, rel, source, lines, result, namespace_prefix: str
    ) -> None:
        for child in node.children:
            t = child.type
            if t == "function_definition":
                self._ingest_function(
                    child, file_id, None, rel, source, lines, result,
                    class_prefix="",  # free functions: no class prefix
                )
            elif t == "class_specifier":
                self._ingest_class(child, file_id, rel, source, lines, result,
                                   namespace_prefix=namespace_prefix)
            elif t == "struct_specifier":
                self._ingest_struct(child, file_id, rel, source, lines, result,
                                    namespace_prefix=namespace_prefix)
            elif t == "enum_specifier":
                self._ingest_enum(child, file_id, rel, source, lines, result)
            elif t == "namespace_definition":
                # Recurse into namespace body (declaration_list)
                ns_id_node = None
                for c in child.children:
                    if c.type == "namespace_identifier":
                        ns_id_node = c
                        break
                ns_name = _text(ns_id_node, source) if ns_id_node else ""
                prefix = f"{namespace_prefix}{ns_name}::" if ns_name else namespace_prefix
                for c in child.children:
                    if c.type == "declaration_list":
                        self._walk_declarations(c, file_id, rel, source, lines, result,
                                                namespace_prefix=prefix)
            elif t == "type_definition":
                # typedef struct Foo { ... } Foo; — extract the struct if present
                for c in child.children:
                    if c.type in ("struct_specifier", "class_specifier"):
                        self._ingest_struct(c, file_id, rel, source, lines, result,
                                            namespace_prefix=namespace_prefix, typedef=child)
                    elif c.type == "enum_specifier":
                        self._ingest_enum(c, file_id, rel, source, lines, result)

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _ingest_class(self, node, file_id, rel, source, lines, result,
                       namespace_prefix: str = "") -> None:
        name_node = None
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break
        if not name_node:
            return

        name = _text(name_node, source)
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1
        class_id = make_class_id(rel, name)

        # Base classes from base_class_clause
        bases: list[str] = []
        for child in node.children:
            if child.type == "base_class_clause":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        bases.append(_text(sub, source))

        cn = ClassNode(
            node_id=class_id,
            name=name,
            file=file_id,
            line_start=line_s,
            line_end=line_e,
            bases=bases,
            docstring=_doc_comment(node, lines),
            is_exported=True,
        )
        result.classes.append(cn)
        result.defines.append(
            GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )

        # Methods from field_declaration_list
        for child in node.children:
            if child.type == "field_declaration_list":
                self._extract_class_members(
                    child, file_id, class_id, rel, source, lines, result, class_name=name
                )

    def _extract_class_members(
        self, body, file_id, class_id, rel, source, lines, result, class_name: str
    ) -> None:
        for member in body.children:
            if member.type == "function_definition":
                self._ingest_function(
                    member, file_id, class_id, rel, source, lines, result,
                    class_prefix=class_name,
                )
            elif member.type == "field_declaration":
                # Method declaration (no body): look for function_declarator
                func_decl = _find_func_declarator(member)
                if func_decl:
                    name = _func_declarator_name(func_decl, source)
                    if not name:
                        continue
                    qualified = f"{class_name}.{name}"
                    func_id = make_func_id(rel, qualified)
                    line_s = member.start_point[0] + 1
                    is_static = _is_static_node(member)
                    fn = FunctionNode(
                        node_id=func_id,
                        name=name,
                        qualified_name=qualified,
                        file=file_id,
                        line_start=line_s,
                        line_end=member.end_point[0] + 1,
                        signature=_method_signature(member, source),
                        is_async=False,
                        complexity=1,
                        docstring=_doc_comment(member, lines),
                        is_exported=True,
                        is_classmethod=is_static,
                    )
                    result.functions.append(fn)
                    result.defines.append(
                        GraphEdge(src=class_id, dst=func_id, kind=EdgeKind.DEFINES,
                                  meta={"line": line_s})
                    )

    # ------------------------------------------------------------------
    # Structs (→ ClassNode)
    # ------------------------------------------------------------------

    def _ingest_struct(self, node, file_id, rel, source, lines, result,
                        namespace_prefix: str = "", typedef=None) -> None:
        name_node = None
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break
        if not name_node:
            return

        name = _text(name_node, source)
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1
        class_id = make_class_id(rel, name)
        doc_node = typedef if typedef is not None else node

        cn = ClassNode(
            node_id=class_id,
            name=name,
            file=file_id,
            line_start=line_s,
            line_end=line_e,
            docstring=_doc_comment(doc_node, lines),
            is_exported=True,
        )
        result.classes.append(cn)
        result.defines.append(
            GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )

        # Struct methods (rare in C, common in C++)
        for child in node.children:
            if child.type == "field_declaration_list":
                self._extract_class_members(
                    child, file_id, class_id, rel, source, lines, result, class_name=name
                )

    # ------------------------------------------------------------------
    # Enums (→ ClassNode)
    # ------------------------------------------------------------------

    def _ingest_enum(self, node, file_id, rel, source, lines, result) -> None:
        name_node = None
        for child in node.children:
            if child.type == "type_identifier":
                name_node = child
                break
        if not name_node:
            return

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
            docstring=_doc_comment(node, lines),
            is_exported=True,
        )
        result.classes.append(cn)
        result.defines.append(
            GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )

    # ------------------------------------------------------------------
    # Functions and methods
    # ------------------------------------------------------------------

    def _ingest_function(
        self, node, file_id, class_id, rel, source, lines, result, class_prefix: str = ""
    ) -> None:
        func_decl = _find_func_declarator(node)
        if not func_decl:
            return
        name = _func_declarator_name(func_decl, source)
        if not name:
            return  # destructor or anonymous

        qualified = f"{class_prefix}.{name}" if class_prefix else name
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1
        func_id = make_func_id(rel, qualified)
        is_static = _is_static_node(node)

        body = None
        for child in node.children:
            if child.type in ("compound_statement", "try_statement"):
                body = child
                break

        fn = FunctionNode(
            node_id=func_id,
            name=name,
            qualified_name=qualified,
            file=file_id,
            line_start=line_s,
            line_end=line_e,
            signature=_method_signature(node, source),
            is_async=False,
            complexity=_complexity(body) if body else 1,
            docstring=_doc_comment(node, lines),
            is_exported=not is_static,
            is_classmethod=is_static and class_id is not None,
        )
        result.functions.append(fn)

        owner = class_id or file_id
        result.defines.append(
            GraphEdge(src=owner, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )
        if class_id and class_id != file_id:
            result.defines.append(
                GraphEdge(src=file_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Includes → IMPORTS edges
    # ------------------------------------------------------------------

    def _extract_includes(self, root, file_id, rel, source, result) -> None:
        for node in _iter_type(root, "preproc_include"):
            module = None
            is_relative = False
            for child in node.children:
                if child.type == "system_lib_string":
                    # <stdio.h> → strip < >
                    raw = _text(child, source).strip("<> ")
                    module = raw
                    is_relative = False
                elif child.type == "string_literal":
                    # "mylib.h" → strip quotes
                    inner = child.child_by_field_name("value")
                    if inner:
                        module = _text(inner, source)
                    else:
                        raw = _text(child, source).strip('"\'')
                        module = raw
                    is_relative = True
                elif child.type == "string_content":
                    module = _text(child, source)
                    is_relative = True
            if module:
                result.imports.append(
                    GraphEdge(
                        src=file_id,
                        dst=f"module:{module}",
                        kind=EdgeKind.IMPORTS,
                        meta={"module": module, "is_relative": is_relative},
                    )
                )
