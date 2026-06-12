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
  interactive 3D graph view.

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
| `notes` | Session notes (`--add`, `--category`, `--clear`) |
| `enrich` | Anthropic API docstring summaries |
| `pr-patterns` | Mine GitHub PR review themes |

## MCP integration

`codegraph serve .` starts a FastMCP server named `codeGraph` exposing 17 tools
(`codegraph_find_symbol`, `codegraph_find_callers`, `codegraph_impact_analysis`,
`codegraph_hot_paths`, `codegraph_compress`, …) plus `graph://context-pack` and
`graph://summary` resources. Use `--transport sse --port 8765` to run a shared
server that Claude Code and the VS Code extension can both connect to.

## VS Code extension

```bash
cd codegraph-vscode
npm install
npm run bundle      # production build -> dist/
```

Activates on startup, spawns the Python server as a stdio subprocess by
default (or connects to a running SSE server with `codegraph.transport: "sse"`).

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
