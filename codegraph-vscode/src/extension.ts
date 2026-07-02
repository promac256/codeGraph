import * as path from 'path';
import * as vscode from 'vscode';
import { McpClient } from './backend/mcp-client';
import { NativeBackend, defaultWasmPaths } from './native/nativeBackend';
import type { Backend } from './native/types';
import { registerCopilotTools } from './copilot/tools';
import { registerChatParticipant } from './copilot/participant';
import { GraphWebviewPanel } from './graph/webview';
import { registerDiagnosticsWatcher } from './editor/diagnostics';

let mcpClient: Backend | null = null;
let extensionPath = '';
let statusBar: vscode.StatusBarItem | null = null;

type GraphState = 'indexing' | 'ready' | 'error' | 'none';

function setStatus(state: GraphState, detail = ''): void {
  if (!statusBar) return;
  switch (state) {
    case 'indexing':
      statusBar.text = '$(sync~spin) codeGraph: indexing…';
      statusBar.tooltip = detail || 'Building the knowledge graph';
      statusBar.command = undefined;
      break;
    case 'ready':
      statusBar.text = `$(type-hierarchy) codeGraph${detail ? `: ${detail}` : ''}`;
      statusBar.tooltip = 'codeGraph ready — click to open the graph view';
      statusBar.command = 'codegraph.showGraph';
      break;
    case 'error':
      statusBar.text = '$(warning) codeGraph: error';
      statusBar.tooltip = `${detail}\nClick to rebuild the graph.`;
      statusBar.command = 'codegraph.initGraph';
      break;
    case 'none':
      statusBar.text = '$(type-hierarchy) codeGraph: no graph';
      statusBar.tooltip = 'Click to build the knowledge graph for this workspace';
      statusBar.command = 'codegraph.initGraph';
      break;
  }
  statusBar.show();
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  extensionPath = context.extensionPath;

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
  context.subscriptions.push(statusBar);

  if ((vscode.workspace.workspaceFolders?.length ?? 0) > 1) {
    const first = vscode.workspace.workspaceFolders![0].name;
    vscode.window.showInformationMessage(
      `codeGraph currently indexes the first workspace folder only ("${first}"). Multi-root support is planned.`,
    );
  }

  const repoPath = getRepoPath();
  if (repoPath) {
    mcpClient = await startClient(repoPath);
  } else {
    setStatus('none');
  }

  // Re-index a file in the native backend when it is saved, so the graph
  // never goes stale during an editing session. Debounced per-file.
  const pendingSaves = new Map<string, NodeJS.Timeout>();
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const backend = mcpClient;
      if (!(backend instanceof NativeBackend) || !backend.ready) return;
      const fsPath = doc.uri.fsPath;
      clearTimeout(pendingSaves.get(fsPath));
      pendingSaves.set(
        fsPath,
        setTimeout(async () => {
          pendingSaves.delete(fsPath);
          try {
            const changed = await backend.reindexFile(fsPath);
            if (changed) setStatus('ready', `${backend.symbolCount} symbols`);
          } catch (err) {
            console.error('codeGraph: reindex failed', err);
          }
        }, 400),
      );
    }),
  );

  // -------------------------------------------------------------------------
  // Commands
  // -------------------------------------------------------------------------

  context.subscriptions.push(
    vscode.commands.registerCommand('codegraph.showGraph', () => {
      if (!mcpClient) {
        vscode.window.showWarningMessage('codeGraph: No workspace folder open.');
        return;
      }
      GraphWebviewPanel.show(context.extensionUri, mcpClient);
    }),

    vscode.commands.registerCommand('codegraph.initGraph', async () => {
      const rp = getRepoPath();
      if (!rp) { vscode.window.showErrorMessage('codeGraph: No workspace folder open.'); return; }
      const native = vscode.workspace.getConfiguration('codegraph').get<string>('backend', 'python') === 'native';
      // Native backend builds in-memory on start; Python backend needs the CLI.
      if (!native) await runCodegraphInTerminal(rp, ['init', rp, '--workers', '8']);
      mcpClient?.dispose();
      mcpClient = await startClient(rp);
      vscode.window.showInformationMessage('codeGraph: Graph built successfully.');
    }),

    vscode.commands.registerCommand('codegraph.updateGraph', async () => {
      const rp = getRepoPath();
      if (!rp) return;
      await runCodegraphInTerminal(rp, ['update', rp]);
      vscode.window.showInformationMessage('codeGraph: Graph updated.');
    }),

    vscode.commands.registerCommand('codegraph.focusNode', () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const relPath = vscode.workspace.asRelativePath(editor.document.uri, false);
      const panel = GraphWebviewPanel.instance;
      if (panel) {
        panel.focusNode(`file:${relPath}`);
      } else if (mcpClient) {
        const p = GraphWebviewPanel.show(context.extensionUri, mcpClient);
        setTimeout(() => p.focusNode(`file:${relPath}`), 1200);
      }
    }),

    vscode.commands.registerCommand('codegraph.clearErrorHighlights', () => {
      GraphWebviewPanel.instance?.clearHighlights();
    }),
  );

  // -------------------------------------------------------------------------
  // Copilot + diagnostics
  // -------------------------------------------------------------------------

  if (mcpClient) {
    registerCopilotTools(context, mcpClient);
    registerChatParticipant(context, mcpClient);
    registerDiagnosticsWatcher(context, mcpClient);
  }

  // Re-init client when workspace changes (debounced — rapid folder churn
  // previously spawned several stdio subprocesses before any was disposed)
  let workspaceChangeTimer: NodeJS.Timeout | undefined;
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      clearTimeout(workspaceChangeTimer);
      workspaceChangeTimer = setTimeout(async () => {
        mcpClient?.dispose();
        const rp = getRepoPath();
        mcpClient = rp ? await startClient(rp) : null;
        if (!rp) setStatus('none');
      }, 500);
    }),
  );

  context.subscriptions.push({ dispose() { mcpClient?.dispose(); } });
}

