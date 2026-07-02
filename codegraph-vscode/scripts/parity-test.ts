/**
 * Golden parity test — the native backend's contract with the Python backend.
 *
 * Indexes tests/fixtures with the Node/WASM backend and asserts the shared
 * invariants in tests/parity_golden.json. The Python side of the same
 * contract is tests/test_parity.py (pytest).
 *
 * Run: npm run parity
 */
import * as fs from 'fs';
import * as path from 'path';
import { NativeBackend, defaultWasmPaths } from '../src/native/nativeBackend';

interface Golden {
  file_count: number;
  python_models_functions: string[];
  animal_class_files: string[];
}

function assertEqual(label: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    console.error(`PARITY FAIL ${label}\n  expected: ${e}\n  actual:   ${a}`);
    process.exitCode = 1;
  } else {
    console.log(`PARITY OK   ${label}`);
  }
}

async function main() {
  const repoRoot = path.resolve(__dirname, '..', '..');
  const golden: Golden = JSON.parse(
    fs.readFileSync(path.join(repoRoot, 'tests', 'parity_golden.json'), 'utf8'),
  );
  const fixtures = path.join(repoRoot, 'tests', 'fixtures');
  const backend = new NativeBackend(fixtures, defaultWasmPaths(path.join(__dirname, '..')));
  await backend.start();

  const ov = (await backend.call('codegraph_overview')) as { files: number };
  assertEqual('file_count', ov.files, golden.file_count);

  const syms = (await backend.call('codegraph_file_symbols', {
    file_path: 'python_sample/models.py',
  })) as { symbols: Array<{ kind: string; qualified_name: string }> };
  assertEqual(
    'python_models_functions',
    syms.symbols.filter((s) => s.kind === 'function').map((s) => s.qualified_name).sort(),
    golden.python_models_functions,
  );

  const animal = (await backend.call('codegraph_find_symbol', {
    name: 'Animal', kind: 'class',
  })) as { matches: Array<{ file: string }> };
  assertEqual(
    'animal_class_files',
    [...new Set(animal.matches.map((m) => m.file))].sort(),
    golden.animal_class_files,
  );

  if (process.exitCode) {
    console.error('\nParity broken — fix the divergence or update tests/parity_golden.json deliberately (and re-run pytest tests/test_parity.py).');
  } else {
    console.log('\nAll parity invariants hold.');
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
