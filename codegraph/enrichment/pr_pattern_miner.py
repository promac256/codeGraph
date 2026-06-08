"""PR Pattern Miner — extract recurring review feedback themes from merged PRs."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codegraph.git.github_client import GitHubClient
    from codegraph.graph.store import GraphStore

# ---------------------------------------------------------------------------
# Theme keyword patterns
# ---------------------------------------------------------------------------

_THEME_PATTERNS: dict[str, list[str]] = {
    "type_hints": [
        r"type hint", r"type annotation", r"add types?", r"missing types?",
        r"\btyping\b", r"\bmypy\b", r"\bpyright\b",
    ],
    "error_handling": [
        r"error handling", r"exception", r"try[- ]?except", r"\bcatch\b",
        r"handle.{0,15}error", r"what if.{0,25}fail", r"edge case",
        r"invalid input",
    ],
    "testing": [
        r"add test", r"test case", r"unit test", r"missing test",
        r"\bcoverage\b", r"\bmock\b", r"\bassert\b", r"\bfixture\b",
        r"not tested",
    ],
    "documentation": [
        r"docstring", r"add doc", r"missing doc", r"documentation",
        r"explain", r"unclear", r"add comment", r"document this",
    ],
    "naming": [
        r"naming", r"rename", r"better name", r"variable name", r"misleading",
        r"more descriptive", r"confusing name", r"abbrev",
    ],
    "complexity": [
        r"complex", r"simplify", r"refactor", r"extract method",
        r"too long", r"too many", r"split this", r"\breadability\b",
    ],
    "performance": [
        r"performance", r"\bslow\b", r"optimiz", r"efficient",
        r"\bn\+1\b", r"\bcache\b", r"unnecessary.{0,15}call", r"redundant",
    ],
    "security": [
        r"security", r"injection", r"sanitiz", r"\bXSS\b", r"\bCSRF\b",
        r"\bauth\b", r"secret", r"password.{0,10}leak", r"token.{0,10}log",
    ],
    "imports": [
        r"unused import", r"import.{0,10}order", r"circular import",
        r"absolute import", r"relative import",
    ],
    "style": [
        r"\bstyle\b", r"\bformat\b", r"\blint\b", r"whitespace",
        r"\bpep8\b", r"\bflake8\b", r"\bblack\b", r"\bisort\b",
        r"trailing comma",
    ],
}

_COMPILED: dict[str, list[re.Pattern]] = {
    theme: [re.compile(p, re.IGNORECASE) for p in patterns]
    for theme, patterns in _THEME_PATTERNS.items()
}


def _match_themes(text: str) -> list[str]:
    if not text:
        return []
    return [
        theme
        for theme, patterns in _COMPILED.items()
        if any(p.search(text) for p in patterns)
    ]


# ---------------------------------------------------------------------------
# Miner class
# ---------------------------------------------------------------------------


class PRPatternMiner:
    """
    Extracts recurring review feedback themes from merged GitHub PRs.

    Strategy:
      1. Fetch N most recently merged PRs via GitHub API.
      2. Collect all inline review comments + top-level review bodies.
      3. Pattern-match each comment against known feedback themes.
      4. Aggregate counts, examples, and affected files per theme.
      5. Persist result to the graph config table as 'pr_patterns'.
    """

    def __init__(
        self,
        store: "GraphStore",
        client: "GitHubClient",
        owner: str,
        repo: str,
    ) -> None:
        self._store = store
        self._client = client
        self._owner = owner
        self._repo = repo

    def mine(self, pr_limit: int = 30) -> dict:
        """Fetch PRs and extract recurring feedback themes."""
        prs = self._client.get_merged_prs(self._owner, self._repo, limit=pr_limit)

        theme_counts: Counter = Counter()
        theme_examples: dict[str, list[str]] = defaultdict(list)
        theme_files: dict[str, set[str]] = defaultdict(set)
        reviewer_counts: Counter = Counter()
        total_comments = 0

        for pr in prs:
            pr_num = pr["number"]
            pr_files: set[str] = set()

            # Inline review comments
            try:
                comments = self._client.get_pr_review_comments(
                    self._owner, self._repo, pr_num
                )
            except Exception:
                comments = []

            for c in comments:
                body = (c.get("body") or "").strip()
                if not body:
                    continue
                total_comments += 1
                reviewer = c.get("user", {}).get("login", "unknown")
                reviewer_counts[reviewer] += 1
                path = c.get("path", "")
                if path:
                    pr_files.add(path)

                for theme in _match_themes(body):
                    theme_counts[theme] += 1
                    if len(theme_examples[theme]) < 3:
                        theme_examples[theme].append(body[:120])
                    if path:
                        theme_files[theme].add(path)

            # Top-level review bodies
            try:
                reviews = self._client.get_pr_reviews(
                    self._owner, self._repo, pr_num
                )
            except Exception:
                reviews = []

            for r in reviews:
                body = (r.get("body") or "").strip()
                if not body:
                    continue
                total_comments += 1
                reviewer = r.get("user", {}).get("login", "unknown")
                reviewer_counts[reviewer] += 1

                for theme in _match_themes(body):
                    theme_counts[theme] += 1
                    if len(theme_examples[theme]) < 3:
                        theme_examples[theme].append(body[:120])

        themes_out: dict[str, dict] = {}
        for theme, count in theme_counts.most_common():
            themes_out[theme] = {
                "count": count,
                "examples": theme_examples[theme],
                "top_files": sorted(theme_files.get(theme, set()))[:5],
            }

        return {
            "prs_analyzed": len(prs),
            "total_comments": total_comments,
            "themes": themes_out,
            "top_reviewers": [
                {"login": login, "comments": cnt}
                for login, cnt in reviewer_counts.most_common(5)
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def mine_and_save(self, pr_limit: int = 30) -> dict:
        report = self.mine(pr_limit=pr_limit)
        self._store.set_config("pr_patterns", json.dumps(report))
        return report

    @staticmethod
    def load(store: "GraphStore") -> dict | None:
        raw = store.get_config("pr_patterns")
        return json.loads(raw) if raw else None