export function deactivate(): void {
  mcpClient?.dispose();
  mcpClient = null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function startClient(repoPath: string): Promise<Backend> {
  const mode = vscode.workspace.getConfiguration('codegraph').get<string>('backend', 'python');
  const client: Backend = mode === 'native'
    ? new NativeBackend(repoPath, defaultWasmPaths(extensionPath))
    : new McpClient(repoPath);
  try {
    setStatus('indexing');
    if (client instanceof NativeBackend) {
      // Index with a cancellable progress notification so a large repo
      // never looks like a hung window.
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Window,
          title: 'codeGraph: indexing',
          cancellable: true,
        },
        (progress, token) =>
          client.start({
            report: (done, total) =>
              progress.report({ message: `${done}/${total} files` }),
            isCancelled: () => token.isCancellationRequested,
          }),
      );
      if (!client.ready) {
        setStatus('none', 'indexing cancelled');
        return client;
      }
      setStatus('ready', `${client.symbolCount} symbols`);
    } else {
      await client.start();
      const label =
        client.transport === 'sse'
          ? `SSE :${getPort()}`
          : 'stdio';
      setStatus('ready', label);
    }
  } catch (err) {
    setStatus('error', String(err));
    vscode.window.showWarningMessage(`codeGraph: backend failed to start — ${err}. Run "codeGraph: Initialize / Rebuild Graph".`);
  }
  return client;
}

function getRepoPath(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function getPort(): number {
  return vscode.workspace.getConfiguration('codegraph').get<number>('ssePort', 8765);
}

async function runCodegraphInTerminal(cwd: string, args: string[]): Promise<void> {
  const config = vscode.workspace.getConfiguration('codegraph');
  const pythonPath = config.get<string>('pythonPath') ?? '';
  const cmd = pythonPath
    ? path.join(path.dirname(pythonPath), 'codegraph')
    : 'codegraph';

  const terminal = vscode.window.createTerminal({ name: 'codeGraph', cwd });
  terminal.show();
  terminal.sendText(`${cmd} ${args.join(' ')}`);
  // Give the terminal command time to start before returning
  await new Promise<void>((r) => setTimeout(r, 400));
}
