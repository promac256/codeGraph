/**
 * In-memory knowledge graph: ingestion, cross-file call resolution, PageRank,
 * and git churn. Ports the resolution and metric logic from
 * codegraph/graph/builder.py (self/attr scoping, same-file preference,
 * structural PageRank fallback, dependency-free power iteration).
 */
import { execFileSync } from 'child_process';
import type { GEdge, GNode, ParseResult } from './types';

export class GraphStore {
  readonly nodes = new Map<string, GNode>();
  private edges: GEdge[] = [];
  private outIdx = new Map<string, GEdge[]>();
  private inIdx = new Map<string, GEdge[]>();
  /** Raw (unresolved) parse results per file. Resolution is a pure derivation
   *  over these, which makes single-file re-indexing correct by construction:
   *  replace one file's raw entry, re-derive, done. */
  private rawByFile = new Map<string, ParseResult>();

  ingest(results: ParseResult[]): void {
    for (const r of results) this.addFile(r);
  }

  /** Add or replace a single file's parse result (call resolveCrossReferences after). */
  addFile(result: ParseResult): void {
    this.rawByFile.set(result.fileNode.id, result);
  }

  /** Remove a file (deleted on disk). Call resolveCrossReferences after. */
  removeFile(fileId: string): boolean {
    return this.rawByFile.delete(fileId);
  }

  get fileCount(): number {
    return this.rawByFile.size;
  }

  /** Rebuild the node map and resolve class:?:: / func:?:: placeholder edge
   *  targets to real nodes — a pure derivation from rawByFile. */
  resolveCrossReferences(): void {
    this.nodes.clear();
    const rawEdges: GEdge[] = [];
    for (const r of this.rawByFile.values()) {
      this.nodes.set(r.fileNode.id, r.fileNode);
      for (const n of r.nodes) this.nodes.set(n.id, n);
      rawEdges.push(...r.edges);
    }

    const classIndex = new Map<string, string[]>();
    const funcIndex = new Map<string, string[]>();
    for (const n of this.nodes.values()) {
      if (!n.name) continue;
      const idx = n.kind === 'class' ? classIndex : n.kind === 'function' ? funcIndex : null;
      if (idx) (idx.get(n.name) ?? idx.set(n.name, []).get(n.name)!).push(n.id);
    }

    const resolved: GEdge[] = [];
    for (const e of rawEdges) {
      if (typeof e.dst !== 'string' || !e.dst.startsWith('class:?::') && !e.dst.startsWith('func:?::')) {
        resolved.push(e);
        continue;
      }
      if (e.dst.startsWith('class:?::')) {
        const cands = classIndex.get(e.dst.slice('class:?::'.length)) ?? [];
        if (cands.length === 1) resolved.push({ ...e, dst: cands[0], meta: { ...e.meta, resolved: true } });
        // ambiguous inherits: leave dropped (matches Python keeping only unique)
        continue;
      }
      // func:?::
      const callee = e.dst.slice('func:?::'.length);
      const chosen = this.pickCallTarget(e.src, callee, funcIndex.get(callee) ?? [], !!e.meta?.self_call);
      if (chosen && chosen !== e.src) {
        resolved.push({ ...e, dst: chosen, meta: { ...e.meta, resolved: true } });
      }
      // else: drop unresolvable call placeholder (no phantom node)
    }
    this.edges = resolved;
    this.buildIndices();
  }

  private pickCallTarget(srcId: string, callee: string, candidates: string[], selfCall: boolean): string | null {
    if (selfCall && srcId.startsWith('func:')) {
      const body = srcId.slice('func:'.length);
      const sep = body.indexOf('::');
      if (sep >= 0) {
        const rel = body.slice(0, sep);
        const qualified = body.slice(sep + 2);
        const dot = qualified.lastIndexOf('.');
        if (dot >= 0) {
          const owner = qualified.slice(0, dot);
          const cand = `func:${rel}::${owner}.${callee}`;
          if (cand !== srcId && this.nodes.has(cand)) return cand;
        }
      }
    }
    if (candidates.length === 0) return null;
    if (candidates.length === 1) return candidates[0];
    if (srcId.startsWith('func:')) {
      const srcRel = srcId.slice('func:'.length).split('::')[0];
      const sameFile = candidates.filter((c) => c.startsWith('func:') && c.slice('func:'.length).split('::')[0] === srcRel);
      if (sameFile.length === 1) return sameFile[0];
    }
    return null;
  }

