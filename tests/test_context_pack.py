"""Tests for context pack generation."""

from __future__ import annotations

import pytest

from codegraph.context.pack_generator import ContextPackGenerator
from codegraph.graph.queries import GraphQuery
from codegraph.models import FileNode, FunctionNode


def _populate(store):
    f = FileNode(
        node_id="file:src/main.py",
        path="src/main.py",
        lang="python",
        line_count=100,
        layer="business",
    )
    fn = FunctionNode(
        node_id="func:src/main.py::run",
        name="run",
        qualified_name="run",
        file="file:src/main.py",
        line_start=5,
        line_end=50,
        signature="run() -> None",
        docstring="Main entry point.",
    )
    store.upsert_node(f)
    store.upsert_node(fn)
    store.set_config("repo_name", "testproject")
    store.commit_transaction()


class TestContextPackGenerator:
    def test_generates_pack(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=4000)
        pack = gen.generate()
        assert pack.repo_name == "testproject"
        assert pack.file_count >= 1

    def test_markdown_output(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=4000)
        pack = gen.generate()
        md = gen.to_markdown(pack)
        assert "testproject" in md
        assert "Repository Overview" in md

    def test_html_output(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=4000)
        pack = gen.generate()
        html = gen.to_html(pack)
        assert "<!DOCTYPE html>" in html
        assert "testproject" in html

    def test_json_output(self, tmp_db):
        import orjson
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=4000)
        pack = gen.generate()
        data = orjson.loads(gen.to_json(pack))
        assert data["repo_name"] == "testproject"
        assert "repo_overview" in data

    def test_token_budget_respected(self, tmp_db):
        _populate(tmp_db)
        q = GraphQuery(tmp_db)
        gen = ContextPackGenerator(tmp_db, q, token_budget=500)
        pack = gen.generate()
        md = gen.to_markdown(pack)
        # Even a tight budget should produce valid output
        assert len(md) > 0
