"""CLI entry point — all codegraph commands."""

from __future__ import annotations

import logging
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


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


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
    no_claude_md: bool = typer.Option(
        False, "--no-claude-md",
        help="Write the context pack to .codegraph/ instead of CLAUDE.md",
    ),
    force_claude_md: bool = typer.Option(
        False, "--force-claude-md",
        help="Overwrite CLAUDE.md even if it was hand-authored",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-file parse warnings and debug detail"
    ),
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
    from codegraph.utils.lockfile import LockHeldError, repo_lock

    _setup_logging(verbose)
    settings = Settings.from_repo(repo)
    settings.codegraph_dir.mkdir(exist_ok=True)

    console.print(f"[bold]codeGraph[/bold] — indexing [cyan]{repo.resolve()}[/cyan]")

    try:
        with repo_lock(settings.codegraph_dir):
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

            # Mine conventions
            from codegraph.enrichment.convention_miner import ConventionMiner
            ConventionMiner(store).mine_and_save()

            console.print(f"\n[green]Graph built successfully:[/green]")
            console.print(f"  Files parsed:  {stats['files_parsed']}")
            console.print(f"  Files skipped: {stats['files_skipped']}")
            console.print(f"  Nodes:         {stats['nodes']}")
            console.print(f"  Edges:         {stats['edges']}")
            console.print(f"  Commits:       {stats.get('commits', 0)}")
            if stats["errors"]:
                console.print(
                    f"  [yellow]Errors:        {stats['errors']}[/yellow]"
                    + ("" if verbose else "  (re-run with --verbose for detail)")
                )

            if llm_enrich:
                _run_enrichment(store, settings, progress_style="bar")

            # Generate context pack
            pack_mode = "off" if no_claude_md else ("force" if force_claude_md else "auto")
            _generate_pack(store, settings, token_budget=token_budget, mode=pack_mode)
            store.close()
    except LockHeldError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@app.command()
