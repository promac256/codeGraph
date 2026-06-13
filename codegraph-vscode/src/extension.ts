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

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  extensionPath = context.extensionPath;
  const repoPath = getRepoPath();
  if (repoPath) {
    mcpClient = await startClient(repoPath);
  }

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

  // Re-init client when workspace changes
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(async () => {
      mcpClient?.dispose();
      const rp = getRepoPath();
      mcpClient = rp ? await startClient(rp) : null;
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
    await client.start();
    const label =
      client.transport === 'native'
        ? 'codeGraph: native in-process backend ready (no Python required)'
        : client.transport === 'sse'
          ? `codeGraph: connected via SSE (shared server on port ${getPort()})`
          : 'codeGraph: started private MCP server (stdio)';
    vscode.window.setStatusBarMessage(label, 5000);
  } catch (err) {
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
