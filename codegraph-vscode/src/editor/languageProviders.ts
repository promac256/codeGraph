/**
 * Native editor integrations backed by the knowledge graph:
 *
 *  - DefinitionProvider  — Go to Definition via codegraph_find_symbol
 *  - ReferenceProvider   — Find References via codegraph_find_callers
 *  - CodeLensProvider    — "N callers" above each function (native backend)
 *
 * These put the graph inside flows developers already use, instead of
 * requiring a chat command.
 */
import * as vscode from 'vscode';
import { NativeBackend } from '../native/nativeBackend';
import type { Backend } from '../native/types';

const LANGUAGES = [
  'python', 'typescript', 'javascript', 'typescriptreact', 'javascriptreact',
  'go', 'rust', 'java', 'c', 'cpp',
];

interface SymbolMatch {
  file: string;
  line: number;
  name: string;
  kind: string;
}

interface CallerEntry {
  name?: string;
  file?: string;
  line_start?: number;
}

interface FileSymbol {
  name: string;
  qualified_name: string;
  kind: string;
  line_start: number;
  line_end: number;
}

function wordAt(doc: vscode.TextDocument, pos: vscode.Position): string | null {
  const range = doc.getWordRangeAtPosition(pos);
  return range ? doc.getText(range) : null;
}

function toLocation(repoRoot: vscode.Uri, relFile: string, line: number): vscode.Location {
  const uri = vscode.Uri.joinPath(repoRoot, relFile.replace(/^file:/, ''));
  const l = Math.max(0, (line || 1) - 1);
  return new vscode.Location(uri, new vscode.Position(l, 0));
}

export function registerLanguageProviders(
  context: vscode.ExtensionContext,
  getClient: () => Backend | null,
): void {
  const repoRoot = () => vscode.workspace.workspaceFolders?.[0]?.uri;
  const selector = LANGUAGES.map((language) => ({ language, scheme: 'file' as const }));

  // --- Go to Definition ------------------------------------------------------
  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(selector, {
      async provideDefinition(doc, pos) {
        const client = getClient();
        const root = repoRoot();
        if (!client?.ready || !root) return null;
        const word = wordAt(doc, pos);
        if (!word) return null;
        try {
          const res = await client.call<{ matches: SymbolMatch[] }>(
            'codegraph_find_symbol', { name: word, kind: 'any' },
          );
          return (res.matches ?? [])
            .filter((m) => m.file)
            .map((m) => toLocation(root, m.file, m.line));
        } catch {
          return null; // fall through to other definition providers
        }
      },
    }),
  );

  // --- Find References (callers) ---------------------------------------------
  context.subscriptions.push(
    vscode.languages.registerReferenceProvider(selector, {
      async provideReferences(doc, pos) {
        const client = getClient();
        const root = repoRoot();
        if (!client?.ready || !root) return null;
        const word = wordAt(doc, pos);
        if (!word) return null;
        try {
          const res = await client.call<{ callers: CallerEntry[] }>(
            'codegraph_find_callers', { symbol_name: word, depth: 1 },
          );
          return (res.callers ?? [])
            .filter((c) => c.file)
            .map((c) => toLocation(root, c.file!, c.line_start ?? 1));
        } catch {
          return null;
        }
      },
    }),
  );

  // --- CodeLens: caller counts above functions (native backend only — needs
  // the fast in-process codegraph_file_symbols lookup) -------------------------
  const lensEmitter = new vscode.EventEmitter<void>();
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(selector, {
      onDidChangeCodeLenses: lensEmitter.event,
      async provideCodeLenses(doc) {
        const client = getClient();
        const root = repoRoot();
        if (!(client instanceof NativeBackend) || !client.ready || !root) return [];
        const rel = vscode.workspace.asRelativePath(doc.uri, false).replace(/\\/g, '/');
        try {
          const res = await client.call<{ symbols: FileSymbol[] }>(
            'codegraph_file_symbols', { file_path: rel },
          );
          return (res.symbols ?? [])
            .filter((s) => s.kind === 'function')
            .map((s) => {
              const range = new vscode.Range(Math.max(0, s.line_start - 1), 0, Math.max(0, s.line_start - 1), 0);
              const lens = new vscode.CodeLens(range);
              (lens as vscode.CodeLens & { cgSymbol?: string }).cgSymbol = s.qualified_name;
              return lens;
            });
        } catch {
          return [];
        }
      },
      async resolveCodeLens(lens) {
        const client = getClient();
        const symbol = (lens as vscode.CodeLens & { cgSymbol?: string }).cgSymbol;
        if (!client?.ready || !symbol) {
          lens.command = { title: '', command: '' };
          return lens;
        }
        try {
          const res = await client.call<{ count: number }>(
            'codegraph_find_callers', { symbol_name: symbol.split('.').pop(), depth: 1 },
          );
          const n = res.count ?? 0;
          lens.command = {
            title: n === 1 ? '1 caller' : `${n} callers`,
            command: 'codegraph.showCallers',
            arguments: [symbol.split('.').pop()],
          };
        } catch {
          lens.command = { title: '', command: '' };
        }
        return lens;
      },
    }),
  );

  // Refresh lenses after the graph changes (saves re-index in native mode).
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(() => {
      setTimeout(() => lensEmitter.fire(), 800); // after the debounced re-index
    }),
  );

  // --- Caller quick-pick used by the CodeLens --------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand('codegraph.showCallers', async (symbolName: string) => {
      const client = getClient();
      const root = repoRoot();
      if (!client?.ready || !root) return;
      const res = await client.call<{ callers: CallerEntry[] }>(
        'codegraph_find_callers', { symbol_name: symbolName, depth: 1 },
      );
      const callers = (res.callers ?? []).filter((c) => c.file);
      if (callers.length === 0) {
        vscode.window.showInformationMessage(`codeGraph: no callers of ${symbolName} in the graph.`);
        return;
      }
      const pick = await vscode.window.showQuickPick(
        callers.map((c) => ({
          label: c.name ?? '(unknown)',
          description: `${String(c.file).replace(/^file:/, '')}:${c.line_start ?? 1}`,
          caller: c,
        })),
        { title: `Callers of ${symbolName}` },
      );
      if (pick) {
        const loc = toLocation(root, pick.caller.file!, pick.caller.line_start ?? 1);
        await vscode.window.showTextDocument(loc.uri, { selection: loc.range });
      }
    }),
  );
}