def update(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    since: Optional[str] = typer.Option(None, "--since", help="SHA to update from"),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show per-file update warnings and debug detail"
    ),
):
    """Incrementally update the graph from recent commits."""
    from codegraph.config import Settings
    from codegraph.enrichment.layer_detector import LayerDetector
    from codegraph.graph.builder import GraphBuilder
    from codegraph.graph.store import GraphStore
    from codegraph.graph.updater import GraphUpdater
    from codegraph.parsers.registry import ParserRegistry
    from codegraph.utils.lockfile import LockHeldError, repo_lock

    _setup_logging(verbose)
    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    try:
        with repo_lock(settings.codegraph_dir):
            store = GraphStore(settings.db_path)
            store.open()
            store.load_graph_to_memory()

            registry = ParserRegistry.default()
            builder = GraphBuilder(store, registry, repo.resolve())
            updater = GraphUpdater(store, builder, repo.resolve(), registry)

            console.print(f"[bold]Updating graph[/bold] from [cyan]{since or 'last indexed commit'}[/cyan]...")
            stats = updater.update_from_commits(since_sha=since)

            LayerDetector().annotate_store(store)
            from codegraph.enrichment.convention_miner import ConventionMiner
            ConventionMiner(store).mine_and_save()

            console.print(f"[green]Update complete:[/green]")
            console.print(f"  Commits processed: {stats['commits_processed']}")
            console.print(f"  Files updated:     {stats['files_updated']}")
            console.print(f"  Files deleted:     {stats['files_deleted']}")

            _generate_pack(store, settings)
            store.close()
    except LockHeldError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    fix: bool = typer.Option(False, "--fix", help="Repair what can be repaired (orphan edges, FTS index)"),
):
    """Validate graph health: DB integrity, FTS index, orphan edges, staleness.

    Read-only by default; --fix removes orphan edges and rebuilds the FTS
    index. Previously the only recourse for a corrupt graph was deleting
    .codegraph and rebuilding from scratch.
    """
    from codegraph.config import Settings
    from codegraph.git.local_repo import LocalRepo
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    problems = 0

    # 1. SQLite integrity
    result = store.integrity_check()
    if result == "ok":
        console.print("[green]OK[/green] SQLite integrity: ok")
    else:
        problems += 1
        console.print(f"[red]FAIL[/red] SQLite integrity: {result}")
        console.print("  [dim]The database file is damaged — rebuild with `codegraph init`.[/dim]")

    # 2. FTS index
    fts_err = store.fts_probe()
    if fts_err is None:
        console.print("[green]OK[/green] FTS index: ok")
    else:
        problems += 1
        console.print(f"[red]FAIL[/red] FTS index: {fts_err}")
        if fix:
            n = store.rebuild_fts()
            console.print(f"  [green]fixed[/green]: rebuilt FTS from {n} symbols")
        else:
            console.print("  [dim]run `codegraph doctor --fix` to rebuild it[/dim]")

    # 3. Orphan edges
    orphans = store.orphan_edges()
    if not orphans:
        console.print("[green]OK[/green] Edges: no orphans")
    else:
        problems += 1
        console.print(f"[yellow]WARN[/yellow] Edges: {len(orphans)} orphan(s) referencing missing nodes")
        if fix:
            store.delete_edges(orphans)
            console.print(f"  [green]fixed[/green]: removed {len(orphans)} orphan edge(s)")
        else:
            console.print("  [dim]run `codegraph doctor --fix` to remove them[/dim]")

    # 4. Staleness vs git HEAD
    indexed_sha = store.get_config("last_indexed_sha")
    head = LocalRepo(repo.resolve()).get_head_sha()
    if head and indexed_sha == head:
        console.print(f"[green]OK[/green] Freshness: graph is at HEAD ({head[:8]})")
    elif head and indexed_sha:
        console.print(
            f"[yellow]WARN[/yellow] Freshness: graph at {indexed_sha[:8]}, HEAD is {head[:8]} "
            "— run `codegraph update`"
        )
    else:
        console.print("[yellow]WARN[/yellow] Freshness: no indexed commit recorded")

    # 5. Snapshot presence
    if settings.snapshot_path.exists():
        console.print("[green]OK[/green] NetworkX snapshot present")
    else:
        console.print("[dim]--[/dim] NetworkX snapshot absent (rebuilt from SQLite on load)")

    # 6. Basic counts
    node_count = store.graph.number_of_nodes() or "?"
    console.print(
        f"\n  nodes in DB: {sum(1 for _ in store.iter_node_data())}, "
        f"todos: {store.count_todos()}"
    )
    store.close()

    if problems and not fix:
        console.print(f"\n[yellow]{problems} problem(s) found.[/yellow] Re-run with --fix to repair.")
        raise typer.Exit(2)
    console.print("\n[green]Graph is healthy.[/green]" if not problems else "\n[green]Repairs applied.[/green]")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@app.command()
