import * as path from 'path';
import * as vscode from 'vscode';
import { McpClient } from '../backend/mcp-client';

export class GraphWebviewPanel {
  private static _instance: GraphWebviewPanel | null = null;

  private readonly _panel: vscode.WebviewPanel;
  private readonly _extensionUri: vscode.Uri;
  private readonly _client: McpClient;
  private _disposables: vscode.Disposable[] = [];
  private _currentGraph: GraphData | null = null;

  static show(extensionUri: vscode.Uri, client: McpClient): GraphWebviewPanel {
    if (GraphWebviewPanel._instance) {
      GraphWebviewPanel._instance._panel.reveal(vscode.ViewColumn.Two);
      return GraphWebviewPanel._instance;
    }
    const instance = new GraphWebviewPanel(extensionUri, client);
    GraphWebviewPanel._instance = instance;
    return instance;
  }

  static get instance(): GraphWebviewPanel | null {
    return GraphWebviewPanel._instance;
  }

  private constructor(extensionUri: vscode.Uri, client: McpClient) {
    this._extensionUri = extensionUri;
    this._client = client;

    this._panel = vscode.window.createWebviewPanel(
      'codegraph.graph',
      'codeGraph — Knowledge Graph',
      vscode.ViewColumn.Two,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'dist')],
      },
    );

    this._panel.iconPath = new vscode.ThemeIcon('type-hierarchy');
    this._panel.webview.html = this._getHtml();

    this._panel.webview.onDidReceiveMessage(
      (msg) => this._handleMessage(msg),
      null,
      this._disposables,
    );

    this._panel.onDidDispose(
      () => this._dispose(),
      null,
      this._disposables,
    );

    // Load graph data once panel is ready
    setTimeout(() => this._loadGraph(), 500);
  }

  private async _loadGraph(): Promise<void> {
    if (!this._client.ready) {
      this._panel.webview.postMessage({ type: 'error', message: 'MCP server not ready. Run "codeGraph: Initialize / Rebuild Graph" first.' });
      return;
    }

    try {
      const [layers, hotPaths] = await Promise.all([
        this._client.call<Record<string, string[]>>('codegraph_architectural_layers', {}),
        this._client.call<Array<Record<string, unknown>>>('codegraph_hot_paths', { top_n: 100 }),
      ]);

      // Build Cytoscape graph data from layers + hot paths
      const nodes: CyNode[] = [];
      const edges: CyEdge[] = [];
      const nodeSet = new Set<string>();

      // Normalise: server returns paths without 'file:' prefix; we store them with it
      // so that IDs match what hotPaths returns in the 'file' field.
      const layerMap: Record<string, string[]> = {};
      for (const [layer, files] of Object.entries(layers as Record<string, string[]>)) {
        layerMap[layer] = files.map((f) => (f.startsWith('file:') ? f : `file:${f}`));
      }

      for (const [layer, fileIds] of Object.entries(layerMap)) {
        for (const fileId of fileIds.slice(0, 50)) {  // cap at 50 per layer
          const relPath = fileId.replace('file:', '');
          const name = relPath.split('/').pop() ?? relPath;
          nodes.push({
            data: { id: fileId, label: name, kind: 'file', layer, fullPath: relPath },
          });
          nodeSet.add(fileId);
        }
      }

      // Overlay hot path functions on top
      for (const hp of (hotPaths as Array<Record<string, unknown>>).slice(0, 80)) {
        const id = hp['node_id'] as string;
        if (!nodeSet.has(id)) {
          const rawFile = hp['file'] as string;
          const fileId = rawFile.startsWith('file:') ? rawFile : `file:${rawFile}`;
          const layer = this._layerOf(layerMap, fileId);
          nodes.push({
            data: {
              id,
              label: hp['name'] as string,
              kind: hp['kind'] as string,
              layer,
              pagerank: hp['pagerank'] as number,
              commits: hp['commit_count'] as number,
              complexity: hp['complexity'] as number,
              fullPath: fileId.replace('file:', ''),
            },
          });
          nodeSet.add(id);
          // Add edge from file to function
          if (fileId && nodeSet.has(fileId)) {
            edges.push({ data: { source: fileId, target: id, kind: 'defines' } });
          }
        }
      }

      this._currentGraph = { nodes, edges };
      this._panel.webview.postMessage({ type: 'loadGraph', nodes, edges });
    } catch (err) {
      this._panel.webview.postMessage({ type: 'error', message: String(err) });
    }
  }

  private _layerOf(layerMap: Record<string, string[]>, fileId: string): string {
    for (const [layer, files] of Object.entries(layerMap)) {
      if (files.includes(fileId)) return layer;
    }
    return 'unknown';
  }

  private async _handleMessage(msg: Record<string, unknown>): Promise<void> {
    switch (msg['type']) {
      case 'openFile': {
        const filePath = msg['filePath'] as string;
        const line = (msg['line'] as number) ?? 0;
        if (!filePath) return;

        const wsFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!wsFolder) return;

        const uri = vscode.Uri.file(path.join(wsFolder, filePath));
        const doc = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(doc, {
          viewColumn: vscode.ViewColumn.One,
          selection: new vscode.Range(Math.max(0, line - 1), 0, Math.max(0, line - 1), 0),
        });
        break;
      }

      case 'nodeHover': {
        // Could show hover info in status bar
        const label = msg['label'] as string;
        if (label) {
          vscode.window.setStatusBarMessage(`codeGraph: ${label}`, 3000);
        }
        break;
      }

      case 'requestRefresh': {
        await this._loadGraph();
        break;
      }
    }
  }

  highlightErrorPath(errorNodeId: string, ancestorIds: string[]): void {
    this._panel.reveal(vscode.ViewColumn.Two, true);
    this._panel.webview.postMessage({
      type: 'highlightErrorPath',
      errorNodeId,
      ancestorIds,
    });
  }

  focusNode(nodeId: string): void {
    this._panel.reveal(vscode.ViewColumn.Two, true);
    this._panel.webview.postMessage({ type: 'focusNode', nodeId });
  }

  clearHighlights(): void {
    this._panel.webview.postMessage({ type: 'clearHighlights' });
  }

  private _getHtml(): string {
    const webviewUri = this._panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'dist', 'webview.js'),
    );
    const nonce = getNonce();

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>codeGraph</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1117; color: #e0e0e0; font-family: var(--vscode-font-family, monospace); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  #toolbar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: #1a1d27; border-bottom: 1px solid #2a2d3e; flex-shrink: 0; }
  #toolbar input { background: #252838; border: 1px solid #3a3d52; color: #e0e0e0; padding: 4px 8px; border-radius: 4px; font-size: 12px; width: 200px; }
  #toolbar button { background: #2a2d3e; border: 1px solid #3a3d52; color: #e0e0e0; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; }
  #toolbar button:hover { background: #3a3d52; }
  #toolbar .spacer { flex: 1; }
  #toolbar .status { font-size: 11px; color: #8888aa; }
  #cy { flex: 1; width: 100%; }
  #tooltip { position: absolute; background: #1a1d27; border: 1px solid #3a3d52; border-radius: 6px; padding: 8px 10px; font-size: 11px; pointer-events: none; display: none; max-width: 280px; z-index: 100; line-height: 1.5; }
  #tooltip .t-name { font-weight: bold; color: #a0cfff; }
  #tooltip .t-kind { color: #8888aa; font-size: 10px; }
  #tooltip .t-file { color: #88ccaa; font-size: 10px; word-break: break-all; }
  #error-banner { display: none; background: #3d1a1a; border-bottom: 1px solid #ff4444; padding: 4px 10px; font-size: 11px; color: #ff8888; flex-shrink: 0; }
</style>
</head>
<body>
<div id="toolbar">
  <input id="search" type="text" placeholder="Filter nodes..." />
  <button id="btn-fit">Fit</button>
  <button id="btn-refresh">Refresh</button>
  <button id="btn-clear-errors">Clear Errors</button>
  <span class="spacer"></span>
  <span id="status" class="status">Loading...</span>
</div>
<div id="error-banner" id="err-banner"></div>
<div id="cy"></div>
<div id="tooltip"></div>
<script nonce="${nonce}" src="${webviewUri}"></script>
</body>
</html>`;
  }

  private _dispose(): void {
    GraphWebviewPanel._instance = null;
    for (const d of this._disposables) d.dispose();
    this._disposables = [];
  }
}

function getNonce(): string {
  let text = '';
  const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

interface CyNode {
  data: {
    id: string;
    label: string;
    kind: string;
    layer?: string;
    pagerank?: number;
    commits?: number;
    complexity?: number;
    fullPath?: string;
  };
}

interface CyEdge {
  data: {
    source: string;
    target: string;
    kind: string;
  };
}

interface GraphData {
  nodes: CyNode[];
  edges: CyEdge[];
}
