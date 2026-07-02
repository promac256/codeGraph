"""Convention miner: detect naming patterns, idioms, and code style from the graph."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore

_STORE_KEY = "conventions"

# Naming style detectors
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_CAMEL_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_UPPER_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_PRIVATE_SINGLE_RE = re.compile(r"^_[a-z]")
_PRIVATE_DOUBLE_RE = re.compile(r"^__[a-z]")


def _naming_style(name: str) -> str:
    if not name:
        return "unknown"
    if _UPPER_RE.match(name):
        return "UPPER_SNAKE"
    if _PASCAL_RE.match(name):
        return "PascalCase"
    if _SNAKE_RE.match(name):
        return "snake_case"
    if _CAMEL_RE.match(name):
        return "camelCase"
    if name.startswith("__"):
        return "dunder"
    if name.startswith("_"):
        return "private"
    return "other"


def _file_naming_style(filename: str) -> str:
    stem = filename.split("/")[-1].split(".")[0]
    if "-" in stem:
        return "kebab-case"
    if "_" in stem:
        return "snake_case"
    if stem and stem[0].isupper():
        return "PascalCase"
    if stem and stem == stem.lower():
        return "lowercase"
    return "camelCase"


# ---------------------------------------------------------------------------
# ConventionMiner
# ---------------------------------------------------------------------------


class ConventionMiner:
    """Analyses the knowledge graph to extract codebase conventions.

    Results are stored in the GraphStore config table under key "conventions"
    so the MCP tool can serve them without re-computing on every call.
    """

    def __init__(self, store: "GraphStore") -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mine(self) -> dict:
        """Compute and return the full conventions report."""
        G = self._store.graph

        func_data = [d for _, d in G.nodes(data=True) if d.get("kind") == "function"]
        class_data = [d for _, d in G.nodes(data=True) if d.get("kind") == "class"]
        file_data = [d for _, d in G.nodes(data=True) if d.get("kind") == "file"]

        report = {
            "naming": self._naming(func_data, class_data, file_data),
            "documentation": self._doc_coverage(func_data, class_data),
            "patterns": self._code_patterns(func_data, class_data),
            "complexity": self._complexity_stats(func_data),
            "imports": self._import_patterns(),
            "tests": self._test_stats(file_data, func_data),
            "languages": self._language_stats(file_data),
        }
        return report

    def mine_and_save(self) -> dict:
        """Compute the conventions report and persist it in the store."""
        report = self.mine()
        self._store.set_config(_STORE_KEY, json.dumps(report))
        return report

    @staticmethod
    def load(store: "GraphStore") -> dict | None:
        """Load a previously mined report from the store (None if not yet run)."""
        raw = store.get_config(_STORE_KEY)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------------
    # Analysis sections
    # ------------------------------------------------------------------

    def _naming(
        self, func_data: list[dict], class_data: list[dict], file_data: list[dict]
    ) -> dict:
        func_styles: Counter = Counter()
        for d in func_data:
            name = d.get("name", "")
            if name.startswith("test_") or name.startswith("Test"):
                continue  # skip test functions; they follow test framework conventions
            func_styles[_naming_style(name)] += 1

        class_styles: Counter = Counter()
        for d in class_data:
            class_styles[_naming_style(d.get("name", ""))] += 1

        file_styles: Counter = Counter()
        for d in file_data:
            path = d.get("path", "")
            if path:
                file_styles[_file_naming_style(path)] += 1

        # Dominant style = most common non-"other" style
        def dominant(c: Counter) -> str:
            filtered = {k: v for k, v in c.items() if k not in ("other", "unknown")}
            return max(filtered, key=filtered.get, default="unknown") if filtered else "unknown"

        private_single = sum(1 for d in func_data if _PRIVATE_SINGLE_RE.match(d.get("name", "")))
        private_double = sum(1 for d in func_data if _PRIVATE_DOUBLE_RE.match(d.get("name", "")))

        return {
            "function_style": dominant(func_styles),
            "function_style_counts": dict(func_styles.most_common()),
            "class_style": dominant(class_styles),
            "class_style_counts": dict(class_styles.most_common()),
            "file_style": dominant(file_styles),
            "file_style_counts": dict(file_styles.most_common()),
            "private_convention": "__dunder" if private_double > private_single else "_single" if private_single else "none",
        }

    def _doc_coverage(self, func_data: list[dict], class_data: list[dict]) -> dict:
        def _coverage(nodes: list[dict], exported_only: bool = True) -> dict:
            pool = [d for d in nodes if d.get("is_exported", True)] if exported_only else nodes
            if not pool:
                return {"total": 0, "documented": 0, "pct": 0}
            documented = sum(1 for d in pool if d.get("docstring") or d.get("llm_summary"))
            return {
                "total": len(pool),
                "documented": documented,
                "pct": round(documented / len(pool) * 100),
            }

        return {
            "public_functions": _coverage(func_data),
            "public_classes": _coverage(class_data),
            "all_functions": _coverage(func_data, exported_only=False),
        }

    def _code_patterns(self, func_data: list[dict], class_data: list[dict]) -> dict:
        async_count = sum(1 for d in func_data if d.get("is_async"))
        property_count = sum(1 for d in func_data if d.get("is_property"))
        classmethod_count = sum(1 for d in func_data if d.get("is_classmethod"))
        staticmethod_count = sum(1 for d in func_data if d.get("is_staticmethod"))
        abstract_count = sum(1 for d in class_data if d.get("is_abstract"))
        dataclass_count = sum(1 for d in class_data if d.get("is_dataclass"))
        exported_class_count = sum(1 for d in class_data if d.get("is_exported", True))
        exported_func_count = sum(1 for d in func_data if d.get("is_exported", True))

        total_funcs = len(func_data) or 1
        total_classes = len(class_data) or 1

        # Detect error-return pattern from signatures
        result_type_count = sum(
            1 for d in func_data
            if "Result<" in (d.get("signature") or "") or "-> Result" in (d.get("signature") or "")
        )
        optional_return_count = sum(
            1 for d in func_data
            if "Optional[" in (d.get("signature") or "") or "| None" in (d.get("signature") or "")
        )
        tuple_error_count = sum(
            1 for d in func_data
            if "error)" in (d.get("signature") or "").lower() or ", error" in (d.get("signature") or "")
        )

        return {
            "async_functions": async_count,
            "async_pct": round(async_count / total_funcs * 100),
            "property_decorators": property_count,
            "classmethod_decorators": classmethod_count,
            "staticmethod_decorators": staticmethod_count,
            "abstract_classes": abstract_count,
            "dataclasses": dataclass_count,
            "exported_functions_pct": round(exported_func_count / total_funcs * 100),
            "exported_classes_pct": round(exported_class_count / total_classes * 100),
            "error_return_style": {
                "Result_type": result_type_count,
                "Optional_return": optional_return_count,
                "tuple_error": tuple_error_count,
            },
        }

    def _complexity_stats(self, func_data: list[dict]) -> dict:
        complexities = [d.get("complexity") or 1 for d in func_data]
        if not complexities:
            return {}

        total = len(complexities)
        avg = sum(complexities) / total
        high = [d for d in func_data if (d.get("complexity") or 1) > 10]
        moderate = [d for d in func_data if 5 < (d.get("complexity") or 1) <= 10]

        high_sorted = sorted(high, key=lambda d: d.get("complexity") or 0, reverse=True)[:10]

        return {
            "average": round(avg, 1),
            "max": max(complexities),
            "min": min(complexities),
            "high_complexity_count": len(high),
            "moderate_complexity_count": len(moderate),
            "high_complexity_functions": [
                {
                    "name": d.get("qualified_name") or d.get("name"),
                    "file": d.get("file", ""),
                    "complexity": d.get("complexity"),
                }
                for d in high_sorted
            ],
        }

    def _import_patterns(self) -> dict:
        """Find the most frequently imported modules across all files."""
        module_counter: Counter = Counter()
        for meta in self._store.iter_edge_meta("imports"):
            module = meta.get("module", "")
            if module:
                # Take top-level crate/package only
                top = module.split("::")[0].split(".")[0].split("/")[0]
                module_counter[top] += 1

        return {
            "top_imports": [
                {"module": mod, "count": cnt}
                for mod, cnt in module_counter.most_common(15)
            ],
            "total_import_edges": sum(module_counter.values()),
        }

    def _test_stats(self, file_data: list[dict], func_data: list[dict]) -> dict:
        test_files = [d for d in file_data if d.get("is_test")]
        code_files = [d for d in file_data if not d.get("is_test")]
        test_funcs = [d for d in func_data if d.get("name", "").startswith("test_")
                      or d.get("name", "").startswith("Test")]

        total_files = len(file_data) or 1
        return {
            "test_files": len(test_files),
            "code_files": len(code_files),
            "test_file_ratio": round(len(test_files) / total_files * 100),
            "test_functions": len(test_funcs),
        }

    def _language_stats(self, file_data: list[dict]) -> dict:
        lang_counter: Counter = Counter()
        line_counter: Counter = Counter()
        for d in file_data:
            lang = d.get("lang", "unknown")
            lang_counter[lang] += 1
            line_counter[lang] += d.get("line_count", 0)
        return {
            "files_by_lang": dict(lang_counter.most_common()),
            "lines_by_lang": dict(line_counter.most_common()),
            "primary_language": lang_counter.most_common(1)[0][0] if lang_counter else "unknown",
        }
