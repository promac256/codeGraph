"""CLI entry point — all codegraph commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="codegraph",
    help="Knowledge graph for LLM coding assistants.",
    add_completion=True,
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    workers: int = typer.Option(8, "--workers", "-w", help="Parallel parser workers"),
    llm_enrich: bool = typer.Option(
        False, "--llm-enrich/--no-llm-enrich", help="Use Anthropic API for docstring summaries"
    ),
    token_budget: int = typer.Option(8000, "--token-budget", help="Context pack token budget"),
):
    """Initialize and build the complete knowledge graph for a repository."""
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from codegraph.config import Settings
    from codegraph.enrichment.layer_detector import LayerDetector
    from codegraph.graph.builder import GraphBuilder
    from codegraph.graph.store import GraphStore
    from codegraph.parsers.registry import ParserRegistry

    settings = Settings.from_repo(repo)
    settings.codegraph_dir.mkdir(exist_ok=True)

    console.print(f"[bold]codeGraph[/bold] — indexing [cyan]{repo.resolve()}[/cyan]")

    store = GraphStore(settings.db_path)
    store.open()
    store.clear_all()
    store.set_config("repo_name", repo.resolve().name)

    registry = ParserRegistry.default()
    builder = GraphBuilder(store, registry, repo.resolve(), max_workers=workers)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        stats = builder.build(progress=progress)

    # Annotate architectural layers
    LayerDetector().annotate_store(store)

    console.print(f"\n[green]Graph built successfully:[/green]")
    console.print(f"  Files parsed:  {stats['files_parsed']}")
    console.print(f"  Files skipped: {stats['files_skipped']}")
    console.print(f"  Nodes:         {stats['nodes']}")
    console.print(f"  Edges:         {stats['edges']}")
    if stats["errors"]:
        console.print(f"  [yellow]Errors:        {stats['errors']}[/yellow]")

    # Generate context pack
    _generate_pack(store, settings, token_budget=token_budget)
    store.close()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@app.command()
def update(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    since: Optional[str] = typer.Option(None, "--since", help="SHA to update from"),
):
    """Incrementally update the graph from recent commits."""
    from codegraph.config import Settings
    from codegraph.enrichment.layer_detector import LayerDetector
    from codegraph.graph.builder import GraphBuilder
    from codegraph.graph.store import GraphStore
    from codegraph.graph.updater import GraphUpdater
    from codegraph.parsers.registry import ParserRegistry

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()

    registry = ParserRegistry.default()
    builder = GraphBuilder(store, registry, repo.resolve())
    updater = GraphUpdater(store, builder, repo.resolve(), registry)

    console.print(f"[bold]Updating graph[/bold] from [cyan]{since or 'last indexed commit'}[/cyan]...")
    stats = updater.update_from_commits(since_sha=since)

    LayerDetector().annotate_store(store)

    console.print(f"[green]Update complete:[/green]")
    console.print(f"  Commits processed: {stats['commits_processed']}")
    console.print(f"  Files updated:     {stats['files_updated']}")
    console.print(f"  Files deleted:     {stats['files_deleted']}")

    _generate_pack(store, settings)
    store.close()


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@app.command()
def query(
    symbol: str = typer.Argument(help="Symbol name to look up"),
    kind: str = typer.Option("any", "--kind", "-k", help="function|class|type|file|any"),
    repo: Path = typer.Option(Path("."), "--repo", help="Repo path"),
    callers: bool = typer.Option(False, "--callers", help="Also show callers"),
):
    """Look up where a symbol is defined."""
    from codegraph.config import Settings
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()
    q = GraphQuery(store)

    results = q.find_definition(symbol, kind)

    if not results:
        console.print(f"[yellow]No results for '{symbol}'[/yellow]")
        store.close()
        return

    table = Table(title=f"Results for '{symbol}'", show_header=True)
    table.add_column("Kind", style="cyan", width=10)
    table.add_column("Name", style="bold")
    table.add_column("File")
    table.add_column("Line", justify="right", width=6)
    table.add_column("Signature")

    for r in results:
        table.add_row(
            r.kind,
            r.name,
            r.file.replace("file:", ""),
            str(r.line_start),
            (r.signature or "")[:60],
        )
    console.print(table)

    if callers and results:
        caller_list = q.get_callers(results[0].node_id, depth=1)
        if caller_list:
            console.print(f"\n[bold]Callers of '{results[0].name}':[/bold]")
            for c in caller_list[:10]:
                console.print(
                    f"  [cyan]{c.get('name','')}[/cyan] "
                    f"in {c.get('file','').replace('file:','')}:{c.get('line_start','')}"
                )

    store.close()


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------


@app.command()
def pack(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
    token_budget: int = typer.Option(8000, "--token-budget"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output path"),
    format: str = typer.Option("markdown", "--format", "-f", help="markdown|json|html|all"),
):
    """Generate a compressed context pack for LLM session start."""
    from codegraph.config import Settings
    from codegraph.context.pack_generator import ContextPackGenerator
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()
    q = GraphQuery(store)
    gen = ContextPackGenerator(store, q, token_budget)
    cp = gen.generate()

    fmt = format.lower()
    if fmt in ("markdown", "all"):
        out = output or (repo / "CLAUDE.md")
        out.write_text(gen.to_markdown(cp))
        console.print(f"[green]CLAUDE.md[/green] written to {out}")
    if fmt in ("json", "all"):
        out = output or (repo / ".codegraph" / "context_pack.json")
        out.write_bytes(gen.to_json(cp))
        console.print(f"[green]context_pack.json[/green] written to {out}")
    if fmt in ("html", "all"):
        out = output or (repo / "codegraph-report.html")
        out.write_text(gen.to_html(cp))
        console.print(f"[green]codegraph-report.html[/green] written to {out}")

    store.close()


# ---------------------------------------------------------------------------
# report  (HTML shorthand)
# ---------------------------------------------------------------------------


@app.command()
def report(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
    token_budget: int = typer.Option(8000),
    open_browser: bool = typer.Option(False, "--open", help="Open in browser after generation"),
):
    """Generate an interactive HTML report of the knowledge graph."""
    from codegraph.config import Settings
    from codegraph.context.pack_generator import ContextPackGenerator
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()
    q = GraphQuery(store)
    gen = ContextPackGenerator(store, q, token_budget)
    cp = gen.generate()

    out = repo / "codegraph-report.html"
    out.write_text(gen.to_html(cp))
    console.print(f"[green]Report written to[/green] {out}")

    if open_browser:
        import webbrowser
        webbrowser.open(str(out.resolve()))

    store.close()


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@app.command()
def serve(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="stdio|sse"),
    port: int = typer.Option(8765, "--port", help="Port for SSE transport"),
):
    """Start the MCP server for Claude Code integration."""
    import os

    from codegraph.mcp.server import mcp

    os.environ["CODEGRAPH_REPO_PATH"] = str(repo.resolve())
    console.print(
        f"[bold]codeGraph MCP server[/bold] starting ({transport}) "
        f"for [cyan]{repo.resolve()}[/cyan]"
    )
    if transport == "sse":
        mcp.run(transport="sse", port=port)
    else:
        mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
    top: int = typer.Option(15, "--top", help="Top N hot paths to show"),
):
    """Show repository stats and hot path heatmap."""
    from codegraph.config import Settings
    from codegraph.graph.queries import GraphQuery
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()
    q = GraphQuery(store)

    ov = q.get_overview()
    console.print("\n[bold]Repository Overview[/bold]")
    console.print(f"  Files:     {ov['files']}")
    console.print(f"  Functions: {ov['functions']}")
    console.print(f"  Classes:   {ov['classes']}")
    console.print(f"  Test files:{ov['test_files']}")
    console.print(f"  Languages: {', '.join(f'{k}({v})' for k, v in ov['languages'].items())}")
    console.print(f"  Edges:     {ov['edge_count']}")

    hot = q.get_hot_paths(top_n=top)
    if hot:
        table = Table(title="Hot Paths", show_header=True)
        table.add_column("#", width=4)
        table.add_column("Name", style="bold")
        table.add_column("Kind", width=10)
        table.add_column("Complexity", justify="right", width=10)
        table.add_column("Commits", justify="right", width=8)
        for i, h in enumerate(hot, 1):
            table.add_row(
                str(i),
                h["name"],
                h["kind"],
                str(h["complexity"]),
                str(h["commit_count"]),
            )
        console.print(table)

    todos = q.get_todos(limit=1)
    total_todos = store._db.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
    console.print(f"\n  Open TODOs: {total_todos}")
    store.close()


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


@app.command()
def watch(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
):
    """Watch for git changes and auto-update the graph."""
    import subprocess
    import time

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        console.print("[red]watchdog not installed. Run: pip install watchdog[/red]")
        raise typer.Exit(1)

    class GitChangeHandler(FileSystemEventHandler):
        def on_modified(self, event):
            src = event.src_path
            if ".git/refs" in src or ".git/COMMIT_EDITMSG" in src:
                console.print("[yellow]Git change detected — updating graph...[/yellow]")
                subprocess.run(
                    [sys.executable, "-m", "codegraph.cli.main", "update", str(repo)],
                    check=False,
                )

    observer = Observer()
    git_dir = str(repo / ".git")
    observer.schedule(GitChangeHandler(), git_dir, recursive=True)
    observer.start()
    console.print(f"[bold]Watching[/bold] [cyan]{repo}[/cyan] for git changes (Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_pack(store, settings, token_budget: int = 8000) -> None:
    from codegraph.context.pack_generator import ContextPackGenerator
    from codegraph.graph.queries import GraphQuery

    q = GraphQuery(store)
    gen = ContextPackGenerator(store, q, token_budget)
    cp = gen.generate()

    claude_md = settings.repo_path / "CLAUDE.md"
    claude_md.write_text(gen.to_markdown(cp))
    console.print(f"[green]CLAUDE.md[/green] updated at {claude_md}")


if __name__ == "__main__":
    app()
