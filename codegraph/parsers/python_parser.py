"""Python language parser using tree-sitter."""

from __future__ import annotations

import re
from pathlib import Path

from codegraph.models import (
    ClassNode,
    EdgeKind,
    FileNode,
    FunctionNode,
    GraphEdge,
    NodeKind,
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
    import tree_sitter_python as tspython

    _PY_LANGUAGE = Language(tspython.language())
    _PARSER = Parser(_PY_LANGUAGE)
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _PY_LANGUAGE = None
    _PARSER = None

_TODO_RE = re.compile(r"#\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE)
_ALL_RE = re.compile(r"^__all__\s*=\s*\[([^\]]+)\]", re.MULTILINE | re.DOTALL)
_DECORATOR_RE = re.compile(r"@([\w.]+)")

_TEST_FILE_PATTERNS = re.compile(r"(^|/)tests?/|_test\.py$|test_.*\.py$", re.IGNORECASE)


class PythonParser(LanguageParser):
    EXTENSIONS = (".py", ".pyi")
    LANGUAGE_NAME = "python"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.EXTENSIONS

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_PATTERNS.search(rel))

        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang="python",
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )

        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-python not available; using fallback")
            result.todos = self._extract_todos(lines)
            return result

        try:
            tree = _PARSER.parse(source)
            self._extract_classes(tree.root_node, file_id, rel, source_text, result)
            self._extract_functions(tree.root_node, file_id, rel, source_text, result)
            self._extract_imports(tree.root_node, file_id, rel, source_text, result)
            self._extract_type_aliases(tree.root_node, file_id, rel, source_text, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = self._extract_todos(lines)
        result.file_node.is_test = is_test
        return result

    # ------------------------------------------------------------------
    # Class extraction
    # ------------------------------------------------------------------

    def _extract_classes(
        self,
        root,
        file_id: str,
        rel: str,
        source_text: str,
        result: ParseResult,
    ) -> None:
        for node in self._iter_type(root, "class_definition"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)

            bases = self._extract_bases(node, source_text)
            body = node.child_by_field_name("body")
            docstring = self._extract_docstring(body)
            decorators = self._get_decorators(node, source_text)
            is_dataclass = "dataclass" in decorators
            is_abstract = "ABC" in bases or "ABCMeta" in bases or "abc.ABC" in bases

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                bases=bases,
                docstring=docstring,
                is_abstract=is_abstract,
                is_dataclass=is_dataclass,
            )
            result.classes.append(cn)
            result.defines.append(
                GraphEdge(
                    src=file_id,
                    dst=class_id,
                    kind=EdgeKind.DEFINES,
                    meta={"line": line_s},
                )
            )
            for base in bases:
                # dst is a placeholder; resolved in builder second pass
                result.inherits.append(
                    GraphEdge(
                        src=class_id,
                        dst=f"class:?::{base}",
                        kind=EdgeKind.INHERITS,
                        meta={"resolved": False},
                    )
                )

    def _extract_bases(self, class_node, source_text: str) -> list[str]:
        bases: list[str] = []
        args = class_node.child_by_field_name("superclasses")
        if not args:
            return bases
        for child in args.named_children:
            text = child.text.decode("utf-8", errors="replace").strip()
            if text:
                bases.append(text)
        return bases

    # ------------------------------------------------------------------
    # Function extraction
    # ------------------------------------------------------------------

    def _extract_functions(
        self,
        root,
        file_id: str,
        rel: str,
        source_text: str,
        result: ParseResult,
    ) -> None:
        # Build class span lookup for determining method context
        class_spans: dict[str, tuple[int, int]] = {}
        for cn in result.classes:
            class_spans[cn.name] = (cn.line_start, cn.line_end)

        for node in self._iter_type(root, "function_definition"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1

            parent_class = self._find_enclosing_class(line_s, class_spans)
            qualified = f"{parent_class}.{name}" if parent_class else name
            func_id = make_func_id(rel, qualified)

            is_async = any(
                c.type == "async" for c in node.children if hasattr(c, "type")
            )
            decorators = self._get_decorators(node, source_text)
            body = node.child_by_field_name("body")
            docstring = self._extract_docstring(body)
            sig = self._build_signature(name, node, source_text)
            complexity = self._compute_complexity(node)

            fn = FunctionNode(
                node_id=func_id,
                name=name,
                qualified_name=qualified,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                signature=sig,
                docstring=docstring,
                is_async=is_async,
                complexity=complexity,
                is_property="property" in decorators,
                is_classmethod="classmethod" in decorators,
                is_staticmethod="staticmethod" in decorators,
            )
            result.functions.append(fn)

            src = make_class_id(rel, parent_class) if parent_class else file_id
            result.defines.append(
                GraphEdge(
                    src=src,
                    dst=func_id,
                    kind=EdgeKind.DEFINES,
                    meta={"line": line_s},
                )
            )

    def _find_enclosing_class(
        self, line: int, class_spans: dict[str, tuple[int, int]]
    ) -> str | None:
        for class_name, (start, end) in class_spans.items():
            if start < line <= end:
                return class_name
        return None

    def _build_signature(self, name: str, func_node, source_text: str) -> str:
        params_node = func_node.child_by_field_name("parameters")
        return_node = func_node.child_by_field_name("return_type")
        params = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
        ret = ""
        if return_node:
            ret = " -> " + return_node.text.decode("utf-8", errors="replace")
        return f"{name}{params}{ret}"[:200]

    def _get_decorators(self, node, source_text: str) -> list[str]:
        decorators: list[str] = []
        for child in node.children:
            if child.type == "decorator":
                text = child.text.decode("utf-8", errors="replace").lstrip("@").strip()
                # Extract just the name part (e.g. "property" from "@property")
                decorators.append(text.split("(")[0].split(".")[-1])
        return decorators

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_imports(
        self,
        root,
        file_id: str,
        rel: str,
        source_text: str,
        result: ParseResult,
    ) -> None:
        for node in self._iter_type(root, "import_statement"):
            for name_node in self._iter_type(node, "dotted_name"):
                module_name = name_node.text.decode("utf-8", errors="replace")
                dst = f"module:{module_name}"
                result.imports.append(
                    GraphEdge(
                        src=file_id,
                        dst=dst,
                        kind=EdgeKind.IMPORTS,
                        meta={"is_relative": False, "is_star": False},
                    )
                )
                break  # first dotted_name is the module

        for node in self._iter_type(root, "import_from_statement"):
            module_node = node.child_by_field_name("module_name")
            module_name = ""
            if module_node:
                module_name = module_node.text.decode("utf-8", errors="replace")
            is_relative = any(c.type == "relative_import" for c in node.children)
            is_star = any(c.type == "wildcard_import" for c in node.children)
            dst = f"module:{module_name}" if module_name else f"file:{rel}"
            result.imports.append(
                GraphEdge(
                    src=file_id,
                    dst=dst,
                    kind=EdgeKind.IMPORTS,
                    meta={
                        "is_relative": is_relative,
                        "is_star": is_star,
                        "module": module_name,
                    },
                )
            )

    # ------------------------------------------------------------------
    # Type alias extraction
    # ------------------------------------------------------------------

    def _extract_type_aliases(
        self,
        root,
        file_id: str,
        rel: str,
        source_text: str,
        result: ParseResult,
    ) -> None:
        for node in self._iter_type(root, "type_alias_statement"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            type_id = make_type_id(rel, name)
            definition = node.text.decode("utf-8", errors="replace")[:300]
            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=node.start_point[0] + 1,
                definition=definition,
                is_exported=not name.startswith("_"),
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(
                    src=file_id,
                    dst=type_id,
                    kind=EdgeKind.DEFINES,
                    meta={"line": tn.line_start},
                )
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_docstring(self, body_node) -> str | None:
        if not body_node:
            return None
        for child in body_node.named_children:
            if child.type == "expression_statement":
                expr = child.named_children[0] if child.named_children else None
                if expr and expr.type in ("string", "concatenated_string"):
                    raw = expr.text.decode("utf-8", errors="replace")
                    cleaned = raw.strip('"""\'\'\'"\' \n\t')
                    return cleaned[:512] if cleaned else None
        return None

    def _extract_todos(self, lines: list[str]) -> list[dict]:
        todos = []
        for i, line in enumerate(lines, 1):
            m = _TODO_RE.search(line)
            if m:
                todos.append(
                    {"line": i, "kind": m.group(1).upper(), "text": m.group(2).strip()}
                )
        return todos

    def _iter_type(self, node, node_type: str):
        """Yield all descendant nodes of a given type."""
        if node.type == node_type:
            yield node
        for child in node.children:
            yield from self._iter_type(child, node_type)