def diff(
    sha1: str = typer.Argument(help="Base ref (commit SHA, branch, or tag)"),
    sha2: str = typer.Argument(default="HEAD", help="Target ref (default: HEAD)"),
    repo: Path = typer.Option(Path("."), "--repo", help="Repo path"),
    no_blast: bool = typer.Option(False, "--no-blast", help="Skip blast radius analysis"),
):
    """Show symbol-level changes between two git refs.

    Compares functions, classes, and types changed between SHA1 and SHA2,
    then queries the graph for callers of every removed or modified symbol.

    Example: codegraph diff main HEAD
    """
    from codegraph.config import Settings
    from codegraph.git.local_repo import LocalRepo
    from codegraph.graph.differ import GraphDiffer
    from codegraph.graph.store import GraphStore
    from codegraph.parsers.registry import ParserRegistry

    settings = Settings.from_repo(repo)
    repo_path = repo.resolve()

    # Resolve refs
    git = LocalRepo(repo_path)
    resolved1 = git.resolve_ref(sha1)
    resolved2 = git.resolve_ref(sha2)
    if not resolved1:
        console.print(f"[red]Cannot resolve ref: {sha1}[/red]")
        raise typer.Exit(1)
    if not resolved2:
        console.print(f"[red]Cannot resolve ref: {sha2}[/red]")
        raise typer.Exit(1)

    # Load graph for blast radius (optional)
    store = None
    if not no_blast and settings.db_path.exists():
        store = GraphStore(settings.db_path)
        store.open()
        store.load_graph_to_memory()

    registry = ParserRegistry.default()
    differ = GraphDiffer(repo_path, registry, store=store)

    short1 = resolved1[:8]
    short2 = resolved2[:8]
    console.print(f"[bold]codeGraph diff[/bold]  {short1} → {short2}\n")

    result = differ.diff(resolved1, resolved2)

    if not result.file_diffs and not any(result.all_changes):
        console.print("[dim]No symbol-level changes detected.[/dim]")
        if store:
            store.close()
        return

    _ICONS = {"added": "[green]+[/green]", "removed": "[red]-[/red]", "modified": "[yellow]~[/yellow]"}
    _STATUS = {"A": "[green]NEW[/green]", "M": "[yellow]MOD[/yellow]", "D": "[red]DEL[/red]"}

    for fd in result.file_diffs:
        status_label = _STATUS.get(fd.status, fd.status)
        console.print(f"  {status_label}  [bold]{fd.path}[/bold]")
        for ch in fd.changes:
            icon = _ICONS.get(ch.change_type, " ")
            detail = f"  [dim]{ch.detail}[/dim]" if ch.detail else ""
            console.print(f"        {icon} [{ch.kind}] {ch.qualified_name}{detail}")
        console.print()

    summary = result.summary
    console.print(
        f"  Summary: [green]+{summary['added']} added[/green]  "
        f"[red]-{summary['removed']} removed[/red]  "
        f"[yellow]~{summary['modified']} modified[/yellow]"
    )

    if result.blast_radius:
        console.print("\n[bold]Blast radius[/bold] (existing callers of changed symbols):")
        for sym, callers in result.blast_radius.items():
            console.print(f"  [yellow]{sym}[/yellow] ← {', '.join(callers[:8])}")

    if store:
        store.close()


# ---------------------------------------------------------------------------
# enrich
# ---------------------------------------------------------------------------


@app.command()
def enrich(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    include_documented: bool = typer.Option(
        False,
        "--include-documented/--skip-documented",
        help="Also enrich symbols that already have docstrings",
    ),
    batch_size: int = typer.Option(20, "--batch-size", help="Symbols per API call"),
):
    """Generate LLM summaries for undocumented symbols in the graph."""
    from codegraph.config import Settings
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()

    from codegraph.enrichment.llm_enricher import LLMEnricher
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    enricher = LLMEnricher(store, settings)
    console.print("[bold cyan]LLM enrichment[/bold cyan] — using Anthropic Haiku...")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        stats = enricher.enrich(
            skip_documented=not include_documented,
            batch_size=batch_size,
            progress=progress,
        )

    console.print(
        f"\n[green]Enrichment complete:[/green]\n"
        f"  Generated (API): {stats['enriched']}\n"
        f"  From cache:      {stats['cached']}\n"
        f"  Skipped:         {stats['skipped']}\n"
        f"  Errors:          {stats['errors']}"
    )
    if stats["errors"] and not settings.anthropic_api_key:
        console.print(
            "[yellow]Tip:[/yellow] Set CODEGRAPH_ANTHROPIC_API_KEY to enable API calls."
        )

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
    focus: Optional[str] = typer.Option(None, "--focus", help="Focus file path for compressed output"),
    role: str = typer.Option("general", "--role", "-r", help="general|debug|review|feature"),
):
    """Generate a compressed context pack for LLM session start."""
    from codegraph.config import Settings
    from codegraph.context.compressor import ContextCompressor
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
    gen = ContextPackGenerator(store, q, token_budget, notes_path=settings.session_notes_path)
    cp = gen.generate()

    if focus or role != "general":
        compressor = ContextCompressor(store, q)
        cp = compressor.compress(cp, focus_file=focus, role=role)
        if focus:
            console.print(f"[cyan]Focus:[/cyan] {focus}  [cyan]Role:[/cyan] {role}")

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
    total_todos = store.count_todos()
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
# notes
# ---------------------------------------------------------------------------


