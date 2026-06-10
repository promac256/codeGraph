"""Java parser using tree-sitter."""

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
    import tree_sitter_java as tsjava

    _JAVA_LANGUAGE = Language(tsjava.language())
    _JAVA_PARSER = Parser(_JAVA_LANGUAGE)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _JAVA_LANGUAGE = _JAVA_PARSER = None  # type: ignore[assignment]

_TODO_RE = re.compile(
    r"(?://+|\*)\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE
)
_TEST_FILE_RE = re.compile(r"(^|/)tests?/|Tests?\.java$", re.IGNORECASE)

_BRANCH_NODES = frozenset({
    "if_statement",
    "for_statement",
    "enhanced_for_statement",
    "while_statement",
    "do_statement",
    "catch_clause",
    "switch_block_statement_group",
    "ternary_expression",
})

_JAVADOC_START_RE = re.compile(r"^/\*+")
_JAVADOC_END_RE = re.compile(r"\*/$")
_JAVADOC_LINE_RE = re.compile(r"^\s*\*?\s*")


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


def _get_modifiers(node) -> set[str]:
    for child in node.children:
        if child.type == "modifiers":
            return {c.type for c in child.children}
    return set()


def _is_public(node) -> bool:
    return "public" in _get_modifiers(node)


def _is_abstract(node) -> bool:
    return "abstract" in _get_modifiers(node)


def _is_static(node) -> bool:
    return "static" in _get_modifiers(node)


def _method_signature(node, source: bytes) -> str:
    """Modifiers + return type + name + formal_parameters, one line."""
    params = None
    for child in node.children:
        if child.type == "formal_parameters":
            params = child
            break
    end = params.end_byte if params else node.end_byte
    raw = source[node.start_byte:end].decode("utf-8", errors="replace").strip()
    return " ".join(raw.split())[:250]


def _javadoc(node, lines: list[str]) -> str | None:
    """Extract /** ... */ javadoc immediately above a node, skipping annotations."""
    target = node.start_point[0]  # 0-indexed line number
    i = target - 1
    # Skip blank lines and @Annotation lines
    while i >= 0 and (lines[i].strip() == "" or lines[i].strip().startswith("@")):
        i -= 1
    if i < 0 or not lines[i].strip().endswith("*/"):
        return None
    # Walk back to find the opening /**
    j = i
    while j >= 0 and "/**" not in lines[j]:
        j -= 1
    if j < 0 or "/**" not in lines[j]:
        return None
    # Extract and clean up comment text
    doc_lines: list[str] = []
    for k in range(j, i + 1):
        raw = lines[k].strip()
        raw = _JAVADOC_START_RE.sub("", raw).strip()
        raw = _JAVADOC_END_RE.sub("", raw).strip()
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


