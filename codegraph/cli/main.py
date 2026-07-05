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
    no_claude_md: bool = typer.Option(
        False, "--no-claude-md",
        help="Write the context pack to .codegraph/ instead of CLAUDE.md",
    ),
    force_claude_md: bool = typer.Option(
        False, "--force-claude-md",
        help="Overwrite CLAUDE.md even if it was hand-authored",
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

    # Mine conventions
    from codegraph.enrichment.convention_miner import ConventionMiner
    ConventionMiner(store).mine_and_save()

    # Re-promote session notes (raw markdown layer) to graph nodes —
    # the full rebuild above wiped all prior note nodes
    from codegraph.context.session_notes import SessionNotesManager
    notes_synced = SessionNotesManager(
        settings.session_notes_path, store=store
    ).sync_graph_nodes()

    console.print(f"\n[green]Graph built successfully:[/green]")
    console.print(f"  Files parsed:  {stats['files_parsed']}")
    console.print(f"  Files skipped: {stats['files_skipped']}")
    console.print(f"  Nodes:         {stats['nodes']}")
    console.print(f"  Edges:         {stats['edges']}")
    console.print(f"  Commits:       {stats.get('commits', 0)}")
    if notes_synced:
        console.print(f"  Session notes re-linked: {notes_synced}")
    if stats["errors"]:
        console.print(f"  [yellow]Errors:        {stats['errors']}[/yellow]")

    if llm_enrich:
        _run_enrichment(store, settings, progress_style="bar")

    # Generate context pack
    pack_mode = "off" if no_claude_md else ("force" if force_claude_md else "auto")
    _generate_pack(store, settings, token_budget=token_budget, mode=pack_mode)
    store.close()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@app.command()
def update(
    repo: Path = typer.Argument(default=Path("."), help="Path to git repo"),
    since: Optional[str] = typer.Option(None, "--since", help="SHA to update from"),
    re_enrich: bool = typer.Option(
        False, "--re-enrich",
        help="Also run LLM enrichment for symbols missing summaries",
    ),
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
    from codegraph.enrichment.convention_miner import ConventionMiner
    ConventionMiner(store).mine_and_save()

    # Re-attach LLM summaries dropped by file re-ingestion (cache-only, no API)
    from codegraph.enrichment.llm_enricher import LLMEnricher
    restored = LLMEnricher(store, settings).reattach_from_cache()

    console.print(f"[green]Update complete:[/green]")
    console.print(f"  Commits processed: {stats['commits_processed']}")
    console.print(f"  Files updated:     {stats['files_updated']}")
    console.print(f"  Files deleted:     {stats['files_deleted']}")
    if restored:
        console.print(f"  Summaries restored from cache: {restored}")

    if re_enrich:
        _run_enrichment(store, settings)

    _generate_pack(store, settings)
    store.close()


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
# lint
# ---------------------------------------------------------------------------


@app.command()
def lint(
    repo: Path = typer.Argument(default=Path("."), help="Repo path"),
    fix: bool = typer.Option(False, "--fix", help="Apply safe repairs (drop dangling edges, re-resolve note refs)"),
):
    """Check graph health: dangling edges, stale summaries, dead note refs.

    Knowledge graphs rot without maintenance — run this periodically
    (e.g. nightly alongside `codegraph update`, see `codegraph hooks`).
    Only safe repairs are applied with --fix; nothing destructive.
    """
    from codegraph.config import Settings
    from codegraph.graph.lint import GraphLinter
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)
    if not settings.db_path.exists():
        console.print("[red]No graph found. Run `codegraph init` first.[/red]")
        raise typer.Exit(1)

    store = GraphStore(settings.db_path)
    store.open()
    store.load_graph_to_memory()

    linter = GraphLinter(
        store, repo_root=repo.resolve(), codegraph_dir=settings.codegraph_dir
    )
    result = linter.lint(fix=fix)

    if not result["findings"]:
        console.print("[green]Graph is healthy — no findings.[/green]")
        store.close()
        return

    _SEV_STYLE = {"error": "red", "warning": "yellow", "info": "dim"}
    table = Table(title=f"Graph Lint — {result['total']} findings", show_header=True)
    table.add_column("Severity", width=9)
    table.add_column("Check", style="cyan")
    table.add_column("Subject")
    table.add_column("Message")
    for f in result["findings"][:50]:
        style = _SEV_STYLE.get(f["severity"], "")
        table.add_row(
            f"[{style}]{f['severity']}[/{style}]" if style else f["severity"],
            f["check"],
            str(f["subject"])[:60],
            f["message"][:80],
        )
    console.print(table)
    if result["total"] > 50:
        console.print(f"[dim]... and {result['total'] - 50} more[/dim]")

    if result["fixed"]:
        fixes = ", ".join(f"{k}: {v}" for k, v in result["fixed"].items())
        console.print(f"\n[green]Repairs applied:[/green] {fixes}")
    elif any(f.get("fixable") for f in result["findings"]):
        console.print("\n[dim]Re-run with --fix to apply safe repairs.[/dim]")

    store.close()


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------


@app.command()
def hooks():
    """Print recipes for keeping the graph alive automatically.

    Shows a Claude Code hooks block (session-end graph refresh), a CLAUDE.md
    instruction snippet (agents file durable decisions as linked notes), and
    a nightly maintenance cron line.
    """
    console.print("""\
[bold]Keeping the graph alive[/bold]

A knowledge graph that only grows when you remember to feed it goes stale.
Three recipes — copy what fits your setup:

[bold cyan]1. Claude Code hooks[/bold cyan] — refresh the graph when a session ends.
Merge into .claude/settings.json:

[dim]{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "codegraph update . && codegraph lint . --fix"
          }
        ]
      }
    ]
  }
}[/dim]

[bold cyan]2. CLAUDE.md instruction[/bold cyan] — agents file durable knowledge as linked notes.
Add to your project's CLAUDE.md:

[dim]## Session memory
- Before ending a session, record durable decisions, gotchas, and conventions:
  `codegraph notes --add "..." --category decision --refs Symbol.name --source session`
  (or the MCP tool codegraph_add_session_note with refs=[...])
- Query prior knowledge before large changes: codegraph_get_session_notes,
  and check the notes attached to symbols via codegraph_find_symbol.[/dim]

[bold cyan]3. Nightly maintenance[/bold cyan] — fold in commits, restore summaries, repair rot.
Cron line:

[dim]0 3 * * *  cd /path/to/repo && codegraph update . --re-enrich && codegraph lint . --fix[/dim]
""")


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
    refs: Optional[str] = typer.Option(
        None, "--refs", help="Comma-separated symbol names this note is about"
    ),
    source: str = typer.Option(
        "manual", "--source", help="Provenance: manual|session|pr|commit"
    ),
    clear: bool = typer.Option(False, "--clear", help="Delete all notes"),
    count: int = typer.Option(20, "--count", "-n", help="Max notes to display"),
):
    """Show, add, or clear architectural session notes.

    Session notes persist across coding sessions, are included in
    CLAUDE.md so new sessions inherit prior discoveries, and are stored
    as graph nodes linked to the symbols named in --refs.

    Examples:

      codegraph notes                         # show recent notes
      codegraph notes --add "GraphBuilder uses two-pass build" --category architecture --refs GraphBuilder.build
      codegraph notes --clear                 # wipe all notes
    """
    from codegraph.config import Settings
    from codegraph.context.session_notes import SessionNotesManager
    from codegraph.graph.store import GraphStore

    settings = Settings.from_repo(repo)

    store = None
    if settings.db_path.exists():
        store = GraphStore(settings.db_path)
        store.open()
        store.load_graph_to_memory()
    mgr = SessionNotesManager(settings.session_notes_path, store=store)

    try:
        if clear:
            mgr.clear()
            console.print("[green]Session notes cleared.[/green]")
            return

        if add:
            ref_list = [r.strip() for r in refs.split(",")] if refs else None
            result = mgr.append(
                add.strip(), category=category, refs=ref_list, source=source
            )
            console.print(f"[green]Note added[/green] [{category}]: {add[:80]}")
            if result.get("resolved_refs"):
                console.print(
                    f"  Linked to: {', '.join(result['resolved_refs'].keys())}"
                )
            if result.get("unresolved_refs"):
                console.print(
                    f"  [yellow]Unresolved refs:[/yellow] "
                    f"{', '.join(result['unresolved_refs'])}"
                )
            return

        # Display
        recent = mgr.read_recent(max_notes=count)
        if not recent:
            console.print("[dim]No session notes yet.[/dim]")
            console.print(f"Add one: codegraph notes --add \"Your discovery here\"")
            return

        console.print(f"[bold]Session Notes[/bold] ({len(recent)} shown)\n")
        for n in recent:
            meta = f"[bold cyan]{n['timestamp']}[/bold cyan] · [italic]{n['category']}[/italic]"
            if n.get("source") and n["source"] != "manual":
                meta += f" · {n['source']}"
            console.print(meta)
            console.print(f"{n['note']}")
            if n.get("refs"):
                console.print(f"[dim]refs: {', '.join(n['refs'])}[/dim]")
            console.print()
    finally:
        if store is not None:
            store.close()


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
