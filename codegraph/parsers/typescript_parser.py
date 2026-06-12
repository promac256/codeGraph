"""TypeScript/JavaScript parser using tree-sitter."""

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
    import tree_sitter_typescript as tsts

    _TS_LANGUAGE = Language(tsts.language_typescript())
    _TS_PARSER = Parser(_TS_LANGUAGE)

    import tree_sitter_javascript as tsjs

    _JS_LANGUAGE = Language(tsjs.language())
    _JS_PARSER = Parser(_JS_LANGUAGE)

    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    _TS_LANGUAGE = _TS_PARSER = _JS_LANGUAGE = _JS_PARSER = None

_TODO_RE = re.compile(r"[/*#]+\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)", re.IGNORECASE)
_TEST_FILE_PATTERNS = re.compile(
    r"(^|/)(tests?|__tests?__)/|\.test\.(ts|tsx|js|jsx)$|\.spec\.(ts|tsx|js|jsx)$",
    re.IGNORECASE,
)


class TypeScriptParser(LanguageParser):
    EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    LANGUAGE_NAME = "typescript"

    def can_parse(self, path: Path) -> bool:
        return path.suffix.lower() in self.EXTENSIONS

    def parse(self, path: Path, source: bytes, repo_root: Path) -> ParseResult:
        rel = path.relative_to(repo_root).as_posix()
        file_id = make_file_id(path, repo_root)
        source_text = source.decode("utf-8", errors="replace")
        lines = source_text.splitlines()
        is_test = bool(_TEST_FILE_PATTERNS.search(rel))
        is_ts = path.suffix.lower() in (".ts", ".tsx")

        lang_name = "typescript" if is_ts else "javascript"
        file_node = FileNode(
            node_id=file_id,
            path=rel,
            lang=lang_name,
            size_bytes=len(source),
            sha256=sha256_bytes(source),
            line_count=len(lines),
            is_test=is_test,
        )

        result = ParseResult(file_node=file_node)

        if not _AVAILABLE:
            result.errors.append("tree-sitter-typescript not available")
            result.todos = self._extract_todos(lines)
            return result

        try:
            parser = _TS_PARSER if is_ts else _JS_PARSER
            tree = parser.parse(source)
            self._extract_classes(tree.root_node, file_id, rel, result)
            self._extract_functions(tree.root_node, file_id, rel, result)
            self._extract_imports(tree.root_node, file_id, rel, result)
            if is_ts:
                self._extract_interfaces(tree.root_node, file_id, rel, result)
                self._extract_type_aliases(tree.root_node, file_id, rel, result)
            self._extract_calls(tree.root_node, result)
        except Exception as e:
            result.errors.append(f"tree-sitter parse error: {e}")

        result.todos = self._extract_todos(lines)
        return result

    def _extract_classes(self, root, file_id, rel, result: ParseResult) -> None:
        for node in self._iter_type(root, "class_declaration"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            line_s = node.start_point[0] + 1
            line_e = node.end_point[0] + 1
            class_id = make_class_id(rel, name)

            bases: list[str] = []
            heritage = node.child_by_field_name("class_heritage")
            if heritage:
                for child in heritage.named_children:
                    if child.type in ("extends_clause", "implements_clause"):
                        for t in child.named_children:
                            text = t.text.decode("utf-8", errors="replace").strip()
                            if text and text not in ("extends", "implements"):
                                bases.append(text)

            cn = ClassNode(
                node_id=class_id,
                name=name,
                file=file_id,
                line_start=line_s,
                line_end=line_e,
                bases=bases,
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

    def _extract_functions(self, root, file_id, rel, result: ParseResult) -> None:
        class_spans = {cn.name: (cn.line_start, cn.line_end) for cn in result.classes}

        func_node_types = (
            "function_declaration",
            "method_definition",
            "arrow_function",
            "function_expression",
        )
        for node_type in func_node_types:
            for node in self._iter_type(root, node_type):
                name_node = node.child_by_field_name("name")
                if not name_node:
                    continue
                name = name_node.text.decode("utf-8", errors="replace")
                if not name or name in ("get", "set"):
                    continue
                line_s = node.start_point[0] + 1
                line_e = node.end_point[0] + 1

                parent_class = self._find_enclosing_class(line_s, class_spans)
                qualified = f"{parent_class}.{name}" if parent_class else name
                func_id = make_func_id(rel, qualified)

                is_async = any(c.type == "async" for c in node.children)
                params_node = node.child_by_field_name("parameters")
                params_text = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
                sig = f"{name}{params_text}"[:200]

                fn = FunctionNode(
                    node_id=func_id,
                    name=name,
                    qualified_name=qualified,
                    file=file_id,
                    line_start=line_s,
                    line_end=line_e,
                    signature=sig,
                    is_async=is_async,
                    complexity=self._compute_complexity(node),
                )
                result.functions.append(fn)
                src = make_class_id(rel, parent_class) if parent_class else file_id
                result.defines.append(
                    GraphEdge(src=src, dst=func_id, kind=EdgeKind.DEFINES, meta={"line": line_s})
                )

    def _extract_imports(self, root, file_id, rel, result: ParseResult) -> None:
        for node in self._iter_type(root, "import_statement"):
            source_node = node.child_by_field_name("source")
            if not source_node:
                continue
            module_path = source_node.text.decode("utf-8", errors="replace").strip("'\"")
            is_relative = module_path.startswith(".")
            dst = f"file:{module_path}" if is_relative else f"module:{module_path}"
            result.imports.append(
                GraphEdge(
                    src=file_id,
                    dst=dst,
                    kind=EdgeKind.IMPORTS,
                    meta={"is_relative": is_relative, "module": module_path},
                )
            )

    def _extract_interfaces(self, root, file_id, rel, result: ParseResult) -> None:
        for node in self._iter_type(root, "interface_declaration"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            type_id = make_type_id(rel, name)
            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=node.start_point[0] + 1,
                definition=node.text.decode("utf-8", errors="replace")[:300],
                is_exported=True,
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": tn.line_start})
            )

    def _extract_type_aliases(self, root, file_id, rel, result: ParseResult) -> None:
        for node in self._iter_type(root, "type_alias_declaration"):
            name_node = node.child_by_field_name("name")
            if not name_node:
                continue
            name = name_node.text.decode("utf-8", errors="replace")
            type_id = make_type_id(rel, name)
            tn = TypeNode(
                node_id=type_id,
                name=name,
                file=file_id,
                line_start=node.start_point[0] + 1,
                definition=node.text.decode("utf-8", errors="replace")[:300],
                is_exported=not name.startswith("_"),
            )
            result.types.append(tn)
            result.defines.append(
                GraphEdge(src=file_id, dst=type_id, kind=EdgeKind.DEFINES, meta={"line": tn.line_start})
            )

    def _find_enclosing_class(
        self, line: int, class_spans: dict[str, tuple[int, int]]
    ) -> str | None:
        for class_name, (start, end) in class_spans.items():
            if start < line <= end:
                return class_name
        return None

    def _extract_todos(self, lines: list[str]) -> list[dict]:
        todos = []
        for i, line in enumerate(lines, 1):
            m = _TODO_RE.search(line)
            if m:
                todos.append({"line": i, "kind": m.group(1).upper(), "text": m.group(2).strip()})
        return todos

    def _extract_calls(self, root, result: ParseResult) -> None:
        if not result.functions:
            return
        sites = []
        for call in self._iter_type(root, "call_expression"):
            fn = call.child_by_field_name("function")
            if fn is None:
                continue
            name, scope = self._callee_info(fn)
            if name:
                sites.append((name, call.start_point[0] + 1, scope))
        self._emit_call_edges(sites, result)

    def _callee_info(self, fn) -> tuple[str | None, str]:
        # foo() -> (foo, "free") ; obj.method() -> (method, "attr") ;
        # this.method() -> (method, "self")
        if fn.type == "identifier":
            return fn.text.decode("utf-8", errors="replace"), "free"
        if fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            if prop is None:
                return None, "free"
            obj = fn.child_by_field_name("object")
            is_self = obj is not None and obj.type == "this"
            return prop.text.decode("utf-8", errors="replace"), "self" if is_self else "attr"
        return None, "free"

    def _iter_type(self, node, node_type: str):
        if node.type == node_type:
            yield node
        for child in node.children:
            yield from self._iter_type(child, node_type)
