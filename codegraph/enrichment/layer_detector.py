"""Architectural layer detection from file paths."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codegraph.graph.store import GraphStore

# Pattern → layer, evaluated in order (first match wins)
_LAYER_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|/)tests?/|_test\.|test_|\.test\.|\.spec\.", re.I), "test"),
    (re.compile(r"(^|/)(migration|schema|model|entity|orm|dao|repository)\b", re.I), "data"),
    (re.compile(r"(^|/)(route|handler|controller|view|template|api|endpoint|graphql|rest)\b", re.I), "presentation"),
    (re.compile(r"(^|/)(service|usecase|business|domain|core|logic|workflow)\b", re.I), "business"),
    (re.compile(r"(^|/)(infra|infrastructure|adapter|gateway|provider|client|driver)\b", re.I), "infrastructure"),
    (re.compile(r"(^|/)(config|setting|env|constant)\b", re.I), "config"),
    (re.compile(r"(^|/)(util|helper|common|shared|lib|pkg)\b", re.I), "utility"),
]


class LayerDetector:
    def detect(self, file_path: str) -> str:
        for pattern, layer in _LAYER_RULES:
            if pattern.search(file_path):
                return layer
        return "unknown"

    def annotate_store(self, store: "GraphStore") -> None:
        from codegraph.models import NodeKind

        updates = []
        for nid, data in list(store.graph.nodes(data=True)):
            if data.get("kind") == NodeKind.FILE:
                layer = self.detect(data.get("path", ""))
                updates.append((layer, nid))

        store.set_node_attr_bulk("layer", updates)