  private buildIndices(): void {
    this.outIdx.clear();
    this.inIdx.clear();
    for (const e of this.edges) {
      (this.outIdx.get(e.src) ?? this.outIdx.set(e.src, []).get(e.src)!).push(e);
      (this.inIdx.get(e.dst) ?? this.inIdx.set(e.dst, []).get(e.dst)!).push(e);
    }
  }

  outEdges(id: string, kind?: GEdge['kind']): GEdge[] {
    const all = this.outIdx.get(id) ?? [];
    return kind ? all.filter((e) => e.kind === kind) : all;
  }

  inEdges(id: string, kind?: GEdge['kind']): GEdge[] {
    const all = this.inIdx.get(id) ?? [];
    return kind ? all.filter((e) => e.kind === kind) : all;
  }

  /** PageRank over the call graph, falling back to the structural graph. */
  computePageRank(): void {
    let rankEdges = this.edges.filter((e) => e.kind === 'calls').map((e) => [e.src, e.dst] as [string, string]);
    if (rankEdges.length === 0) {
      rankEdges = this.edges
        .filter((e) => e.kind === 'imports' || e.kind === 'inherits' || e.kind === 'defines')
        .map((e) => [e.src, e.dst] as [string, string]);
    }
    if (rankEdges.length === 0) return;
    const pr = pagerankPowerIteration(rankEdges);
    for (const [id, score] of pr) {
      const n = this.nodes.get(id);
      if (n) n.pagerank = score;
    }
  }

  /** Per-file commit counts from git history -> commitCount on file nodes. */
  ingestGitChurn(repoRoot: string, maxCommits = 1000): number {
    let out = '';
    try {
      out = execFileSync('git', ['log', `-${maxCommits}`, '--name-only', '--pretty=format:%H'], {
        cwd: repoRoot, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
      });
    } catch {
      return 0;
    }
    const counts = new Map<string, number>();
    let commits = 0;
    for (const line of out.split('\n')) {
      const t = line.trim();
      if (!t) continue;
      if (/^[0-9a-f]{40}$/.test(t)) { commits++; continue; }
      const fileId = `file:${t}`;
      if (this.nodes.has(fileId)) counts.set(fileId, (counts.get(fileId) ?? 0) + 1);
    }
    for (const [fileId, n] of counts) {
      const node = this.nodes.get(fileId);
      if (node) node.commitCount = n;
    }
    return commits;
  }

  stats() {
    let files = 0, functions = 0, classes = 0, types = 0, tests = 0;
    for (const n of this.nodes.values()) {
      if (n.kind === 'file') { files++; if (n.isTest) tests++; }
      else if (n.kind === 'function') functions++;
      else if (n.kind === 'class') classes++;
      else if (n.kind === 'type') types++;
    }
    return { files, functions, classes, types, tests, edges: this.edges.length };
  }
}

/** Pure power-iteration PageRank (no deps); mirrors builder._pagerank_power_iteration. */
export function pagerankPowerIteration(
  edges: Array<[string, string]>, alpha = 0.85, maxIter = 100, tol = 1e-6,
): Map<string, number> {
  const out = new Map<string, string[]>();
  const nodes = new Set<string>();
  for (const [s, d] of edges) {
    (out.get(s) ?? out.set(s, []).get(s)!).push(d);
    nodes.add(s); nodes.add(d);
  }
  const n = nodes.size;
  const rank = new Map<string, number>();
  if (n === 0) return rank;
  for (const node of nodes) rank.set(node, 1 / n);
  const dangling = [...nodes].filter((node) => !out.has(node));
  const base = (1 - alpha) / n;

  for (let iter = 0; iter < maxIter; iter++) {
    const prev = new Map(rank);
    let danglingMass = 0;
    for (const node of dangling) danglingMass += prev.get(node)!;
    danglingMass = (alpha * danglingMass) / n;
    for (const node of nodes) rank.set(node, base + danglingMass);
    for (const [s, dsts] of out) {
      const share = (alpha * prev.get(s)!) / dsts.length;
      for (const d of dsts) rank.set(d, rank.get(d)! + share);
    }
    let err = 0;
    for (const node of nodes) err += Math.abs(rank.get(node)! - prev.get(node)!);
    if (err < tol) break;
  }
  return rank;
}
