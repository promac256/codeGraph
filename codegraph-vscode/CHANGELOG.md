# Changelog

## 0.2.0

- **Native (Python-free) backend** — in-process Node/WASM indexing for all six
  languages (Python, TS/JS, Go, Rust, Java, C/C++); `codegraph.backend: "native"`.
- **Live graph** — file saves re-index incrementally; the graph no longer goes
  stale during an editing session.
- **Editor integrations** — Go to Definition, Find References (callers), and
  per-function caller-count CodeLens with a jump-to-call-site quick pick.
- **Async activation** — indexing is chunked, cancellable, and reports
  progress; a status bar item shows indexing / ready / error state.
- **Graph view** — tooltip content is HTML-escaped; truncated views now say
  "showing X of Y files" instead of passing as the whole repo.
- **First-run guidance** — chat and tools link to *Initialize / Rebuild Graph*
  when no graph exists; multi-root workspaces get an explicit notice.
- **Parity contract** — golden tests (`npm run parity` + pytest) keep the
  native and Python backends from drifting.

## 0.1.0

- Initial release: Copilot chat participant + 12 LM tools, 3D graph view,
  error tracing, Python-CLI MCP backend (stdio/SSE).
