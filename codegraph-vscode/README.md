# codeGraph — Knowledge Graph for Your Codebase

codeGraph indexes your repository into a live knowledge graph — symbols, call
graph, imports, git churn, architectural layers — and puts it to work inside
VS Code: Go to Definition and Find References answered from the graph, caller
counts above every function, a `@codegraph` Copilot chat participant, error
tracing, and an interactive 3D visualization of your architecture.

**No Python or external tools required** — the built-in Node/WASM backend
parses six languages in-process: Python, TypeScript/JavaScript, Go, Rust,
Java, and C/C++.

## Getting started

1. Open a folder that is a git repository.
2. Run **codeGraph: Initialize / Rebuild Graph** (or click the codeGraph
   status bar item). Indexing runs in the background with progress.
3. Press `Ctrl+Shift+G` (`Cmd+Shift+G` on macOS) to open the 3D graph view.

The graph stays fresh automatically: every file save re-indexes just that
file in-process.

## Features

- **Editor integrations** — Go to Definition, Find References (callers), and
  a CodeLens above each function showing its caller count; click it to jump
  to any call site.
- **`@codegraph` chat participant** — `/symbol`, `/callers`, `/impact`,
  `/deps`, `/hotspots`, `/overview`, `/search`, `/todos`, `/errors` in
  Copilot Chat, plus 12 language-model tools Copilot can call on its own.
- **3D graph view** — files stratified by architectural layer, hot paths
  highlighted by PageRank × git churn; error tracing pulses failing nodes.
- **Impact analysis** — blast-radius queries: what breaks if this changes?
- **Status bar** — indexing progress, symbol count, one-click rebuild.

## Backends

| `codegraph.backend` | What it is | Needs |
|---|---|---|
| `native` | In-process Node/WASM (all 6 languages, core read tools) | nothing |
| `python` (default) | The `codegraph` CLI over MCP — full feature set incl. LLM enrichment, symbol-level git diff, PR pattern mining | `pip install codegraph` |

For a zero-install experience set `codegraph.backend: "native"` in settings.
With the Python backend, the extension can also share one live graph server
with Claude Code via SSE (`codegraph.transport: "sse"`).

## Key settings

- `codegraph.backend` — `native` (no install) or `python` (full features)
- `codegraph.pythonPath` — Python interpreter for the CLI backend
- `codegraph.transport` / `codegraph.ssePort` — shared-server mode
- `codegraph.errorTracing` — pulse error paths in the graph view

## Requirements

- VS Code 1.93+
- A git repository (churn/hot-path ranking uses commit history)
- GitHub Copilot Chat (optional — only for the chat participant and LM tools)

## Learn more

Source, CLI documentation, and the MCP server for Claude Code:
[github.com/promac256/codeGraph](https://github.com/promac256/codeGraph)
