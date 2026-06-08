"""Tests for the PR pattern miner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from codegraph.enrichment.pr_pattern_miner import PRPatternMiner, _match_themes
from codegraph.models import FileNode


# ---------------------------------------------------------------------------
# Theme matching
# ---------------------------------------------------------------------------


class TestMatchThemes:
    def test_type_hints_match(self):
        assert "type_hints" in _match_themes("Please add type hints to this function")

    def test_error_handling_match(self):
        assert "error_handling" in _match_themes("Missing error handling for the None case")

    def test_testing_match(self):
        assert "testing" in _match_themes("Can you add a test case for this edge case?")

    def test_documentation_match(self):
        assert "documentation" in _match_themes("This needs a docstring")

    def test_naming_match(self):
        assert "naming" in _match_themes("The variable name is confusing, please rename")

    def test_complexity_match(self):
        assert "complexity" in _match_themes("This function is too complex, please simplify it")

    def test_performance_match(self):
        assert "performance" in _match_themes("This loop has an N+1 query problem")

    def test_security_match(self):
        assert "security" in _match_themes("Potential SQL injection vulnerability here")

    def test_style_match(self):
        assert "style" in _match_themes("Please run black formatter before merging")

    def test_imports_match(self):
        assert "imports" in _match_themes("There's an unused import at the top")

    def test_no_match_returns_empty(self):
        assert _match_themes("LGTM! Great work.") == []

    def test_multiple_themes_matched(self):
        text = "Missing type hints and no error handling"
        themes = _match_themes(text)
        assert "type_hints" in themes
        assert "error_handling" in themes

    def test_empty_string(self):
        assert _match_themes("") == []

    def test_case_insensitive(self):
        assert "type_hints" in _match_themes("ADD TYPE HINTS PLEASE")


# ---------------------------------------------------------------------------
# PRPatternMiner with mocked GitHub client
# ---------------------------------------------------------------------------


def _make_client(prs=None, comments=None, reviews=None):
    client = MagicMock()
    client.get_merged_prs.return_value = prs or []
    client.get_pr_review_comments.return_value = comments or []
    client.get_pr_reviews.return_value = reviews or []
    return client


def _fake_prs(n=3):
    return [
        {"number": i, "merged_at": f"2026-01-0{i}T12:00:00Z", "title": f"PR {i}"}
        for i in range(1, n + 1)
    ]


class TestPRPatternMiner:
    def test_mine_returns_expected_keys(self, tmp_db):
        client = _make_client(prs=_fake_prs(2))
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine(pr_limit=2)
        assert "prs_analyzed" in result
        assert "total_comments" in result
        assert "themes" in result
        assert "top_reviewers" in result
        assert "generated_at" in result

    def test_prs_analyzed_count(self, tmp_db):
        prs = _fake_prs(3)
        client = _make_client(prs=prs)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert result["prs_analyzed"] == 3

    def test_no_prs_returns_empty_themes(self, tmp_db):
        client = _make_client(prs=[])
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert result["themes"] == {}
        assert result["prs_analyzed"] == 0

    def test_comments_matched_to_themes(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "Please add type hints here", "path": "src/foo.py",
             "user": {"login": "alice"}},
            {"body": "Missing error handling for None", "path": "src/bar.py",
             "user": {"login": "alice"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert "type_hints" in result["themes"]
        assert "error_handling" in result["themes"]

    def test_comment_count_is_accurate(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "type hints", "path": "a.py", "user": {"login": "bob"}},
            {"body": "add tests", "path": "b.py", "user": {"login": "carol"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert result["total_comments"] >= 2

    def test_reviewer_counted(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "type hints needed", "path": "a.py", "user": {"login": "alice"}},
            {"body": "type hints needed", "path": "b.py", "user": {"login": "alice"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        reviewer_logins = [r["login"] for r in result["top_reviewers"]]
        assert "alice" in reviewer_logins

    def test_theme_example_captured(self, tmp_db):
        prs = _fake_prs(1)
        body = "Please add type hints to this function signature"
        comments = [{"body": body, "path": "x.py", "user": {"login": "dave"}}]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        examples = result["themes"].get("type_hints", {}).get("examples", [])
        assert len(examples) >= 1
        assert body[:50] in examples[0]

    def test_top_file_captured(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "add type hints", "path": "src/api.py", "user": {"login": "eve"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        files = result["themes"]["type_hints"]["top_files"]
        assert "src/api.py" in files

    def test_reviews_body_included(self, tmp_db):
        prs = _fake_prs(1)
        reviews = [
            {"body": "Overall this needs more documentation", "user": {"login": "frank"}},
        ]
        client = _make_client(prs=prs, reviews=reviews)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert "documentation" in result["themes"]

    def test_empty_comment_bodies_skipped(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "", "path": "a.py", "user": {"login": "g"}},
            {"body": None, "path": "b.py", "user": {"login": "g"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert result["total_comments"] == 0

    def test_api_errors_handled_gracefully(self, tmp_db):
        prs = _fake_prs(2)
        client = _make_client(prs=prs)
        client.get_pr_review_comments.side_effect = Exception("API rate limit")
        client.get_pr_reviews.side_effect = Exception("API rate limit")
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine()
        assert result["prs_analyzed"] == 2
        assert result["total_comments"] == 0


# ---------------------------------------------------------------------------
# mine_and_save / load
# ---------------------------------------------------------------------------


class TestMineAndSave:
    def test_saves_to_store(self, tmp_db):
        prs = _fake_prs(1)
        comments = [
            {"body": "add type hints", "path": "a.py", "user": {"login": "h"}},
        ]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        miner.mine_and_save()
        raw = tmp_db.get_config("pr_patterns")
        assert raw is not None
        data = json.loads(raw)
        assert "themes" in data

    def test_load_returns_saved_data(self, tmp_db):
        prs = _fake_prs(1)
        comments = [{"body": "add tests", "path": "a.py", "user": {"login": "i"}}]
        client = _make_client(prs=prs, comments=comments)
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        miner.mine_and_save()
        loaded = PRPatternMiner.load(tmp_db)
        assert loaded is not None
        assert "themes" in loaded
        assert "testing" in loaded["themes"]

    def test_load_returns_none_when_empty(self, tmp_db):
        assert PRPatternMiner.load(tmp_db) is None

    def test_mine_and_save_returns_result(self, tmp_db):
        client = _make_client()
        miner = PRPatternMiner(tmp_db, client, "org", "repo")
        result = miner.mine_and_save()
        assert isinstance(result, dict)
        assert "themes" in result


# ---------------------------------------------------------------------------
# Context pack integration
# ---------------------------------------------------------------------------


class TestContextPackIntegration:
    def test_pack_includes_pr_patterns_when_stored(self, tmp_db):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.graph.queries import GraphQuery

        # Store PR pattern data
        tmp_db.set_config("pr_patterns", json.dumps({
            "prs_analyzed": 10,
            "total_comments": 80,
            "themes": {
                "type_hints": {"count": 15, "examples": [], "top_files": []},
                "testing": {"count": 10, "examples": [], "top_files": []},
            },
            "top_reviewers": [],
            "generated_at": "2026-01-01T00:00:00+00:00",
        }))
        tmp_db.set_config("repo_name", "testproject")
        tmp_db.commit_transaction()
        tmp_db.load_graph_to_memory()

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000)
        pack = gen.generate()

        assert pack.pr_patterns.get("prs_analyzed") == 10
        assert "type_hints" in pack.pr_patterns.get("top_themes", [])

    def test_claude_md_includes_pr_patterns(self, tmp_db):
        from codegraph.context.claude_md import ClaudeMdWriter
        from codegraph.context.pack_generator import ContextPack

        pack = ContextPack(
            repo_name="test", generated_at="2026-01-01", token_budget=8000,
            pr_patterns={"prs_analyzed": 5, "top_themes": ["type_hints", "testing"]},
        )
        md = ClaudeMdWriter(pack).render()
        assert "PR Review Patterns" in md
        assert "type hints" in md

    def test_pack_no_pr_patterns_when_not_stored(self, tmp_db):
        from codegraph.context.pack_generator import ContextPackGenerator
        from codegraph.graph.queries import GraphQuery

        tmp_db.set_config("repo_name", "testproject")
        tmp_db.commit_transaction()
        tmp_db.load_graph_to_memory()

        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=8000)
        pack = gen.generate()
        assert pack.pr_patterns == {}
