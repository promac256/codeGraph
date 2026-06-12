"""Tests for context-pack output targeting (CLAUDE.md clobber protection)."""

from __future__ import annotations

import pytest

from codegraph.cli.main import _PACK_MARKER, _generate_pack
from codegraph.config import Settings
from codegraph.graph.store import GraphStore
from codegraph.models import FileNode


@pytest.fixture
def repo(tmp_path):
    """A Settings + populated store rooted at a temp repo."""
    settings = Settings.from_repo(tmp_path)
    settings.codegraph_dir.mkdir(exist_ok=True)
    store = GraphStore(settings.db_path)
    store.open()
    store.set_config("repo_name", "demo")
    store.upsert_node(
        FileNode(node_id="file:a.py", path="a.py", lang="python", line_count=5)
    )
    store.commit_transaction()
    yield settings, store, tmp_path
    store.close()


def test_auto_preserves_hand_authored_claude_md(repo):
    settings, store, root = repo
    claude = root / "CLAUDE.md"
    claude.write_text("# My hand-written guide\n", encoding="utf-8")

    _generate_pack(store, settings, mode="auto")

    assert claude.read_text(encoding="utf-8") == "# My hand-written guide\n"
    fallback = settings.codegraph_dir / "context-pack.md"
    assert fallback.exists() and _PACK_MARKER in fallback.read_text(encoding="utf-8")


def test_auto_overwrites_its_own_generated_file(repo):
    settings, store, root = repo
    claude = root / "CLAUDE.md"
    claude.write_text(f"# old\n> {_PACK_MARKER} earlier\n", encoding="utf-8")

    _generate_pack(store, settings, mode="auto")

    assert _PACK_MARKER in claude.read_text(encoding="utf-8")
    assert "# old" not in claude.read_text(encoding="utf-8")


def test_auto_creates_claude_md_when_absent(repo):
    settings, store, root = repo
    _generate_pack(store, settings, mode="auto")
    assert _PACK_MARKER in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_force_overwrites_hand_authored(repo):
    settings, store, root = repo
    (root / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
    _generate_pack(store, settings, mode="force")
    assert _PACK_MARKER in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_off_never_touches_claude_md(repo):
    settings, store, root = repo
    (root / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
    _generate_pack(store, settings, mode="off")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# mine\n"
    assert (settings.codegraph_dir / "context-pack.md").exists()
