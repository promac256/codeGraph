# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What codeGraph Is

A knowledge graph engine for LLM coding assistants. It indexes a git repository into a dual-layer graph (SQLite + NetworkX), exposes 17 query tools via MCP, and generates compressed context packs (CLAUDE.md files) for session initialization in ~2–8k tokens.

**Monorepo layout:**
- `codegraph/` — Python backend: CLI, MCP server, parsers, graph engine
- `codegraph-vscode/` — TypeScript VS Code extension with Copilot integration and 3D graph view
- `tests/` — pytest suite with language fixture files

## Python Backend

### Install & run

```bash
pip install -e ".[all-langs]"        # install with all language parsers
pip install -e ".[all-langs,git]"    # also enables GitHub PR mining

codegraph init .                     # build graph for current repo
codegraph update .                   # incremental update from new commits
codegraph serve .                    # start MCP server (stdio)
codegraph serve . --transport sse    # start shared SSE server on port 8765
```

### Tests

```bash
pytest                               # all tests
pytest tests/test_mcp_server.py      # single file
pytest -k test_find_symbol           # by name pattern
pytest --cov=codegraph               # with coverage
```

`asyncio_mode = "auto"` is set in `pyproject.toml` — no `@pytest.mark.asyncio` needed.

### CLI commands

| Command | Purpose |
|---|---|
| `init` | Full build from scratch (`--llm-enrich`, `--workers 8`, `--token-budget 8000`) |
| `update` | Incremental from commits (`--since SHA`) |
| `diff <sha1> [sha2]` | Symbol-level change analysis + blast radius |
| `enrich` | Anthropic API docstring summaries for undocumented symbols |
| `query <name>` | Look up symbol (`--kind`, `--callers`) |
| `pack` | Generate context pack (`--focus FILE`, `--role debug\|review\|feature`) |
| `report` | Interactive HTML report (`--open`) |
| `serve` | MCP server (`--transport stdio\|sse`, `--port 8765`) |
| `stats` | Repo statistics and hot-paths heatmap |
| `watch` | Auto-update on git changes |
| `notes` | Session notes (`--add TEXT`, `--category`, `--clear`) |
| `pr-patterns` | Mine GitHub PR review themes |

## Architecture

### Dual-layer graph store (`codegraph/graph/store.py`)

SQLite handles durable storage with FTS5 full-text search on symbol names. NetworkX `MultiDiGraph` handles in-memory traversal (PageRank, BFS, impact analysis). Both are populated together on every `upsert_node()` / `upsert_edge()` call. The store lives at `.codegraph/graph.db`; the NetworkX snapshot is serialized to `.codegraph/graph.nx.json.gz`.

### Build pipeline (`codegraph/graph/builder.py`)

`GraphBuilder.build()` parses all repo files in parallel (configurable `max_workers`), then does a cross-file reference resolution pass. Parsing is delegated to `ParserRegistry` which selects the right tree-sitter parser by extension. A generic regex fallback handles unsupported languages.

### Language parsers (`codegraph/parsers/`)

All parsers extend `LanguageParser` (ABC in `base.py`) and return lists of `FileNode`, `FunctionNode`, `ClassNode`, `TypeNode`, and `GraphEdge`. Supported: Python, TypeScript/JS, Go, Rust, Java, C/C++.

### Context packing (`codegraph/context/pack_generator.py`)

Token-budget-aware tiered strategy:
- **Tier 1** (always included): overview, architectural layers, hot paths, recent changes, public API summary
- **Tier 2** (fills remaining budget): key modules, class hierarchy, todos
- **Tier 3** (index only): symbol/file counts

`ContextCompressor` (`compressor.py`) applies role overlays on top: `debug` re-ranks by complexity×churn; `review` caps hot paths and sorts API alphabetically; `feature` surfaces test modules and similar files.

### MCP server (`codegraph/mcp/server.py`)

FastMCP server named `codeGraph`. The server holds a single `GraphStore` instance in module-level globals (`_store`, `_queries`) initialized at startup with the repo path. The 17 tools (`codegraph_find_symbol`, `codegraph_find_callers`, `codegraph_get_dependencies`, `codegraph_hot_paths`, `codegraph_impact_analysis`, `codegraph_compress`, etc.) plus 2 resources (`graph://context-pack`, `graph://summary`) all operate against this shared state. Tests inject a test graph directly into these globals to avoid filesystem setup.

### Data models (`codegraph/models.py`)

`NodeKind`: `repo | file | module | class | function | type | test | commit`  
`EdgeKind`: `imports | defines | calls | inherits | implements | tests | modifies | exports | contains | depends_on | resolves_to`

Qualified names for methods follow `ClassName.method_name` convention — this is what MCP tool lookups expect.

### Configuration (`codegraph/config.py`)

`Settings` (Pydantic BaseSettings) reads from environment or optional `codegraph.toml`. Key env vars: `CODEGRAPH_ANTHROPIC_API_KEY` (LLM enrichment), `CODEGRAPH_GITHUB_TOKEN` (PR mining). All graph artifacts live under `.codegraph/` in the repo root.

## VS Code Extension

```bash
cd codegraph-vscode
npm install
npm run bundle          # production build → dist/
npm run watch           # dev watch mode
npm run lint            # ESLint
```

The extension activates on `onStartupFinished`. It spawns the Python `codegraph-mcp` server as a stdio subprocess by default; with `codegraph.transport: "sse"` it connects to an already-running server on `codegraph.ssePort` (default 8765) — enabling Claude Code desktop and the extension to share one live graph.

The `@codegraph` Copilot chat participant and 12 registered LM tools all route through `McpClient` (`src/backend/mcp-client.ts`), which wraps the MCP JSON-RPC protocol and unwraps the `{content:[{type:'text',text:'...'}]}` envelope before returning results to callers.

The 3D graph webview (`src/graph/webview.ts`) uses `3d-force-graph` + Three.js. Nodes are stratified by architectural layer on distinct Z-planes; error tracing (`src/editor/diagnostics.ts`) pulses error nodes red and fades unrelated nodes to 12% opacity.