@app.command()
def notes(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    add: Optional[str] = typer.Option(None, "--add", "-a", help="Append a new note"),
    category: str = typer.Option("general", "--category", "-c", help="Note category"),
    clear: bool = typer.Option(False, "--clear", help="Delete all notes"),
    count: int = typer.Option(20, "--count", "-n", help="Max notes to display"),
):
    """Show, add, or clear architectural session notes.

    Session notes persist across coding sessions and are included in
    CLAUDE.md so new sessions inherit prior discoveries.

    Examples:

      codegraph notes                         # show recent notes
      codegraph notes --add "GraphBuilder uses two-pass build" --category architecture
      codegraph notes --clear                 # wipe all notes
    """
    from codegraph.config import Settings
    from codegraph.context.session_notes import SessionNotesManager

    settings = Settings.from_repo(repo)
    mgr = SessionNotesManager(settings.session_notes_path)

    if clear:
        mgr.clear()
        console.print("[green]Session notes cleared.[/green]")
        return

    if add:
        mgr.append(add.strip(), category=category)
        console.print(f"[green]Note added[/green] [{category}]: {add[:80]}")
        return

    # Display
    recent = mgr.read_recent(max_notes=count)
    if not recent:
        console.print("[dim]No session notes yet.[/dim]")
        console.print(f"Add one: codegraph notes --add \"Your discovery here\"")
        return

    console.print(f"[bold]Session Notes[/bold] ({len(recent)} shown)\n")
    for n in recent:
        console.print(f"[bold cyan]{n['timestamp']}[/bold cyan] · [italic]{n['category']}[/italic]")
        console.print(f"{n['note']}\n")


# ---------------------------------------------------------------------------
# pr-patterns
# ---------------------------------------------------------------------------


@app.command(name="pr-patterns")
def pr_patterns(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    owner: str = typer.Option(..., "--owner", "-o", help="GitHub owner (user or org)"),
    github_repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo name"),
    pr_limit: int = typer.Option(30, "--prs", help="Number of merged PRs to analyze"),
    token: Optional[str] = typer.Option(None, "--token", help="GitHub token (or set GITHUB_TOKEN env)"),
    show: bool = typer.Option(False, "--show", help="Display stored results instead of re-mining"),
):
    """Mine recurring review feedback themes from merged GitHub PRs.

    Fetches the most recently merged PRs, collects review comments, and
    identifies recurring feedback patterns (type hints, error handling, tests,
    naming, complexity, etc.). Results are saved and shown in CLAUDE.md.

    Examples:

      codegraph pr-patterns --owner myorg --repo myproject --prs 50
      codegraph pr-patterns --owner myorg --repo myproject --show
    """
    import os
    from codegraph.config import Settings
    from codegraph.enrichment.pr_pattern_miner import PRPatternMiner
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()

    if show:
        data = PRPatternMiner.load(store)
        if not data:
            console.print("[yellow]No PR pattern data stored. Run without --show to mine.[/yellow]")
        else:
            _display_pr_patterns(data)
        store.close()
        return

    gh_token = token or os.environ.get("GITHUB_TOKEN")
    from codegraph.git.github_client import GitHubClient
    client = GitHubClient(token=gh_token)
    miner = PRPatternMiner(store, client, owner, github_repo)

    console.print(f"[cyan]Mining[/cyan] {owner}/{github_repo} (last {pr_limit} merged PRs)…")
    try:
        result = miner.mine_and_save(pr_limit=pr_limit)
    except Exception as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        raise typer.Exit(1)
    finally:
        client.close()

    _display_pr_patterns(result)
    store.close()


