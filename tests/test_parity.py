"""Golden parity tests — the Python backend's contract with the native backend.

Both backends index tests/fixtures and must satisfy the invariants in
tests/parity_golden.json. The Node side of the same contract is
codegraph-vscode/scripts/parity-test.ts (run with `npm run parity`).

If a parser change breaks one of these, either fix the divergence or update
the golden file deliberately (and re-run BOTH sides) — never let the two
backends drift silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codegraph.graph.builder import GraphBuilder
from codegraph.graph.store import GraphStore
from codegraph.parsers.registry import ParserRegistry

GOLDEN = json.loads(
    (Path(__file__).parent / "parity_golden.json").read_text(encoding="utf-8")
)
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fixture_graph(tmp_path_factory):
    store = GraphStore(tmp_path_factory.mktemp("parity") / "g.db")
    store.open()
    builder = GraphBuilder(store, ParserRegistry.default(), FIXTURES.resolve(), max_workers=2)
    builder.build()
    yield store
    store.close()


def test_file_count_matches_golden(fixture_graph):
    files = [
        n for n, d in fixture_graph.graph.nodes(data=True) if d.get("kind") == "file"
    ]
    assert len(files) == GOLDEN["file_count"]


def test_python_models_functions_match_golden(fixture_graph):
    fns = sorted(
        d.get("qualified_name") or d.get("name")
        for _, d in fixture_graph.graph.nodes(data=True)
        if d.get("kind") == "function" and d.get("file") == "file:python_sample/models.py"
    )
    assert fns == GOLDEN["python_models_functions"]


def test_animal_class_found_in_every_language(fixture_graph):
    files = sorted(
        {
            d.get("file", "").removeprefix("file:")
            for _, d in fixture_graph.graph.nodes(data=True)
            if d.get("kind") == "class" and d.get("name") == "Animal"
        }
    )
    assert files == GOLDEN["animal_class_files"]
