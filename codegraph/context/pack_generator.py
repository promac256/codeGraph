"""Token-budget-aware context pack generator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore


@dataclass
class ContextPack:
    """Compressed, LLM-consumable codebase snapshot."""

    repo_name: str
    generated_at: str
    token_budget: int

    # Tier 1 — always included
    repo_overview: dict = field(default_factory=dict)
    architectural_layers: dict = field(default_factory=dict)
    hot_paths: list = field(default_factory=list)
    recent_changes: list = field(default_factory=list)
    public_api_summary: list = field(default_factory=list)

    # Tier 2 — fills remaining budget
    top_modules: list = field(default_factory=list)
    key_classes: list = field(default_factory=list)
    todos: list = field(default_factory=list)

    # Tier 2 addition — session notes (if any exist)
    session_notes: list = field(default_factory=list)

    # PR review patterns — populated by PRPatternMiner when available
    pr_patterns: dict = field(default_factory=dict)

    # Focus / role context — populated by ContextCompressor, empty in default packs
    focus_context: dict = field(default_factory=dict)

    # Tier 3 — index only (queried on demand via MCP)
    symbol_count: int = 0
    file_count: int = 0


class ContextPackGenerator:
    """
    Generates a token-budget-aware ContextPack for session-start context.

    Strategy:
      Tier 1 is always included (~2k tokens).
      Tier 2 fills remaining budget.
      Tier 3 stays as counts — available via MCP on demand.
    """

    DEFAULT_TOKEN_BUDGET = 8000

    def __init__(
        self,
        store: "GraphStore",
        query: "GraphQuery",
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        notes_path: "Path | None" = None,
    ):
        self.store = store
        self.query = query
        self.token_budget = token_budget
        self.notes_path = notes_path

    def generate(self) -> ContextPack:
        repo_name = self.store.get_config("repo_name", "unknown")
        pack = ContextPack(
            repo_name=repo_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
            token_budget=self.token_budget,
        )

        # Tier 1
        pack.repo_overview = self.query.get_overview()
        pack.architectural_layers = {
            k: [f.replace("file:", "") for f in v]
            for k, v in self.query.get_architectural_layers().items()
        }
        pack.hot_paths = self.query.get_hot_paths(top_n=15)
        pack.recent_changes = self.query.get_recent_changes(limit=5)
        pack.public_api_summary = self._summarize_public_api()

        used = self._estimate_tokens(pack)
        remaining = self.token_budget - used

        # Tier 2
        if remaining > 1000:
            pack.top_modules = self._get_top_modules(token_limit=remaining // 3)
            remaining -= self._estimate_tokens(pack.top_modules)

        if remaining > 500:
            pack.key_classes = self._get_key_classes(token_limit=remaining // 2)
            remaining -= self._estimate_tokens(pack.key_classes)

        if remaining > 200:
            pack.todos = self.query.get_todos(limit=20)

        # PR patterns — include top themes if stored and budget allows
        if remaining > 400:
            from codegraph.enrichment.pr_pattern_miner import PRPatternMiner
            patterns = PRPatternMiner.load(self.store)
            if patterns and patterns.get("themes"):
                pack.pr_patterns = {
                    "prs_analyzed": patterns.get("prs_analyzed", 0),
                    "top_themes": list(patterns["themes"].keys())[:6],
                }
                remaining -= self._estimate_tokens(pack.pr_patterns)

        # Session notes — included when they exist and budget allows.
        # Notes that annotate hot-path symbols are preferred over merely
        # recent ones, so the highest-leverage knowledge survives the cut.
        if self.notes_path and remaining > 300:
            from codegraph.context.session_notes import SessionNotesManager
            mgr = SessionNotesManager(self.notes_path)
            if mgr.exists():
                candidates = mgr.read_recent(max_notes=24)
                hot_names = {
                    h.get("name", "") for h in pack.hot_paths
                } | {
                    h.get("file", "").replace("file:", "") for h in pack.hot_paths
                }
                def _is_hot(note: dict) -> bool:
                    return any(
                        r in hot_names or r.rsplit(".", 1)[0] in hot_names
                        for r in note.get("refs", [])
                    )
                hot = [n for n in candidates if _is_hot(n)]
                rest = [n for n in candidates if not _is_hot(n)]
                pack.session_notes = (hot + rest)[:8]

        # Tier 3
        from codegraph.models import NodeKind
        G = self.store.graph
        pack.file_count = sum(
            1 for _, d in G.nodes(data=True) if d.get("kind") == NodeKind.FILE
        )
        pack.symbol_count = G.number_of_nodes()

        return pack

    def to_json(self, pack: ContextPack) -> bytes:
        import orjson
        return orjson.dumps(pack.__dict__, option=orjson.OPT_INDENT_2)

    def to_markdown(self, pack: ContextPack) -> str:
        from codegraph.context.claude_md import ClaudeMdWriter
        return ClaudeMdWriter(pack).render()

    def to_html(self, pack: ContextPack) -> str:
        from codegraph.context.html_reporter import HtmlReporter
        return HtmlReporter(pack).render()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_public_api(self) -> list:
        api = self.query.get_public_api()
        return [
            {
                "name": s.get("name", ""),
                "kind": s.get("kind", ""),
                "file": s.get("file", s.get("path", "")).replace("file:", ""),
                "sig": s.get("signature", "")[:80],
            }
            for s in api[:30]
        ]

    def _get_top_modules(self, token_limit: int) -> list:
        from codegraph.models import NodeKind

        G = self.store.graph
        items = []
        for nid, data in G.nodes(data=True):
            if data.get("kind") == NodeKind.FILE:
                score = data.get("commit_count", 0) + G.in_degree(nid) * 2
                items.append((score, data))
        items.sort(key=lambda x: x[0], reverse=True)

        result, tokens = [], 0
        for _, data in items[:60]:
            entry = {
                "path": data.get("path", ""),
                "lang": data.get("lang", ""),
                "lines": data.get("line_count", 0),
                "commit_count": data.get("commit_count", 0),
                "layer": data.get("layer", "unknown"),
            }
            t = self._estimate_tokens(entry)
            if tokens + t > token_limit:
                break
            result.append(entry)
            tokens += t
        return result

    def _get_key_classes(self, token_limit: int) -> list:
        from codegraph.models import NodeKind

        G = self.store.graph
        items = []
        for nid, data in G.nodes(data=True):
            if data.get("kind") == NodeKind.CLASS:
                sub_count = sum(
                    1 for s, _, k in G.in_edges(nid, keys=True)
                    if k == "inherits"
                )
                items.append((sub_count, data))
        items.sort(key=lambda x: x[0], reverse=True)

        result, tokens = [], 0
        for _, data in items[:40]:
            entry = {
                "name": data.get("name", ""),
                "file": data.get("file", "").replace("file:", ""),
                "docstring": (data.get("docstring") or "")[:100],
                "bases": data.get("bases", []),
            }
            t = self._estimate_tokens(entry)
            if tokens + t > token_limit:
                break
            result.append(entry)
            tokens += t
        return result

    def _estimate_tokens(self, obj: Any) -> int:
        import dataclasses
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            obj = dataclasses.asdict(obj)
        text = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
        return max(1, len(text) // 4)
