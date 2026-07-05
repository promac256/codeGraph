"""Tests for the LLM enricher (fully mocked — no real API calls)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codegraph.enrichment.llm_enricher import LLMEnricher, _cache_key, _build_entry
from codegraph.graph.store import GraphStore
from codegraph.models import ClassNode, FunctionNode, NodeKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, api_key: str = "test-key"):
    from codegraph.config import Settings

    d = tmp_path / ".codegraph"
    d.mkdir()
    return Settings(repo_path=tmp_path, anthropic_api_key=api_key)


def _insert_function(store: GraphStore, node_id: str, name: str, docstring=None, llm_summary=None):
    fn = FunctionNode(
        node_id=node_id,
        name=name,
        qualified_name=name,
        file="file:test.py",
        signature=f"def {name}():",
        docstring=docstring,
        llm_summary=llm_summary,
    )
    store.upsert_node(fn)
    store.commit_transaction()


def _insert_class(store: GraphStore, node_id: str, name: str, docstring=None, llm_summary=None):
    cn = ClassNode(
        node_id=node_id,
        name=name,
        file="file:test.py",
        docstring=docstring,
        llm_summary=llm_summary,
    )
    store.upsert_node(cn)
    store.commit_transaction()


def _mock_anthropic_response(text: str):
    content = MagicMock()
    content.text = text
    msg = MagicMock()
    msg.content = [content]
    return msg


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_cache_key_is_deterministic(self):
        k1 = _cache_key("func:a.py::foo", "def foo():", None)
        k2 = _cache_key("func:a.py::foo", "def foo():", None)
        assert k1 == k2

    def test_cache_key_differs_on_sig_change(self):
        k1 = _cache_key("func:a.py::foo", "def foo():", None)
        k2 = _cache_key("func:a.py::foo", "def foo(x: int):", None)
        assert k1 != k2

    def test_cache_key_differs_on_docstring_change(self):
        k1 = _cache_key("id", "sig", None)
        k2 = _cache_key("id", "sig", "some doc")
        assert k1 != k2

    def test_build_entry_includes_kind_and_file(self):
        node = {
            "kind": "function",
            "name": "foo",
            "qualified_name": "MyClass.foo",
            "signature": "def foo(self) -> str:",
            "file": "file:src/models.py",
            "docstring": None,
        }
        entry = _build_entry(node)
        assert "[function]" in entry
        assert "MyClass.foo" in entry       # qualified_name in header
        assert "def foo(self) -> str:" in entry  # signature on next line
        assert "src/models.py" in entry
        assert "no existing documentation" in entry

    def test_build_entry_shows_existing_docstring(self):
        node = {
            "kind": "class",
            "name": "Dog",
            "file": "file:models.py",
            "docstring": "A dog class.",
        }
        entry = _build_entry(node)
        assert "A dog class." in entry


# ---------------------------------------------------------------------------
# Integration tests: enrichment logic (mocked Anthropic client)
# ---------------------------------------------------------------------------


class TestLLMEnricher:
    def test_skips_already_enriched_nodes(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::a", "already_done", llm_summary="Existing summary")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        with patch.object(enricher, "_make_client") as mock_client:
            stats = enricher.enrich()

        mock_client.assert_not_called()
        assert stats["enriched"] == 0
        assert stats["cached"] == 0

    def test_skips_documented_nodes_by_default(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::b", "has_docs", docstring="Already documented.")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        with patch.object(enricher, "_make_client") as mock_client:
            stats = enricher.enrich(skip_documented=True)

        mock_client.assert_not_called()
        assert stats["enriched"] == 0

    def test_includes_documented_when_flag_set(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::c", "has_docs", docstring="Already documented.")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        api_response = json.dumps({"0": "Summary for has_docs."})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(api_response)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich(skip_documented=False)

        assert stats["enriched"] == 1

    def test_enriches_undocumented_function(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::d", "undocumented_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        api_response = json.dumps({"0": "Does something useful."})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(api_response)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich()

        assert stats["enriched"] == 1
        assert stats["cached"] == 0

        # Verify it was written to SQLite
        import orjson
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='func:t::d'"
        ).fetchone()
        data = orjson.loads(row[0])
        assert data["llm_summary"] == "Does something useful."

    def test_enriches_undocumented_class(self, tmp_db, tmp_path):
        _insert_class(tmp_db, "class:t::Dog", "Dog")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        api_response = json.dumps({"0": "Represents a dog."})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(api_response)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich()

        assert stats["enriched"] == 1

    def test_uses_cache_on_second_run(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::e", "cached_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        # Seed the cache manually
        import diskcache
        cache_dir = settings.codegraph_dir / "llm_cache"
        cache_dir.mkdir(exist_ok=True)
        with diskcache.Cache(str(cache_dir)) as c:
            key = _cache_key("func:t::e", "def cached_fn():", None)
            c[key] = "Cached summary."

        mock_client = MagicMock()
        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich()

        # API should not have been called
        mock_client.messages.create.assert_not_called()
        assert stats["cached"] == 1
        assert stats["enriched"] == 0

    def test_handles_api_error_gracefully(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::f", "error_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API timeout")

        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich()

        assert stats["errors"] == 1
        assert stats["enriched"] == 0

    def test_handles_missing_api_key(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::g", "no_key_fn")
        settings = _make_settings(tmp_path, api_key=None)
        enricher = LLMEnricher(tmp_db, settings)
        stats = enricher.enrich()
        assert stats["errors"] == 1

    def test_batches_multiple_nodes(self, tmp_db, tmp_path):
        for i in range(5):
            _insert_function(tmp_db, f"func:t::fn{i}", f"fn{i}")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        call_count = 0

        def fake_call(client, batch):
            nonlocal call_count
            call_count += 1
            return {str(i): f"Summary {i}" for i in range(len(batch))}

        mock_client = MagicMock()
        with patch.object(enricher, "_make_client", return_value=mock_client):
            with patch.object(enricher, "_call_api", side_effect=fake_call):
                stats = enricher.enrich(batch_size=3)

        assert stats["enriched"] == 5
        assert call_count == 2  # ceil(5 / 3)

    def test_patches_in_memory_graph_node(self, tmp_db, tmp_path):
        tmp_db.load_graph_to_memory()
        _insert_function(tmp_db, "func:t::h", "graph_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        api_response = json.dumps({"0": "In-memory summary."})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(api_response)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            enricher.enrich()

        assert tmp_db.graph.nodes["func:t::h"].get("llm_summary") == "In-memory summary."

    def test_strips_markdown_code_fences(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::i", "fenced_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        fenced = '```json\n{"0": "Fenced summary."}\n```'
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(fenced)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            stats = enricher.enrich()

        assert stats["enriched"] == 1

    def test_returns_zero_stats_when_no_candidates(self, tmp_db, tmp_path):
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)
        stats = enricher.enrich()
        assert stats == {"enriched": 0, "cached": 0, "errors": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Staleness metadata + survival across updates
# ---------------------------------------------------------------------------


class TestStalenessAndSurvival:
    def test_enrichment_writes_staleness_metadata(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::meta", "meta_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        api_response = json.dumps({"0": "Summary."})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(api_response)

        with patch.object(enricher, "_make_client", return_value=mock_client):
            enricher.enrich()

        import orjson
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='func:t::meta'"
        ).fetchone()
        data = orjson.loads(row[0])
        assert data["llm_summary"] == "Summary."
        assert data["llm_enriched_at"]
        assert data["llm_cache_key"] == _cache_key(
            "func:t::meta", "def meta_fn():", None
        )

    def test_reattach_restores_summary_after_reingest(self, tmp_db, tmp_path):
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        # Seed the disk cache as a prior enrichment run would have
        import diskcache
        cache_dir = settings.codegraph_dir / "llm_cache"
        cache_dir.mkdir(exist_ok=True)
        key = _cache_key("func:t::surv", "def surv_fn():", None)
        with diskcache.Cache(str(cache_dir)) as c:
            c[key] = "Survived summary."

        # Node recreated by update without llm_summary (the silent-drop case)
        _insert_function(tmp_db, "func:t::surv", "surv_fn")

        restored = enricher.reattach_from_cache()
        assert restored == 1

        import orjson
        row = tmp_db._db.execute(
            "SELECT data FROM nodes WHERE node_id='func:t::surv'"
        ).fetchone()
        data = orjson.loads(row[0])
        assert data["llm_summary"] == "Survived summary."
        assert data["llm_cache_key"] == key

    def test_reattach_skips_changed_signature(self, tmp_db, tmp_path):
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)

        import diskcache
        cache_dir = settings.codegraph_dir / "llm_cache"
        cache_dir.mkdir(exist_ok=True)
        old_key = _cache_key("func:t::chg", "def chg_fn():", None)
        with diskcache.Cache(str(cache_dir)) as c:
            c[old_key] = "Stale summary."

        # Signature changed since the summary was generated → cache miss
        fn = FunctionNode(
            node_id="func:t::chg",
            name="chg_fn",
            qualified_name="chg_fn",
            file="file:test.py",
            signature="def chg_fn(x: int):",
        )
        tmp_db.upsert_node(fn)
        tmp_db.commit_transaction()

        assert enricher.reattach_from_cache() == 0

    def test_reattach_noop_without_cache_dir(self, tmp_db, tmp_path):
        _insert_function(tmp_db, "func:t::nocache", "nocache_fn")
        settings = _make_settings(tmp_path)
        enricher = LLMEnricher(tmp_db, settings)
        assert enricher.reattach_from_cache() == 0
