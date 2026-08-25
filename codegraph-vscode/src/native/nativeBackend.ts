/**
 * NativeBackend — an in-process, Python-free implementation of the Backend
 * surface. Parses the repo with web-tree-sitter (WASM), builds the graph in
 * memory, and answers the same tool calls as the Python MCP server with the
 * same JSON shapes, so it is a drop-in replacement for McpClient.
 *
 * Spike scope: Python sources + the read tools used by the extension UI.
 * Additional languages are additive (one parser module each).
 */
import * as fs from 'fs';
import * as path from 'path';
import Parser from 'web-tree-sitter';
import { GraphStore } from './graph';
import { parseGeneric, type LangSpec } from './genericParser';
import { LANG_SPECS, specForFile } from './langSpecs';
import { PythonParser } from './pythonParser';
import type { Backend, GNode, ParseResult } from './types';

const SKIP_DIRS = new Set([
  '.git', 'node_modules', '__pycache__', '.venv', 'venv', 'env', 'dist',
  'build', '.tox', 'vendor', '.mypy_cache', '.pytest_cache', '.codegraph',
  'out', '.next', 'target', 'coverage', '.cache', '.idea',
]);

// Files above this size are almost always generated (minified bundles, data
// dumps) — parsing them risks an uncatchable WASM out-of-memory abort that
// takes down the whole extension host.
const MAX_FILE_BYTES = 1_000_000;

export interface NativeBackendOptions {
  /** Directory holding grammar wasms (e.g. node_modules/tree-sitter-wasms/out). */
  wasmDir: string;
  /** Path to web-tree-sitter's core tree-sitter.wasm. */
  coreWasmPath: string;
}

export class NativeBackend implements Backend {
  private readonly repoPath: string;
  private readonly opts: NativeBackendOptions;
  private store = new GraphStore();
  private allTodos: Array<{ file: string; line: number; kind: string; text: string }> = [];
  private _ready = false;

  constructor(repoPath: string, opts: NativeBackendOptions) {
    this.repoPath = repoPath;
    this.opts = opts;
  }

  get ready(): boolean { return this._ready; }
  get transport(): string { return 'native'; }

  async start(): Promise<void> {
    await Parser.init({ locateFile: () => this.opts.coreWasmPath });

    // Load every grammar whose wasm shipped; skip any that's missing.
    const load = async (wasm: string): Promise<Parser | null> => {
      const p = path.join(this.opts.wasmDir, wasm);
      if (!fs.existsSync(p)) return null;
      const parser = new Parser();
      parser.setLanguage(await Parser.Language.load(p));
      return parser;
    };

    const pyParser = await load('tree-sitter-python.wasm');
    const py = new PythonParser();
    const specParsers = new Map<LangSpec, Parser>();
    for (const spec of LANG_SPECS) {
      const parser = await load(spec.grammarWasm);
      if (parser) specParsers.set(spec, parser);
    }

    const results: ParseResult[] = [];
    const ingestResult = (rel: string, res: ParseResult) => {
      results.push(res);
      for (const t of res.todos) this.allTodos.push({ file: rel, ...t });
    };

    let sinceYield = 0;
    for (const file of this.walk(this.repoPath)) {
      // Decide whether this is a parseable source file BEFORE touching its
      // contents — a workspace can contain arbitrarily large data files, and
      // readFileSync on one of those OOMs the extension host.
      const isPython = file.endsWith('.py') || file.endsWith('.pyi');
      const spec = isPython ? undefined : specForFile(file);
      const parser = isPython ? pyParser : (spec ? specParsers.get(spec) : undefined);
      if (!parser) continue;

      let size = 0;
      try { size = fs.statSync(file).size; } catch { continue; }
      if (size === 0 || size > MAX_FILE_BYTES) continue;

      const rel = path.relative(this.repoPath, file).replace(/\\/g, '/');
      let source: string;
      try { source = fs.readFileSync(file, 'utf8'); } catch { continue; }

      if (isPython) {
        this.tryParse(parser, source, (tree) => ingestResult(rel, py.parse(tree, rel, source)));
      } else if (spec) {
        this.tryParse(parser, source, (tree) => ingestResult(rel, parseGeneric(spec, tree, rel, source)));
      }

      // Yield to the event loop periodically so a large workspace doesn't
      // freeze the extension host during indexing.
      if (++sinceYield >= 25) {
        sinceYield = 0;
        await new Promise<void>((r) => setImmediate(r));
      }
    }

    this.store.ingest(results);
    this.store.resolveCrossReferences();
    this.store.computePageRank();
    this.store.ingestGitChurn(this.repoPath);
    this._ready = true;
  }

