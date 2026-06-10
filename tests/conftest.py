"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from codegraph.graph.store import GraphStore


@pytest.fixture
def tmp_db(tmp_path) -> GraphStore:
    """An open GraphStore backed by a temp SQLite database."""
    store = GraphStore(tmp_path / "test.db")
    store.open()
    yield store
    store.close()


@pytest.fixture
def python_sample_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "python_sample"


@pytest.fixture
def typescript_sample_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "typescript_sample"
