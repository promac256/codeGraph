import * as vscode from 'vscode';
import type { Backend } from '../native/types';

const PARTICIPANT_ID = 'codegraph.assistant';

function md(text: string): vscode.MarkdownString {
  const m = new vscode.MarkdownString(text);
  m.isTrusted = true;
  return m;
}

async function safeCall(client: Backend, tool: string, args: Record<string, unknown>): Promise<unknown> {
  if (!client.ready) {
    throw new Error('codeGraph MCP server is not running. Use "codeGraph: Initialize / Rebuild Graph" first.');
  }
  return client.call(tool, args);
}

export function registerChatParticipant(
  context: vscode.ExtensionContext,
  client: Backend,
): void {
  const participant = vscode.chat.createChatParticipant(
    PARTICIPANT_ID,
    async (request, _chatContext, stream, _token) => {
      const cmd = request.command;
      const prompt = request.prompt.trim();

      try {
        switch (cmd) {
          case 'symbol': {
            const name = prompt || 'unknown';
            const result = await safeCall(client, 'codegraph_find_symbol', { name, kind: 'any' });
            stream.markdown(md(`**Definition of \`${name}\`**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'callers': {
            const name = prompt || 'unknown';
            const result = await safeCall(client, 'codegraph_find_callers', { symbol_name: name, depth: 2 });
            stream.markdown(md(`**Callers of \`${name}\`**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'impact': {
            const name = prompt || 'unknown';
            const result = await safeCall(client, 'codegraph_impact_analysis', { symbol_name: name, max_depth: 3 });
            stream.markdown(md(`**Impact analysis for \`${name}\`**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'deps': {
            const filePath = prompt;
            const result = await safeCall(client, 'codegraph_get_dependencies', { file_path: filePath, depth: 2 });
            stream.markdown(md(`**Dependencies of \`${filePath}\`**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'hotspots': {
            const result = await safeCall(client, 'codegraph_hot_paths', { top_n: 20 }) as { hot_paths?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>;
            const rows = Array.isArray(result) ? result : ((result as { hot_paths?: Array<Record<string, unknown>> }).hot_paths ?? []);
            let table = '**Hot Paths (PageRank × Commit Frequency)**\n\n| # | Name | Kind | File | Commits |\n|---|------|------|------|---------|\n';
            rows.forEach((r, i) => {
              const file = String(r['file'] || '').replace('file:', '');
              table += `| ${i + 1} | \`${r['name']}\` | ${r['kind']} | ${file} | ${r['commit_count']} |\n`;
            });
            stream.markdown(md(table));
            break;
          }

          case 'overview': {
            const result = await safeCall(client, 'codegraph_architectural_layers', {});
            const changes = await safeCall(client, 'codegraph_recent_changes', { limit: 5 });
            stream.markdown(md(
              `**Repository Architecture Overview**\n\n` +
              `**Layers:**\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\`\n\n` +
              `**Recent Changes:**\n\`\`\`json\n${JSON.stringify(changes, null, 2)}\n\`\`\``,
            ));
            break;
          }

          case 'search': {
            const result = await safeCall(client, 'codegraph_search', { query: prompt, limit: 20 });
            stream.markdown(md(`**Search results for \`${prompt}\`**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'todos': {
            const result = await safeCall(client, 'codegraph_todos', { limit: 50 });
            stream.markdown(md(`**Open TODOs & FIXMEs**\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\``));
            break;
          }

          case 'errors': {
            // Get diagnostics from active editor
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
              stream.markdown(md('No active editor to trace errors from.'));
              break;
            }
            const diags = vscode.languages.getDiagnostics(editor.document.uri)
              .filter(d => d.severity === vscode.DiagnosticSeverity.Error);
            if (diags.length === 0) {
              stream.markdown(md('No errors in the active file.'));
              break;
            }
            const firstError = diags[0];
            stream.markdown(md(`**Error at line ${firstError.range.start.line + 1}:** ${firstError.message}\n\n*Searching the graph for related symbols...*`));
            const searchResult = await safeCall(client, 'codegraph_search', {
              query: firstError.message.split(':')[0].trim(),
              limit: 5,
            });
            stream.markdown(md(`**Possibly related symbols:**\n\`\`\`json\n${JSON.stringify(searchResult, null, 2)}\n\`\`\``));
            break;
          }

          default: {
            // Natural language fallback — search the graph
            if (prompt) {
              const result = await safeCall(client, 'codegraph_search', { query: prompt, limit: 10 });
              stream.markdown(md(`I searched the codeGraph knowledge graph for **"${prompt}"**:\n\n\`\`\`json\n${JSON.stringify(result, null, 2)}\n\`\`\`\n\nTry a slash command for more specific queries: \`/symbol\`, \`/callers\`, \`/impact\`, \`/deps\`, \`/hotspots\`, \`/overview\`, \`/search\`, \`/todos\`, \`/errors\``));
            } else {
              stream.markdown(md(
                '**codeGraph Assistant** — available commands:\n\n' +
                '- `/symbol <name>` — find symbol definition\n' +
                '- `/callers <name>` — find callers of a function\n' +
                '- `/impact <name>` — blast radius analysis\n' +
                '- `/deps <file>` — show file dependencies\n' +
                '- `/hotspots` — high-churn complex files\n' +
                '- `/overview` — architectural layers + recent changes\n' +
                '- `/search <query>` — full-text search\n' +
                '- `/todos` — open TODOs and FIXMEs\n' +
                '- `/errors` — trace current editor errors in graph',
              ));
            }
          }
        }
      } catch (err) {
        stream.markdown(md(`**Error:** ${String(err)}`));
      }
    },
  );

  participant.iconPath = new vscode.ThemeIcon('type-hierarchy');
  context.subscriptions.push(participant);
}