def _display_pr_patterns(data: dict) -> None:
    console.print(
        f"\n[bold]PR Pattern Analysis[/bold] — "
        f"{data['prs_analyzed']} PRs · {data['total_comments']} comments\n"
    )
    themes = data.get("themes", {})
    if not themes:
        console.print("[dim]No recurring patterns found.[/dim]")
        return

    t = Table(title="Recurring Feedback Themes", show_lines=False)
    t.add_column("Theme", style="cyan")
    t.add_column("Count", justify="right")
    t.add_column("Example")
    for theme, info in list(themes.items())[:10]:
        ex = (info["examples"][0] if info["examples"] else "")[:70]
        t.add_row(theme.replace("_", " "), str(info["count"]), ex)
    console.print(t)

    reviewers = data.get("top_reviewers", [])
    if reviewers:
        console.print("\n[bold]Top Reviewers[/bold]")
        for r in reviewers:
            console.print(f"  {r['login']}: {r['comments']} comments")


# ---------------------------------------------------------------------------


def _run_enrichment(store, settings, progress_style: str = "spinner") -> None:
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from codegraph.enrichment.llm_enricher import LLMEnricher

    enricher = LLMEnricher(store, settings)
    console.print("\n[bold cyan]LLM enrichment[/bold cyan] — generating summaries for undocumented symbols...")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        stats = enricher.enrich(progress=progress)
    console.print(
        f"  Enriched:  {stats['enriched']}  "
        f"Cached: {stats['cached']}  "
        f"Skipped: {stats['skipped']}  "
        f"Errors: {stats['errors']}"
    )


_PACK_MARKER = "Auto-generated by codeGraph"


def _generate_pack(store, settings, token_budget: int = 8000, mode: str = "auto") -> None:
    """Render the context pack and write it where it won't surprise the user.

    ``mode``:
      - ``auto`` (default): write ``CLAUDE.md`` only if it is absent or was
        itself codeGraph-generated; a hand-authored ``CLAUDE.md`` is left
        untouched and the pack is written to ``.codegraph/context-pack.md``.
      - ``force``: always (over)write ``CLAUDE.md``.
      - ``off``: never touch ``CLAUDE.md``; write only the fallback file.
    """
    from codegraph.context.pack_generator import ContextPackGenerator
    from codegraph.graph.queries import GraphQuery

    q = GraphQuery(store)
    gen = ContextPackGenerator(store, q, token_budget, notes_path=settings.session_notes_path)
    cp = gen.generate()
    markdown = gen.to_markdown(cp)

    claude_md = settings.repo_path / "CLAUDE.md"
    fallback = settings.codegraph_dir / "context-pack.md"

    def _is_generated(path: Path) -> bool:
        try:
            return _PACK_MARKER in path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False

    write_claude = mode == "force" or (
        mode == "auto" and (not claude_md.exists() or _is_generated(claude_md))
    )
    if write_claude:
        claude_md.write_text(markdown, encoding="utf-8")
        console.print(f"[green]CLAUDE.md[/green] updated at {claude_md}")
        return

    settings.codegraph_dir.mkdir(exist_ok=True)
    fallback.write_text(markdown, encoding="utf-8")
    if mode == "off":
        console.print(f"[green]Context pack[/green] written to {fallback}")
    else:
        console.print(
            f"[yellow]Preserved your hand-authored CLAUDE.md[/yellow]; "
            f"context pack written to {fallback}.\n"
            f"  Pass [cyan]--force-claude-md[/cyan] to overwrite CLAUDE.md instead."
        )


if __name__ == "__main__":
    app()
