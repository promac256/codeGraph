"""LLM enrichment: generate summaries for undocumented functions and classes."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    from codegraph.config import Settings
    from codegraph.graph.store import GraphStore

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a code documentation assistant. "
    "For each numbered symbol below, write a concise 1-2 sentence description "
    "of what it does. Return ONLY a valid JSON object mapping the index (as a "
    'string) to the description, e.g. {"0": "Creates a new user record.", '
    '"1": "Validates an email address format."}'
)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_MAX_RESPONSE_TOKENS = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_key(node_id: str, sig: str, docstring: str | None) -> str:
    raw = f"{node_id}|{sig}|{docstring or ''}".encode()
    return hashlib.sha256(raw).hexdigest()


def _build_entry(node: dict) -> str:
    kind = node.get("kind", "symbol")
    name = node.get("qualified_name") or node.get("name", "?")
    sig = node.get("signature") or node.get("definition") or name
    file_ = node.get("file", "")
    doc = node.get("docstring")
    header = f"[{kind}] {name}"
    if sig and sig != name:
        header += f"\n   Signature: {sig}"
    parts = [header, f"   File: {file_}"]
    if doc:
        parts.append(f"   Existing doc: {doc[:120]}")
    else:
        parts.append("   (no existing documentation)")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict[str, str]:
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        inner = text.split("```")
        text = inner[1] if len(inner) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("LLM response not valid JSON: %.200s", text)
        return {}


# ---------------------------------------------------------------------------
# LLMEnricher
# ---------------------------------------------------------------------------


class LLMEnricher:
    """Batch-enriches undocumented graph nodes using the Anthropic API.

    Results are cached in .codegraph/llm_cache/ keyed on a hash of
    node_id + signature + existing docstring, so re-runs only call the
    API for symbols that changed or were never enriched.
    """

    def __init__(self, store: "GraphStore", settings: "Settings") -> None:
        self._store = store
        self._settings = settings
        self._cache = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_cache(self):
        if self._cache is None:
            import diskcache

            cache_dir = self._settings.codegraph_dir / "llm_cache"
            cache_dir.mkdir(exist_ok=True)
            self._cache = diskcache.Cache(str(cache_dir))
        return self._cache

    def _make_client(self):
        import anthropic

        api_key = self._settings.anthropic_api_key
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not configured. "
                "Set CODEGRAPH_ANTHROPIC_API_KEY env var or anthropic_api_key in codegraph.toml."
            )
        return anthropic.Anthropic(api_key=api_key)

    def _fetch_candidates(self, skip_documented: bool, kinds: tuple[str, ...]) -> list[dict]:
        placeholders = ",".join("?" * len(kinds))
        cur = self._store._db.execute(
            f"SELECT data FROM nodes WHERE kind IN ({placeholders})", kinds
        )
        candidates = []
        for (data,) in cur:
            node = orjson.loads(data)
            if node.get("llm_summary"):
                continue
            if skip_documented and node.get("docstring"):
                continue
            candidates.append(node)
        return candidates

    def _call_api(self, client, batch: list[dict]) -> dict[str, str]:
        entries = "\n\n".join(f"{i}. {_build_entry(node)}" for i, node in enumerate(batch))
        message = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=_MAX_RESPONSE_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": entries}],
        )
        return _parse_json_response(message.content[0].text)

    def _patch_node(self, node_id: str, summary: str, cache_key: str | None = None) -> None:
        from datetime import datetime, timezone

        row = self._store._db.execute(
            "SELECT data FROM nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if not row:
            return
        enriched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data = orjson.loads(row[0])
        data["llm_summary"] = summary
        data["llm_enriched_at"] = enriched_at
        if cache_key:
            data["llm_cache_key"] = cache_key
        self._store._db.execute(
            "UPDATE nodes SET data=? WHERE node_id=?",
            (orjson.dumps(data).decode(), node_id),
        )
        if node_id in self._store.graph:
            attrs = self._store.graph.nodes[node_id]
            attrs["llm_summary"] = summary
            attrs["llm_enriched_at"] = enriched_at
            if cache_key:
                attrs["llm_cache_key"] = cache_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich(
        self,
        skip_documented: bool = True,
        batch_size: int = 20,
        kinds: tuple[str, ...] = ("function", "class"),
        progress=None,
    ) -> dict[str, int]:
        """Run enrichment. Returns counts: enriched, cached, errors, skipped."""
        stats: dict[str, int] = {"enriched": 0, "cached": 0, "errors": 0, "skipped": 0}

        candidates = self._fetch_candidates(skip_documented, kinds)
        if not candidates:
            return stats

        try:
            client = self._make_client()
        except RuntimeError as exc:
            log.error("%s", exc)
            stats["errors"] += 1
            return stats

        cache = self._get_cache()

        task = None
        if progress is not None:
            task = progress.add_task("[cyan]LLM enrichment", total=len(candidates))

        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start : batch_start + batch_size]

            to_call: list[dict] = []
            cache_results: list[tuple[str, str, str]] = []  # (node_id, summary, cache_key)

            for node in batch:
                sig = node.get("signature") or node.get("name", "")
                key = _cache_key(node["node_id"], sig, node.get("docstring"))
                if key in cache:
                    cache_results.append((node["node_id"], cache[key], key))
                else:
                    to_call.append(node)

            for node_id, summary, key in cache_results:
                self._patch_node(node_id, summary, cache_key=key)
                stats["cached"] += 1

            if to_call:
                try:
                    results = self._call_api(client, to_call)
                    for idx_str, summary in results.items():
                        try:
                            idx = int(idx_str)
                        except ValueError:
                            continue
                        if idx >= len(to_call):
                            continue
                        node = to_call[idx]
                        sig = node.get("signature") or node.get("name", "")
                        key = _cache_key(node["node_id"], sig, node.get("docstring"))
                        cache[key] = summary
                        self._patch_node(node["node_id"], summary, cache_key=key)
                        stats["enriched"] += 1
                    # Count nodes the API didn't return results for
                    missing = len(to_call) - len(results)
                    if missing > 0:
                        stats["skipped"] += missing
                except Exception as exc:
                    log.warning("LLM batch failed (%d nodes): %s", len(to_call), exc)
                    stats["errors"] += len(to_call)

            if progress is not None and task is not None:
                progress.advance(task, len(batch))

        self._store.commit_transaction()
        return stats

    def reattach_from_cache(
        self, kinds: tuple[str, ...] = ("function", "class")
    ) -> int:
        """Re-attach cached summaries to nodes that lost them on re-ingest.

        `codegraph update` removes and re-parses changed files, which drops
        `llm_summary` from recreated nodes. Summaries whose cache key
        (node_id + signature + docstring) is unchanged are restored from the
        disk cache without any API call. Returns the number restored.

        No-op (returns 0) if enrichment has never been run for this repo.
        """
        cache_dir = self._settings.codegraph_dir / "llm_cache"
        if not cache_dir.exists():
            return 0
        cache = self._get_cache()

        placeholders = ",".join("?" * len(kinds))
        cur = self._store._db.execute(
            f"SELECT data FROM nodes WHERE kind IN ({placeholders})", kinds
        )
        restored = 0
        for (data,) in cur.fetchall():
            node = orjson.loads(data)
            if node.get("llm_summary"):
                continue
            sig = node.get("signature") or node.get("name", "")
            key = _cache_key(node["node_id"], sig, node.get("docstring"))
            if key in cache:
                self._patch_node(node["node_id"], cache[key], cache_key=key)
                restored += 1
        if restored:
            self._store.commit_transaction()
        return restored
