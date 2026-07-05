# codeGraph

A knowledge-graph engine for LLM coding assistants. codeGraph indexes a git
repository into a dual-layer graph (SQLite + NetworkX), exposes it through 17
MCP tools, and generates compressed context packs (`CLAUDE.md`) that prime a
coding session in ~2–8k tokens.

## Why

LLM coding assistants waste context re-discovering structure every session.
codeGraph builds that structure once — symbols, call graph, imports, churn,
architectural layers — and serves it on demand: "who calls this?", "what
breaks if I change this?", "what are the hot paths?", all answered from a graph
instead of a full-repo scan.

## Features

- **Multi-language parsing** via tree-sitter: Python, TypeScript/JavaScript,
  Go, Rust, Java, C/C++ (generic regex fallback for the rest).
- **Real call graph** with class-aware resolution — `find_callers` and
  `impact_analysis` (blast radius) work across files.
- **Git-aware**: per-file commit churn feeds hot-path ranking; incremental
  updates from new commits.
- **Context packs**: token-budgeted `CLAUDE.md` generation with role overlays
  (`debug`, `review`, `feature`).
- **MCP server** (FastMCP) over stdio or SSE — shareable between Claude Code
  and the VS Code extension.
- **VS Code extension** with a `@codegraph` Copilot participant and an
  interactive 3D graph view — runnable with **no Python install** via a
  built-in Node/WASM backend (all 6 languages, in-process).

## Install

```bash
pip install -e ".[all-langs]"        # backend + all language parsers
pip install -e ".[all-langs,git]"    # also enable GitHub PR mining
```

Requires Python 3.10+.

## Quickstart

```bash
codegraph init .          # build the graph for the current repo
codegraph stats           # repo overview + hot-paths heatmap
codegraph query upsert_node --callers
codegraph serve .         # start the MCP server (stdio)
```

`init` writes a context pack to `CLAUDE.md`. If you maintain a hand-authored
`CLAUDE.md`, codeGraph leaves it untouched and writes the pack to
`.codegraph/context-pack.md` instead — pass `--force-claude-md` to overwrite,
or `--no-claude-md` to always use the fallback location.

## CLI

| Command | Purpose |
|---|---|
| `init` | Full build (`--llm-enrich`, `--workers`, `--token-budget`, `--force-claude-md`, `--no-claude-md`) |
| `update` | Incremental update from commits (`--since SHA`) |
| `diff <sha1> [sha2]` | Symbol-level change analysis + blast radius |
| `query <name>` | Look up a symbol (`--kind`, `--callers`) |
| `pack` | Generate a context pack (`--focus FILE`, `--role debug\|review\|feature`) |
| `report` | Interactive HTML report (`--open`) |
| `serve` | MCP server (`--transport stdio\|sse`, `--port 8765`) |
| `stats` | Repo statistics and hot-paths heatmap |
| `watch` | Auto-update on git changes |
| `notes` | Session notes (`--add`, `--category`, `--refs`, `--source`, `--clear`) |
| `lint` | Graph health checks — dangling edges, stale summaries, dead note refs (`--fix`) |
| `hooks` | Print recipes for automatic graph maintenance (Claude Code hooks, cron) |
| `enrich` | Anthropic API docstring summaries |
| `pr-patterns` | Mine GitHub PR review themes |

## MCP integration

`codegraph serve .` starts a FastMCP server named `codeGraph` exposing 18 tools
(`codegraph_find_symbol`, `codegraph_find_callers`, `codegraph_impact_analysis`,
`codegraph_hot_paths`, `codegraph_compress`, `codegraph_health`, …) plus
`graph://context-pack` and `graph://summary` resources. Use `--transport sse
--port 8765` to run a shared server that Claude Code and the VS Code extension
can both connect to.

## Keeping the graph alive

A knowledge graph that only grows when you remember to feed it goes stale.
codeGraph ships the pieces; scheduling belongs to your harness:

- **Session notes are graph nodes.** `codegraph notes --add "..." --refs
  GraphBuilder.build --source session` stores the note in the append-only raw
  layer (`.codegraph/session_notes.md`) *and* as a `note` node with
  `annotates` edges to the referenced symbols. Notes attached to hot-path
  symbols are preferred when the context pack is assembled, and
  `codegraph_find_symbol` returns the notes attached to each match.
- **Summaries survive updates and announce staleness.** LLM summaries carry
  `llm_enriched_at` + a content cache key; `codegraph update` re-attaches
  unchanged summaries from the local cache after re-parsing files, and
  `codegraph lint` flags summaries whose symbol has since changed.
- **`codegraph lint [--fix]`** detects graph rot — dangling edges, unresolved
  note refs, files missing on disk, index-behind-HEAD — and applies the safe
  repairs with `--fix`.
- **`codegraph hooks`** prints ready-to-paste recipes: a Claude Code
  `SessionEnd` hook (refresh + lint on session end), a CLAUDE.md snippet
  telling agents to file durable decisions as linked notes, and a nightly
  maintenance cron line.

## VS Code extension

```bash
cd codegraph-vscode
npm install
npm run bundle      # production build -> dist/ (incl. WASM grammars)
```

The extension activates on startup and offers a `@codegraph` Copilot chat
participant, Copilot language-model tools, error tracing, and a 3D graph view.

### Two backends (`codegraph.backend`)

The extension can source its graph from either backend, selectable in settings:

- **`python`** (default) — talks to the `codegraph` CLI over MCP (a stdio
  subprocess, or a shared `--transport sse` server via `codegraph.transport`).
  Full feature set (LLM enrichment, diff, PR mining), but requires a Python
  install with `codegraph` available.
- **`native`** — a built-in **Node/WASM backend that runs in-process with no
  Python**. Parses all six languages with `web-tree-sitter`, builds the graph
  in memory, and answers the same tools. Ideal for a zero-install setup;
  indexing runs on activation / "codeGraph: Initialize / Rebuild Graph".

The native backend covers the core read tools (find symbol/callers, impact
analysis, hot paths, dependencies, search, public API, architectural layers,
todos). Enrichment, diff, PR mining, and convention/test-coverage mining
remain CLI-only — use the `python` backend for those.

### Install the packaged extension

```bash
cd codegraph-vscode
npx @vscode/vsce package --no-dependencies   # -> codegraph-0.1.0.vsix
code --install-extension codegraph-0.1.0.vsix
```

## Configuration

`Settings` (Pydantic) reads from the environment or an optional `codegraph.toml`.
Key vars: `CODEGRAPH_ANTHROPIC_API_KEY` (LLM enrichment),
`CODEGRAPH_GITHUB_TOKEN` (PR mining), `CODEGRAPH_REPO_PATH` (server target).
All artifacts live under `.codegraph/` in the repo root.

## Tests

```bash
pytest                       # full suite
pytest --cov=codegraph       # with coverage
```

## Limitations

Call-graph resolution is static and name-based (no full type inference). Calls
on the enclosing instance (`self`/`this`/unqualified) bind to the caller's own
class; calls to well-known builtin/stdlib method names on other receivers are
dropped to avoid false edges. Cross-object calls to repo-unique names resolve;
genuinely ambiguous cross-file names are skipped rather than guessed.

## Architecture

See [CLAUDE.md](CLAUDE.md) for a full architectural tour (graph store, build
pipeline, parsers, context packing, MCP server, data models).
