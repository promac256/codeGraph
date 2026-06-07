import * as vscode from 'vscode';
import { McpClient } from '../backend/mcp-client';
import { GraphWebviewPanel } from '../graph/webview';

let _debounceTimer: ReturnType<typeof setTimeout> | null = null;

export function registerDiagnosticsWatcher(
  context: vscode.ExtensionContext,
  client: McpClient,
): void {
  const watcher = vscode.languages.onDidChangeDiagnostics(async (e) => {
    const config = vscode.workspace.getConfiguration('codegraph');
    if (!config.get<boolean>('errorTracing', true)) return;
    if (!client.ready) return;

    // Debounce rapid diagnostic changes (e.g. while typing)
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => _processDiagnostics(e, client), 1200);
  });

  context.subscriptions.push(watcher);
}

async function _processDiagnostics(
  e: vscode.DiagnosticChangeEvent,
  client: McpClient,
): Promise<void> {
  const panel = GraphWebviewPanel.instance;

  // Collect all errors from changed URIs
  const errors: Array<{ uri: vscode.Uri; diag: vscode.Diagnostic }> = [];
  for (const uri of e.uris) {
    const diags = vscode.languages.getDiagnostics(uri);
    for (const d of diags) {
      if (d.severity === vscode.DiagnosticSeverity.Error) {
        errors.push({ uri, diag: d });
      }
    }
  }

  if (errors.length === 0) {
    panel?.clearHighlights();
    return;
  }

  // Use the first error for graph tracing
  const { uri, diag } = errors[0];
  const wsFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!wsFolder) return;

  // Get relative file path
  const relPath = vscode.workspace.asRelativePath(uri, false);

  try {
    // Search for symbols near the error location
    const errorMessage = diag.message;
    const firstWord = errorMessage.split(/[\s:(']+/)[0];

    // Try to find the symbol at the error line via search
    const searchResult = await client.call<Array<Record<string, unknown>>>(
      'codegraph_search',
      { query: firstWord, limit: 5 },
    );

    const symbolList = Array.isArray(searchResult) ? searchResult : [];

    // Find the symbol in this file
    const inFile = symbolList.find((s) => {
      const sFile = String(s['file'] || s['path'] || '');
      return sFile.includes(relPath) || relPath.includes(sFile.replace('file:', ''));
    });

    if (!inFile) {
      // Can't locate to a specific node — still show the panel with a file focus
      if (panel) {
        const fileId = `file:${relPath}`;
        panel.focusNode(fileId);
      }
      return;
    }

    const nodeId = inFile['node_id'] as string;

    // Get impact analysis (reverse BFS — what depends on this symbol)
    const impact = await client.call<{
      symbol: string;
      affected_symbol_count: number;
      affected_files: string[];
      affected_tests: string[];
      blast_radius: number;
    }>('codegraph_impact_analysis', { symbol_name: String(inFile['name'] ?? firstWord), max_depth: 3 });

    // Highlight in graph
    if (panel) {
      panel.highlightErrorPath(nodeId, [
        ...(impact.affected_tests ?? []),
        // Map affected files to node IDs
        ...(impact.affected_files ?? []).map((f) => `file:${f}`),
      ]);
    }
  } catch {
    // Silent failure — error tracing is best-effort
  }
}