  private tryParse(parser: Parser, source: string, use: (tree: Parser.Tree) => void): void {
    let tree: Parser.Tree | null = null;
    try {
      tree = parser.parse(source);
      if (tree) use(tree);
    } catch { /* skip unparseable file */ } finally {
      tree?.delete();
    }
  }

  dispose(): void { this._ready = false; this.store = new GraphStore(); }

  private *walk(dir: string): Generator<string> {
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) yield* this.walk(full);
      } else if (entry.isFile()) {
        yield full;
      }
    }
  }

  async call<T = unknown>(toolName: string, args: Record<string, unknown> = {}): Promise<T> {
    if (!this._ready) throw new Error('NativeBackend not started');
    const r = this.dispatch(toolName, args);
    return r as T;
  }

  // ---- tool dispatch -------------------------------------------------------

  private dispatch(tool: string, a: Record<string, unknown>): unknown {
    switch (tool) {
      case 'codegraph_find_symbol': return this.findSymbol(String(a.name ?? ''), String(a.kind ?? 'any'));
      case 'codegraph_find_callers': return this.findCallers(String(a.symbol_name ?? ''), Number(a.depth ?? 1));
      case 'codegraph_impact_analysis': return this.impact(String(a.symbol_name ?? ''), Number(a.max_depth ?? 3));
      case 'codegraph_hot_paths': return { hot_paths: this.hotPaths(Number(a.top_n ?? 20)) };
      case 'codegraph_search': return this.search(String(a.query ?? ''), Number(a.limit ?? 20));
      case 'codegraph_overview': return this.overview();
      case 'codegraph_get_dependencies': return this.dependencies(`file:${a.file_path}`);
      case 'codegraph_architectural_layers': return { layers: this.layers() };
      case 'codegraph_todos': return { todos: this.todos(String(a.kind ?? 'all'), Number(a.limit ?? 50)) };
      case 'codegraph_public_api': return this.publicApi(a.file_path ? `file:${a.file_path}` : null);
      case 'codegraph_recent_changes': return { changes: [] }; // not yet ported (git-log driven)
      case 'codegraph_test_coverage': return { symbol: String(a.symbol_name ?? ''), tests: [] };
      case 'codegraph_conventions': return { conventions: {}, note: 'convention mining is CLI-only' };
      default: throw new Error(`Unknown or unsupported tool: ${tool}`);
    }
  }

  private resolveId(nameOrId: string): string | null {
    if (this.store.nodes.has(nameOrId)) return nameOrId;
    let best: GNode | null = null;
    for (const n of this.store.nodes.values()) {
      if (n.name === nameOrId && (n.kind === 'function' || n.kind === 'class')) {
        if (!best || (n.pagerank ?? 0) > (best.pagerank ?? 0)) best = n;
      }
    }
    return best?.id ?? null;
  }

  private findSymbol(name: string, kind: string) {
    const k = kind === 'any' ? null : kind;
    const matches = [...this.store.nodes.values()]
      .filter((n) => n.name === name && (!k || n.kind === k))
      .map((n) => ({
        node_id: n.id, kind: n.kind, name: n.name,
        file: n.file.replace('file:', ''), line: n.lineStart ?? 0,
        signature: n.signature ?? '', docstring: (n.docstring ?? '').slice(0, 200),
      }));
    return { query: name, kind, matches, count: matches.length };
  }

  private nodeDict(n: GNode) {
    return {
      node_id: n.id, name: n.name, kind: n.kind,
      file: n.file.replace('file:', ''), line_start: n.lineStart ?? 0,
      pagerank: n.pagerank ?? 0, commit_count: n.commitCount ?? 0,
    };
  }

  private findCallers(symbolName: string, depth: number) {
    const id = this.resolveId(symbolName);
    if (!id) return { symbol: symbolName, depth, callers: [], count: 0 };
    const callers: ReturnType<NativeBackend['nodeDict']>[] = [];
    const visited = new Set<string>();
    let frontier = new Set([id]);
    for (let d = 0; d < depth; d++) {
      const next = new Set<string>();
      for (const node of frontier) {
        for (const e of this.store.inEdges(node, 'calls')) {
          if (!visited.has(e.src)) {
            const n = this.store.nodes.get(e.src);
            if (n) callers.push(this.nodeDict(n));
            next.add(e.src); visited.add(e.src);
          }
        }
      }
      frontier = next;
    }
    return { symbol: id, depth, callers, count: callers.length };
  }

  private impact(symbolName: string, maxDepth: number) {
    const id = this.resolveId(symbolName);
    if (!id) return { symbol: symbolName, affected_symbol_count: 0, affected_files: [], affected_tests: [], blast_radius: [] };
    const affected = new Set<string>();
    const files = new Set<string>();
    const tests = new Set<string>();
    let frontier = new Set([id]);
    for (let d = 0; d < maxDepth; d++) {
      const next = new Set<string>();
      for (const node of frontier) {
        for (const e of this.store.inEdges(node)) {
          if (e.kind !== 'calls' && e.kind !== 'inherits') continue;
          if (!affected.has(e.src)) {
            affected.add(e.src); next.add(e.src);
            const n = this.store.nodes.get(e.src);
            if (n) { const f = n.file.replace('file:', ''); files.add(f); if (n.isTest || /test/i.test(f)) tests.add(f); }
          }
        }
      }
      frontier = next;
    }
    return {
      symbol: id, affected_symbol_count: affected.size,
      affected_files: [...files], affected_tests: [...tests], blast_radius: [...affected],
    };
  }

  private hotPaths(topN: number) {
    const rows = [...this.store.nodes.values()]
      .filter((n) => n.kind === 'function' || n.kind === 'file')
      .map((n) => ({
        node_id: n.id, name: n.name, kind: n.kind,
        file: n.file.replace('file:', ''),
        score: (n.pagerank ?? 0) + (n.commitCount ?? 0) * 0.01,
        commit_count: n.commitCount ?? 0, pagerank: n.pagerank ?? 0,
        complexity: n.complexity ?? 1,
      }))
      .sort((a, b) => b.score - a.score);
    return rows.slice(0, topN);
  }

  private search(query: string, limit: number) {
    const q = query.toLowerCase();
    const results = [...this.store.nodes.values()]
      .filter((n) => n.kind !== 'file' && (n.name.toLowerCase().includes(q) || (n.docstring ?? '').toLowerCase().includes(q)))
      .map((n) => ({ node_id: n.id, name: n.name, kind: n.kind, file: n.file.replace('file:', ''), line: n.lineStart ?? 0 }))
      .slice(0, limit);
    return { query, results, count: results.length };
  }

  private overview() {
    const s = this.store.stats();
    const langs: Record<string, number> = {};
    for (const n of this.store.nodes.values()) if (n.kind === 'file' && n.lang) langs[n.lang] = (langs[n.lang] ?? 0) + 1;
    return { files: s.files, functions: s.functions, classes: s.classes, test_files: s.tests, languages: langs, edge_count: s.edges };
  }

  private dependencies(fileId: string) {
    const direct = this.store.outEdges(fileId, 'imports').map((e) => e.dst.replace('file:', '').replace('module:', ''));
    return { file: fileId.replace('file:', ''), direct_deps: direct, transitive_deps: direct, dep_count: direct.length };
  }

  private todos(kind: string, limit: number) {
    const k = kind === 'all' ? null : kind.toUpperCase();
    return this.allTodos.filter((t) => !k || t.kind === k).slice(0, limit);
  }

  private publicApi(fileId: string | null) {
    const api = [...this.store.nodes.values()]
      .filter((n) => (n.kind === 'function' || n.kind === 'class') && !n.name.startsWith('_'))
      .filter((n) => !fileId || n.file === fileId)
      .map((n) => ({ node_id: n.id, name: n.name, kind: n.kind, file: n.file.replace('file:', ''), signature: n.signature ?? '' }));
    return { api: api.slice(0, 50), count: api.length };
  }

  private layers(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const n of this.store.nodes.values()) {
      if (n.kind !== 'file') continue;
      const p = n.path ?? '';
      let layer = 'unknown';
      if (n.isTest || /(^|\/)tests?\//.test(p)) layer = 'test';
      else if (/config|settings/i.test(p)) layer = 'config';
      else if (/(^|\/)(cli|mcp|api|server|web)/.test(p)) layer = 'presentation';
      else if (/(^|\/)(graph|store|db|data)/.test(p)) layer = 'data';
      (out[layer] ??= []).push(p);
    }
    return out;
  }
}

export function defaultWasmPaths(extensionRoot: string): NativeBackendOptions {
  // In the packaged extension, wasms are copied next to dist/. In dev, they
  // resolve from node_modules.
  const candidates = [
    path.join(extensionRoot, 'dist', 'wasm'),
    path.join(extensionRoot, 'node_modules', 'tree-sitter-wasms', 'out'),
  ];
  const wasmDir = candidates.find((c) => fs.existsSync(path.join(c, 'tree-sitter-python.wasm'))) ?? candidates[0];
  const coreCandidates = [
    path.join(extensionRoot, 'dist', 'wasm', 'tree-sitter.wasm'),
    path.join(extensionRoot, 'node_modules', 'web-tree-sitter', 'tree-sitter.wasm'),
  ];
  const coreWasmPath = coreCandidates.find((c) => fs.existsSync(c)) ?? coreCandidates[0];
  return { wasmDir, coreWasmPath };
}
