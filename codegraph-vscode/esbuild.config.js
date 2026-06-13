const esbuild = require('esbuild');
const fs = require('fs');
const path = require('path');

const watch = process.argv.includes('--watch');

// Copy the WASM runtime + grammars the native backend loads at runtime into
// dist/wasm so they ship inside the .vsix.
function copyWasm() {
  const outDir = path.join(__dirname, 'dist', 'wasm');
  fs.mkdirSync(outDir, { recursive: true });
  const core = path.join(__dirname, 'node_modules', 'web-tree-sitter', 'tree-sitter.wasm');
  if (fs.existsSync(core)) fs.copyFileSync(core, path.join(outDir, 'tree-sitter.wasm'));
  const grammarsDir = path.join(__dirname, 'node_modules', 'tree-sitter-wasms', 'out');
  const grammars = ['python']; // spike: Python; add languages here as parsers land
  for (const g of grammars) {
    const src = path.join(grammarsDir, `tree-sitter-${g}.wasm`);
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(outDir, `tree-sitter-${g}.wasm`));
  }
}

const extensionConfig = {
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'dist/extension.js',
  external: ['vscode'],
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  sourcemap: true,
  minify: !watch,
};

const webviewConfig = {
  entryPoints: ['webview/graph.ts'],
  bundle: true,
  outfile: 'dist/webview.js',
  format: 'iife',
  platform: 'browser',
  target: 'es2022',
  sourcemap: true,
  minify: !watch,
  // 3d-force-graph uses some Node-style globals in rare paths; stub them
  define: {
    'process.env.NODE_ENV': '"production"',
  },
};

async function build() {
  copyWasm();
  if (watch) {
    const extCtx = await esbuild.context(extensionConfig);
    const webCtx = await esbuild.context(webviewConfig);
    await Promise.all([extCtx.watch(), webCtx.watch()]);
    console.log('Watching for changes...');
  } else {
    await Promise.all([
      esbuild.build(extensionConfig),
      esbuild.build(webviewConfig),
    ]);
    console.log('Build complete.');
  }
}

build().catch(() => process.exit(1));
