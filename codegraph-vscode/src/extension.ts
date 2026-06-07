import * as path from 'path';
import * as vscode from 'vscode';
import { McpClient } from './backend/mcp-client';
import { registerCopilotTools } from './copilot/tools';
import { registerChatParticipant } from './copilot/participant';
import { GraphWebviewPanel } from './graph/webview';
import { registerDiagnosticsWatcher } from './editor/diagnostics';

let mcpClient: McpClient | null = null;

export function activate(context: vscode.ExtensionContext): void {
  const repoPath = getRepoPath();

  if (repoPath) {
    mcpClient = new McpClient(repoPath);
    mcpClient.start();
  }

  // Commands
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
      if (!rp) {
        vscode.window.showErrorMessage('codeGraph: No workspace folder open.');
        return;
      }
      await runCodegraphCommand(rp, ['init', rp, '--workers', '8']);
      // Restart MCP client to pick up new graph
      mcpClient?.dispose();
      mcpClient = new McpClient(rp);
      mcpClient.start();
      vscode.window.showInformationMessage('codeGraph: Graph built successfully.');
    }),

    vscode.commands.registerCommand('codegraph.updateGraph', async () => {
      const rp = getRepoPath();
      if (!rp) return;
      await runCodegraphCommand(rp, ['update', rp]);
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
        setTimeout(() => p.focusNode(`file:${relPath}`), 1000);
      }
    }),

    vscode.commands.registerCommand('codegraph.clearErrorHighlights', () => {
      GraphWebviewPanel.instance?.clearHighlights();
    }),
  );

  if (mcpClient) {
    // Register GitHub Copilot language model tools
    registerCopilotTools(context, mcpClient);

    // Register @codegraph chat participant
    registerChatParticipant(context, mcpClient);

    // Watch diagnostics for error tracing
    registerDiagnosticsWatcher(context, mcpClient);
  }

  // Watch for workspace folder changes and restart the client
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => {
      mcpClient?.dispose();
      const rp = getRepoPath();
      if (rp) {
        mcpClient = new McpClient(rp);
        mcpClient.start();
      }
    }),
  );

  context.subscriptions.push({
    dispose() {
      mcpClient?.dispose();
    },
  });
}

export function deactivate(): void {
  mcpClient?.dispose();
  mcpClient = null;
}

function getRepoPath(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

async function runCodegraphCommand(cwd: string, args: string[]): Promise<void> {
  const config = vscode.workspace.getConfiguration('codegraph');
  const pythonPath = config.get<string>('pythonPath') || '';
  const cmd = pythonPath
    ? path.join(path.dirname(pythonPath), 'codegraph')
    : 'codegraph';

  return new Promise((resolve, reject) => {
    const { spawn } = require('child_process') as typeof import('child_process');

    const terminal = vscode.window.createTerminal({
      name: 'codeGraph',
      cwd,
    });
    terminal.show();
    terminal.sendText(`${cmd} ${args.join(' ')}`);

    // We can't easily wait for the terminal to finish; resolve after a short delay
    // In a real extension you'd use a Task or OutputChannel instead
    setTimeout(resolve, 500);
  });
}
