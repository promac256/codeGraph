"""Tests for GraphDiffer: symbol-level diff between two git refs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codegraph.graph.differ import (
    DiffResult,
    FileDiff,
    GraphDiffer,
    SymbolChange,
    _diff_classes,
    _diff_functions,
    _diff_types,
)
from codegraph.models import ClassNode, FunctionNode, TypeNode
from codegraph.parsers.registry import ParserRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fn(name: str, sig: str = "", async_: bool = False, cx: int = 1) -> FunctionNode:
    return FunctionNode(
        node_id=f"func:t::{name}",
        name=name,
        qualified_name=name,
        file="file:t.py",
        signature=sig or f"def {name}():",
        is_async=async_,
        complexity=cx,
    )


def _cls(name: str, bases: list[str] | None = None, abstract: bool = False) -> ClassNode:
    return ClassNode(
        node_id=f"class:t::{name}",
        name=name,
        file="file:t.py",
        bases=bases or [],
        is_abstract=abstract,
    )


def _tp(name: str) -> TypeNode:
    return TypeNode(node_id=f"type:t::{name}", name=name, file="file:t.py")


def _make_parse_result(functions=(), classes=(), types=()):
    from codegraph.models import FileNode
    from codegraph.parsers.base import ParseResult
    from codegraph.utils.hashing import sha256_bytes

    fn = FileNode(node_id="file:t.py", path="t.py", sha256=sha256_bytes(b""))
    r = ParseResult(file_node=fn)
    r.functions = list(functions)
    r.classes = list(classes)
    r.types = list(types)
    return r


# ---------------------------------------------------------------------------
# Unit: diff helpers
# ---------------------------------------------------------------------------


class TestDiffFunctions:
    def test_added(self):
        changes = _diff_functions([], [_fn("new_fn")])
        assert len(changes) == 1
        assert changes[0].change_type == "added"
        assert changes[0].name == "new_fn"

    def test_removed(self):
        changes = _diff_functions([_fn("old_fn")], [])
        assert len(changes) == 1
        assert changes[0].change_type == "removed"
        assert changes[0].name == "old_fn"

    def test_unchanged(self):
        fn = _fn("stable", sig="def stable():")
        changes = _diff_functions([fn], [fn])
        assert changes == []

    def test_signature_changed(self):
        changes = _diff_functions(
            [_fn("foo", sig="def foo():")],
            [_fn("foo", sig="def foo(x: int):")],
        )
        assert len(changes) == 1
        assert changes[0].change_type == "modified"
        assert "signature" in changes[0].detail

    def test_async_changed(self):
        changes = _diff_functions([_fn("bar", async_=False)], [_fn("bar", async_=True)])
        assert len(changes) == 1
        assert changes[0].change_type == "modified"
        assert "async" in changes[0].detail

    def test_large_complexity_jump(self):
        changes = _diff_functions([_fn("baz", cx=2)], [_fn("baz", cx=12)])
        assert len(changes) == 1
        assert changes[0].change_type == "modified"
        assert "complexity" in changes[0].detail

    def test_small_complexity_change_ignored(self):
        changes = _diff_functions([_fn("qux", cx=3)], [_fn("qux", cx=4)])
        assert changes == []

    def test_multiple_changes(self):
        changes = _diff_functions(
            [_fn("a"), _fn("b"), _fn("c")],
            [_fn("b", sig="def b(x):"), _fn("c"), _fn("d")],
        )
        names = {(ch.name, ch.change_type) for ch in changes}
        assert ("a", "removed") in names
        assert ("b", "modified") in names
        assert ("d", "added") in names


class TestDiffClasses:
    def test_added(self):
        changes = _diff_classes([], [_cls("Dog")])
        assert len(changes) == 1
        assert changes[0].change_type == "added"

    def test_removed(self):
        changes = _diff_classes([_cls("Cat")], [])
        assert len(changes) == 1
        assert changes[0].change_type == "removed"

    def test_bases_changed(self):
        changes = _diff_classes([_cls("Dog", bases=[])], [_cls("Dog", bases=["Animal"])])
        assert len(changes) == 1
        assert "inheritance" in changes[0].detail

    def test_abstract_changed(self):
        changes = _diff_classes([_cls("Abc", abstract=False)], [_cls("Abc", abstract=True)])
        assert len(changes) == 1
        assert "abstract" in changes[0].detail

    def test_unchanged(self):
        cn = _cls("Cat", bases=["Animal"])
        changes = _diff_classes([cn], [cn])
        assert changes == []


class TestDiffTypes:
    def test_added(self):
        changes = _diff_types([], [_tp("UserId")])
        assert len(changes) == 1
        assert changes[0].change_type == "added"

    def test_removed(self):
        changes = _diff_types([_tp("OldId")], [])
        assert len(changes) == 1
        assert changes[0].change_type == "removed"

    def test_unchanged(self):
        changes = _diff_types([_tp("X")], [_tp("X")])
        assert changes == []


# ---------------------------------------------------------------------------
# Integration: GraphDiffer.diff() with mocked git and parser
# ---------------------------------------------------------------------------


class TestGraphDiffer:
    @pytest.fixture
    def registry(self):
        reg = MagicMock(spec=ParserRegistry)
        parser = MagicMock()
        reg.get_parser.return_value = parser
        return reg, parser

    def _make_differ(self, registry, store=None):
        return GraphDiffer(Path("/repo"), registry, store=store)

    def _mock_local_repo(self, changed_files, file_contents):
        """Helper that patches LocalRepo inside the differ module."""
        repo_mock = MagicMock()
        repo_mock.get_changed_files_between.return_value = changed_files

        def _show(sha, path):
            return file_contents.get((sha, path))

        repo_mock.get_file_at_sha.side_effect = _show
        return repo_mock

    def test_added_file(self):
        reg, parser = MagicMock(spec=ParserRegistry), MagicMock()
        MagicMock(spec=ParserRegistry).get_parser = MagicMock(return_value=parser)
        differ = GraphDiffer(Path("/repo"), reg, store=None)

        after_result = _make_parse_result(functions=[_fn("new_fn")])
        parser.parse.return_value = after_result
        reg.get_parser.return_value = parser

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "t.py", "status": "A"}]
            repo_inst.get_file_at_sha.return_value = b"def new_fn(): pass"

            result = differ.diff("sha1", "sha2")

        assert len(result.file_diffs) == 1
        assert result.file_diffs[0].status == "A"
        changes = result.file_diffs[0].changes
        assert any(c.change_type == "added" and c.name == "new_fn" for c in changes)

    def test_deleted_file(self):
        reg, parser = MagicMock(spec=ParserRegistry), MagicMock()
        differ = GraphDiffer(Path("/repo"), reg, store=None)

        before_result = _make_parse_result(functions=[_fn("gone")])
        parser.parse.return_value = before_result
        reg.get_parser.return_value = parser

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "t.py", "status": "D"}]
            repo_inst.get_file_at_sha.return_value = b"def gone(): pass"

            result = differ.diff("sha1", "sha2")

        assert any(c.change_type == "removed" for c in result.all_changes)

    def test_modified_file_symbol_diff(self):
        reg, parser = MagicMock(spec=ParserRegistry), MagicMock()
        differ = GraphDiffer(Path("/repo"), reg, store=None)
        reg.get_parser.return_value = parser

        before = _make_parse_result(functions=[_fn("foo", sig="def foo():")])
        after = _make_parse_result(functions=[_fn("foo", sig="def foo(x: int):")])

        call_count = [0]
        def side_effect(path, content, root):
            call_count[0] += 1
            return before if call_count[0] == 1 else after

        parser.parse.side_effect = side_effect

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "t.py", "status": "M"}]
            repo_inst.get_file_at_sha.return_value = b"content"

            result = differ.diff("sha1", "sha2")

        modified = [c for c in result.all_changes if c.change_type == "modified"]
        assert len(modified) == 1
        assert modified[0].name == "foo"

    def test_unparseable_file_skipped(self):
        reg = MagicMock(spec=ParserRegistry)
        reg.get_parser.return_value = None
        differ = GraphDiffer(Path("/repo"), reg, store=None)

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "README.md", "status": "M"}]

            result = differ.diff("sha1", "sha2")

        assert result.file_diffs == []

    def test_summary_counts(self):
        result = DiffResult(sha1="a", sha2="b")
        result.file_diffs = [
            FileDiff(path="a.py", status="M", changes=[
                SymbolChange("function", "foo", "foo", "a.py", "added"),
                SymbolChange("function", "bar", "bar", "a.py", "removed"),
                SymbolChange("function", "baz", "baz", "a.py", "modified", "signature changed"),
            ])
        ]
        s = result.summary
        assert s["added"] == 1
        assert s["removed"] == 1
        assert s["modified"] == 1
        assert s["files"] == 1

    def test_no_changes_empty_result(self):
        reg, parser = MagicMock(spec=ParserRegistry), MagicMock()
        differ = GraphDiffer(Path("/repo"), reg, store=None)
        reg.get_parser.return_value = parser

        same = _make_parse_result(functions=[_fn("stable")])
        parser.parse.return_value = same

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "t.py", "status": "M"}]
            repo_inst.get_file_at_sha.return_value = b"content"

            result = differ.diff("sha1", "sha2")

        assert result.file_diffs == []  # no changes → no file diff entry

    def test_blast_radius_populated(self, tmp_db):
        from codegraph.models import GraphEdge, EdgeKind

        # Set up a graph with a CALLS edge: caller → foo
        tmp_db.load_graph_to_memory()
        foo = _fn("foo")
        foo_node_id = foo.node_id
        tmp_db.upsert_node(foo)
        caller = _fn("caller_fn")
        tmp_db.upsert_node(caller)
        tmp_db.upsert_edge(GraphEdge(
            src=caller.node_id, dst=foo_node_id,
            kind=EdgeKind.CALLS, meta={},
        ))
        tmp_db.commit_transaction()

        reg, parser = MagicMock(spec=ParserRegistry), MagicMock()
        differ = GraphDiffer(Path("/repo"), reg, store=tmp_db)
        reg.get_parser.return_value = parser

        before = _make_parse_result(functions=[_fn("foo", sig="def foo():")])
        after = _make_parse_result(functions=[_fn("foo", sig="def foo(x):")])

        call_count = [0]
        def side_effect(path, content, root):
            call_count[0] += 1
            return before if call_count[0] == 1 else after

        parser.parse.side_effect = side_effect

        with patch("codegraph.graph.differ.LocalRepo") as MockRepo:
            repo_inst = MockRepo.return_value
            repo_inst.get_changed_files_between.return_value = [{"path": "t.py", "status": "M"}]
            repo_inst.get_file_at_sha.return_value = b"content"

            result = differ.diff("sha1", "sha2")

        assert "foo" in result.blast_radius
        assert "caller_fn" in result.blast_radius["foo"]