class JavaParser(LanguageParser):
    EXTENSIONS = (".java",)
    LANGUAGE_NAME = "java"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() == ".java"

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_RE.search(rel))

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang="java",
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )
        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-java not available")
            result.todos = _extract_todos(lines)
            return result

        try:
            tree = _JAVA_PARSER.parse(source)
            root = tree.root_node
            self._extract_classes(root, file_id, rel, source, lines, result)
            self._extract_interfaces(root, file_id, rel, source, lines, result)
            self._extract_enums(root, file_id, rel, source, lines, result)
            self._extract_imports(root, file_id, rel, source, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = _extract_todos(lines)
        return result

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _extract_classes(self, root, file_id, rel, source, lines, result) -> None:
        for node in root.children:
            if node.type != "class_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)
            mods = _get_modifiers(node)

            # Superclass
            bases: list[str] = []
            superclass = node.child_by_field_name("superclass")
            if superclass:
                for tid in _iter_type(superclass, "type_identifier"):
                    bases.append(_text(tid, source))
                    break  # only one superclass in Java

            # Implemented interfaces → IMPLEMENTS edges
            iface_names: list[str] = []
            super_ifaces = node.child_by_field_name("interfaces")
            if super_ifaces:
                for tid in _iter_type(super_ifaces, "type_identifier"):
                    iface_names.append(_text(tid, source))

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                bases=bases,
                docstring=_javadoc(node, lines),
                is_exported="public" in mods,
                is_abstract="abstract" in mods,
            )
            result.classes.append(cn)
            result.defines.append(
                GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )
            for iface in iface_names:
                type_id = make_type_id(rel, iface)
                result.exports.append(
                    GraphEdge(
                        src=class_id,
                        dst=type_id,
                        kind=EdgeKind.IMPLEMENTS,
                        meta={"interface": iface, "resolved": False},
                    )
                )

            # Methods and constructors inside this class
            class_body = node.child_by_field_name("body")
            if class_body:
                for member in class_body.children:
                    if member.type in ("method_declaration", "constructor_declaration"):
                        self._ingest_method(
                            member, file_id, class_id, rel, source, lines, result,
                            qualified_prefix=name,
                        )

    # ------------------------------------------------------------------
    # Interfaces → TypeNode
    # ------------------------------------------------------------------

    def _extract_interfaces(self, root, file_id, rel, source, lines, result) -> None:
        for node in root.children:
            if node.type != "interface_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            type_id = make_type_id(rel, name)
            mods = _get_modifiers(node)

            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=line_s,
                definition=_text(node, source)[:300],
                docstring=_javadoc(node, lines),
                is_exported="public" in mods or not mods,  # default access = package-visible
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

    # ------------------------------------------------------------------
    # Enums → ClassNode (algebraic data types with methods)
    # ------------------------------------------------------------------

    def _extract_enums(self, root, file_id, rel, source, lines, result) -> None:
        for node in root.children:
            if node.type != "enum_declaration":
                continue
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = _text(name_node, source)
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)
            mods = _get_modifiers(node)

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                docstring=_javadoc(node, lines),
                is_exported="public" in mods,
            )
            result.classes.append(cn)
            result.defines.append(
                GraphEdge(src=file_id, dst=class_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )

            # Methods inside enum_body_declarations
            enum_body = node.child_by_field_name("body")
            if enum_body:
                for decls_node in _iter_type(enum_body, "enum_body_declarations"):
                    for member in decls_node.children:
                        if member.type == "method_declaration":
                            self._ingest_method(
                                member, file_id, class_id, rel, source, lines, result,
                                qualified_prefix=name,
                            )

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, root, file_id, rel, source, result) -> None:
        for node in root.children:
            if node.type != "import_declaration":
                continue
            # Collect scoped_identifier text (the import path)
            path_parts: list[str] = []
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier"):
                    path_parts.append(_text(child, source))
            if not path_parts:
                continue
            module = path_parts[0]
            # Check for wildcard
            has_wildcard = any(c.type == "asterisk" for c in node.children)
            if has_wildcard:
                module = module + ".*"
            is_relative = False  # Java standard imports are never relative
            result.imports.append(
                GraphEdge(
                    src=file_id,
                    dst=f"module:{module}",
                    kind=EdgeKind.IMPORTS,
                    meta={"module": module, "is_relative": is_relative},
                )
            )

    # ------------------------------------------------------------------
    # Methods and constructors
    # ------------------------------------------------------------------

    def _ingest_method(
        self, node, file_id, class_id, rel, source, lines, result,
        qualified_prefix: str,
    ) -> None:
        is_constructor = node.type == "constructor_declaration"
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _text(name_node, source)
        qualified = f"{qualified_prefix}.{name}"
        line_s = node.start_point[0] + 1
        line_e = node.end_point[0] + 1
        func_id = make_func_id(rel, qualified)
        mods = _get_modifiers(node)

        # Body node differs for methods vs constructors
        body = None
        for child in node.children:
            if child.type in ("block", "constructor_body"):
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
            is_async=False,  # Java has no native async keyword
            complexity=_complexity(body) if body else 1,
            docstring=_javadoc(node, lines),
            is_exported="public" in mods,
            is_classmethod="static" in mods,
        )
        result.functions.append(fn)
        result.defines.append(
            GraphEdge(src=class_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
        )
        if class_id != file_id:
            result.defines.append(
                GraphEdge(src=file_id, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
            )
