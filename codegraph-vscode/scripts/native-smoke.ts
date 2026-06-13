/**
 * Standalone validation of the native (Node/WASM) backend — no VS Code host.
 * Indexes a repo and exercises the read tools, printing results.
 *
 * Usage: node dist/native-smoke.cjs [repoPath]
 */
import * as path from 'path';
import { NativeBackend, defaultWasmPaths } from '../src/native/nativeBackend';

async function main() {
  const repo = path.resolve(process.argv[2] ?? path.join(__dirname, '..', '..'));
  const extRoot = path.join(__dirname, '..'); // codegraph-vscode/
  const backend = new NativeBackend(repo, defaultWasmPaths(extRoot));

  const t0 = Date.now();
  await backend.start();
  const ms = Date.now() - t0;

  const ov = await backend.call('codegraph_overview');
  console.log(`indexed ${repo} in ${ms}ms`);
  console.log('overview:', JSON.stringify(ov));

  const hp = (await backend.call('codegraph_hot_paths', { top_n: 5 })) as any;
  console.log('hot_paths top5:', hp.hot_paths.map((r: any) => `${r.name}(${r.commit_count})`).join(', '));

  const fc = (await backend.call('codegraph_find_callers', { symbol_name: 'upsert_node', depth: 1 })) as any;
  console.log(`find_callers(upsert_node): ${fc.count} ->`, fc.callers.map((c: any) => c.name).slice(0, 6).join(', '));

  const ia = (await backend.call('codegraph_impact_analysis', { symbol_name: 'upsert_node' })) as any;
  console.log(`impact(upsert_node): ${ia.affected_symbol_count} symbols, ${ia.affected_files.length} files`);

  const sr = (await backend.call('codegraph_search', { query: 'parser', limit: 5 })) as any;
  console.log(`search('parser'): ${sr.count} hits ->`, sr.results.map((r: any) => r.name).slice(0, 5).join(', '));

  const fs2 = (await backend.call('codegraph_find_symbol', { name: 'GraphStore' })) as any;
  console.log(`find_symbol(GraphStore): ${fs2.count} ->`, fs2.matches.map((m: any) => `${m.file}:${m.line}`).join(', '));

  // sanity: the exists() stdlib-collision must NOT have phantom callers
  const ec = (await backend.call('codegraph_find_callers', { symbol_name: 'exists' })) as any;
  console.log(`find_callers(exists): ${ec.count} (should be small — builtin suppression)`);
}

main().catch((e) => { console.error('SMOKE FAILED:', e); process.exit(1); });
