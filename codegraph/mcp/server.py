"""FastMCP server — exposes the knowledge graph as Claude Code MCP tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP(
    name="codeGraph",
    instructions=(
        "codeGraph is a knowledge graph for this software repository. "
        "Use these tools to answer questions about the codebase without reading files directly. "
        "Always prefer graph queries over file reads for navigation and structure questions."
    ),
)

_graph_query = None
_graph_store = None
_settings = None


def _resolve_symbol_id(q, name_or_id: str) -> str:
    """Accept either a bare symbol name or an already-qualified node_id."""
    # If it looks like a node_id (contains ':') treat it as-is
    if ":" in name_or_id:
        return name_or_id
    results = q.find_definition(name_or_id, "any")
    if results:
        return results[0].node_id
    return name_or_id


_settings = None


def _get_query():
    global _graph_query, _graph_store, _settings  # noqa: PLW0603
    if _graph_query is not None:
        return _graph_query

    from codegraph.config import Settings
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore

    repo_path = Path(os.environ.get("CODEGRAPH_REPO_PATH", ".")).resolve()
    _settings = Settings.from_repo(repo_path)

    _graph_store = GraphStore(_settings.db_path)
    _graph_store.open()
    _graph_store.load_graph_to_memory()
    _graph_query = GraphQuery(_graph_store)
    return _graph_query


def _get_notes_manager():
    from codegraph.config import Settings
    from codegraph.context.session_notes import SessionNotesManager

    if _settings is not None:
        return SessionNotesManager(_settings.session_notes_path)
    repo_path = Path(os.environ.get("CODEGRAPH_REPO_PATH", ".")).resolve()
    settings = Settings.from_repo(repo_path)
    return SessionNotesManager(settings.session_notes_path)


@mcp.tool()
def codegraph_find_symbol(name: str, kind: str = "any") -> dict:
    """
    Find where a symbol (function, class, type, file) is defined.

    Args:
        name: Symbol name to search (exact or partial match).
        kind: One of 'function', 'class', 'type', 'file', or 'any'.
    """
    q = _get_query()
    results = q.find_definition(name, kind)
    return {
        "query": name,
        "kind": kind,
        "matches": [
            {
                "node_id": r.node_id,
                "kind": r.kind,
                "name": r.name,
                "file": r.file.replace("file:", ""),
                "line": r.line_start,
                "signature": r.signature,
                "docstring": (r.docstring or "")[:200],
            }
            for r in results
        ],
        "count": len(results),
    }


@mcp.tool()
def codegraph_find_callers(symbol_name: str, depth: int = 1) -> dict:
    """
    Find all callers of a function.

    Args:
        symbol_name: Symbol name or node_id. If a name, the top match is used.
        depth: Call depth to traverse (1=direct callers, 2=callers of callers).
    """
    q = _get_query()
    symbol_id = _resolve_symbol_id(q, symbol_name)
    callers = q.get_callers(symbol_id, depth)
    return {"symbol": symbol_id, "depth": depth, "callers": callers, "count": len(callers)}


@mcp.tool()
def codegraph_get_dependencies(file_path: str, depth: int = 2) -> dict:
    """
    Get the import dependency graph for a file.

    Args:
        file_path: Repo-relative file path (e.g. 'src/api/routes.py').
        depth: Transitive dependency depth (1=direct only).
    """
    q = _get_query()
    return q.get_dependencies(f"file:{file_path}", depth)


@mcp.tool()
def codegraph_recent_changes(limit: int = 10) -> dict:
    """
    Get recent commits and their impact on the graph.

    Args:
        limit: Number of recent commits to return.
    """
    q = _get_query()
    return {"changes": q.get_recent_changes(limit)}


@mcp.tool()
def codegraph_hot_paths(top_n: int = 20) -> dict:
    """
    Return the most frequently modified and most-called files/functions.
    Useful for identifying core architectural components.

    Args:
        top_n: Number of results to return.
    """
    q = _get_query()
    return {"hot_paths": q.get_hot_paths(top_n)}


@mcp.tool()
def codegraph_test_coverage(symbol_name: str) -> dict:
    """
    Find what tests cover a specific function or class.

    Args:
        symbol_name: Symbol name or node_id.
    """
    q = _get_query()
    symbol_id = _resolve_symbol_id(q, symbol_name)
    return q.get_test_coverage(symbol_id)


@mcp.tool()
def codegraph_public_api(file_path: str | None = None) -> dict:
    """
    Get the public API surface — exported non-private symbols.

    Args:
        file_path: Optional file to limit results to. If None, returns repo-wide API.
    """
    q = _get_query()
    file_id = f"file:{file_path}" if file_path else None
    api = q.get_public_api(file_id)
    return {"api": api[:50], "count": len(api)}


@mcp.tool()
def codegraph_todos(kind: str = "all", limit: int = 50) -> dict:
    """
    Get all TODO/FIXME/HACK/BUG/NOTE comments in the codebase.

    Args:
        kind: Filter type — 'TODO', 'FIXME', 'HACK', 'BUG', 'NOTE', or 'all'.
        limit: Maximum number to return.
    """
    q = _get_query()
    k = None if kind == "all" else kind
    return {"todos": q.get_todos(k, limit)}


@mcp.tool()
def codegraph_search(query: str, limit: int = 20) -> dict:
    """
    Full-text search over symbol names and docstrings.

    Args:
        query: Search query (supports FTS5 boolean: AND, OR, NOT).
        limit: Maximum results.
    """
    q = _get_query()
    results = q.store.fts_search(query, limit)
    return {"query": query, "results": results[:limit], "count": len(results)}


@mcp.tool()
def codegraph_architectural_layers() -> dict:
    """
    Get the detected architectural layers and which files belong to each.
    Layers: presentation, business, data, infrastructure, config, utility, test, unknown.
    """
    q = _get_query()
    layers = q.get_architectural_layers()
    return {
        "layers": {
            k: list(v) for k, v in layers.items()
        }
    }


@mcp.tool()
def codegraph_impact_analysis(symbol_name: str, max_depth: int = 3) -> dict:
    """
    Analyze the blast radius of changing a symbol.
    Returns all code that would be affected by modifying this function/class.

    Args:
        symbol_name: Symbol name or node_id. If a name, the top match is used.
        max_depth: How many hops to traverse (default 3).
    """
    q = _get_query()
    symbol_id = _resolve_symbol_id(q, symbol_name)
    return q.impact_analysis(symbol_id, max_depth)


@mcp.tool()
def codegraph_conventions() -> dict:
    """
    Get detected code conventions, naming patterns, and code idioms in the codebase.

    Returns: naming styles (snake_case vs camelCase), documentation coverage,
    async/decorator patterns, complexity stats, top imports, test ratios, and
    language breakdown. Run `codegraph init` or `codegraph update` to refresh.
    """
    q = _get_query()
    from codegraph.enrichment.convention_miner import ConventionMiner
    stored = ConventionMiner.load(q.store)
    if stored:
        return stored
    # Fallback: compute on-the-fly if init hasn't been run yet
    return ConventionMiner(q.store).mine()


@mcp.tool()
def codegraph_overview() -> dict:
    """
    Get a full repository overview: stats, languages, layers, hot paths.
    Use this at session start for quick orientation.
    """
    q = _get_query()
    return {
        "overview": q.get_overview(),
        "layers": {k: len(v) for k, v in q.get_architectural_layers().items()},
        "hot_paths": q.get_hot_paths(top_n=10),
        "todo_summary": {
            "total": len(q.get_todos(limit=1000)),
        },
    }


@mcp.resource("graph://context-pack")
def get_context_pack() -> str:
    """The compressed CLAUDE.md context pack as markdown — load at session start."""
    q = _get_query()
    from codegraph.context.pack_generator import ContextPackGenerator

    gen = ContextPackGenerator(q.store, q)
    pack = gen.generate()
    return gen.to_markdown(pack)


@mcp.resource("graph://summary")
def get_summary() -> str:
    """One-paragraph codebase orientation."""
    q = _get_query()
    ov = q.get_overview()
    return (
        f"Repository with {ov['files']} files, {ov['functions']} functions, "
        f"{ov['classes']} classes. "
        f"Languages: {', '.join(f'{k}({v})' for k, v in ov.get('languages', {}).items())}. "
        f"Use codegraph_find_symbol to locate definitions, "
        f"codegraph_hot_paths for core components, "
        f"codegraph_architectural_layers for structure overview."
    )


@mcp.tool()
def codegraph_get_session_notes(max_notes: int = 10) -> dict:
    """
    Read accumulated architectural session notes for this repository.

    Session notes persist across coding sessions and record discoveries,
    conventions, warnings, and architectural decisions. They are also
    embedded in CLAUDE.md so new sessions inherit prior knowledge.

    Args:
        max_notes: Maximum number of recent notes to return (default 10).
    """
    mgr = _get_notes_manager()
    notes = mgr.read_recent(max_notes=max_notes)
    return {
        "notes": notes,
        "total": mgr.note_count(),
        "tip": "Add notes via `codegraph notes --add` CLI or codegraph_add_session_note tool.",
    }


@mcp.tool()
def codegraph_add_session_note(note: str, category: str = "general") -> dict:
    """
    Append an architectural discovery or note for this repository.

    Notes are stored in .codegraph/session_notes.md and included in
    future CLAUDE.md context packs so every new session inherits them.

    Args:
        note:     The note text (markdown supported).
        category: One of: general, architecture, convention, warning, decision.
    """
    mgr = _get_notes_manager()
    mgr.append(note, category=category)
    return {"saved": True, "total_notes": mgr.note_count()}


def run():
    mcp.run()


if __name__ == "__main__":
    run()
