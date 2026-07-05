"""Pydantic models for all graph nodes and edges."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeKind(StrEnum):
    REPO = "repo"
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    TYPE = "type"
    TEST = "test"
    COMMIT = "commit"
    NOTE = "note"


class EdgeKind(StrEnum):
    IMPORTS = "imports"
    DEFINES = "defines"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    TESTS = "tests"
    MODIFIES = "modifies"
    EXPORTS = "exports"
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    RESOLVES_TO = "resolves_to"
    ANNOTATES = "annotates"


class BaseNode(BaseModel):
    node_id: str
    kind: NodeKind
    meta: dict[str, Any] = Field(default_factory=dict)


class RepoNode(BaseNode):
    kind: NodeKind = NodeKind.REPO
    name: str
    remote_url: str | None = None
    default_branch: str = "main"
    languages: list[str] = Field(default_factory=list)
    total_files: int = 0


class FileNode(BaseNode):
    kind: NodeKind = NodeKind.FILE
    path: str
    lang: str = "unknown"
    size_bytes: int = 0
    sha256: str = ""
    line_count: int = 0
    last_commit: str = ""
    commit_count: int = 0
    is_test: bool = False
    layer: str | None = None


class ModuleNode(BaseNode):
    kind: NodeKind = NodeKind.MODULE
    name: str
    file: str
    exports: list[str] = Field(default_factory=list)


class ClassNode(BaseNode):
    kind: NodeKind = NodeKind.CLASS
    name: str
    file: str
    line_start: int = 0
    line_end: int = 0
    bases: list[str] = Field(default_factory=list)
    docstring: str | None = None
    llm_summary: str | None = None
    is_abstract: bool = False
    is_dataclass: bool = False
    is_exported: bool = True


class FunctionNode(BaseNode):
    kind: NodeKind = NodeKind.FUNCTION
    name: str
    qualified_name: str
    file: str
    line_start: int = 0
    line_end: int = 0
    signature: str = ""
    docstring: str | None = None
    llm_summary: str | None = None
    is_async: bool = False
    is_property: bool = False
    is_classmethod: bool = False
    is_staticmethod: bool = False
    is_exported: bool = True
    complexity: int = 1
    pagerank: float = 0.0


class TypeNode(BaseNode):
    kind: NodeKind = NodeKind.TYPE
    name: str
    file: str
    line_start: int = 0
    definition: str = ""
    is_exported: bool = False
    docstring: str | None = None


class TestNode(BaseNode):
    kind: NodeKind = NodeKind.TEST
    name: str
    file: str
    line_start: int = 0
    framework: str = "unknown"
    covers: list[str] = Field(default_factory=list)


class CommitNode(BaseNode):
    kind: NodeKind = NodeKind.COMMIT
    sha: str
    short_sha: str
    author: str = ""
    author_email: str = ""
    timestamp: int = 0
    message: str = ""
    files_changed: list[str] = Field(default_factory=list)
    insertions: int = 0
    deletions: int = 0


class NoteNode(BaseNode):
    """A session note promoted to a graph node.

    Notes annotate code symbols via ANNOTATES edges, carry provenance
    (source + created_at), and remain backed by the append-only raw
    layer in .codegraph/session_notes.md.
    """

    kind: NodeKind = NodeKind.NOTE
    name: str
    text: str
    category: str = "general"
    source: str = "manual"
    created_at: str = ""
    refs: list[str] = Field(default_factory=list)
    unresolved_refs: list[str] = Field(default_factory=list)
    docstring: str | None = None  # mirrors text so notes are FTS-searchable


class GraphEdge(BaseModel):
    src: str
    dst: str
    kind: EdgeKind
    meta: dict[str, Any] = Field(default_factory=dict)
