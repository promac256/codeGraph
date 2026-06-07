import * as vscode from 'vscode';
import { McpClient } from '../backend/mcp-client';

type ToolInput = Record<string, unknown>;

function makeToolResult(content: unknown): vscode.LanguageModelToolResult {
  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(text)]);
}

async function safecall(
  client: McpClient,
  tool: string,
  args: ToolInput,
): Promise<vscode.LanguageModelToolResult> {
  if (!client.ready) {
    return makeToolResult({ error: 'codeGraph MCP server is not running. Run "codeGraph: Initialize / Rebuild Graph" first.' });
  }
  try {
    const result = await client.call(tool, args);
    return makeToolResult(result);
  } catch (err) {
    return makeToolResult({ error: String(err) });
  }
}

export function registerCopilotTools(
  context: vscode.ExtensionContext,
  client: McpClient,
): void {
  const tools: vscode.Disposable[] = [

    vscode.lm.registerTool('codegraph_find_symbol', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_find_symbol', {
          symbol_name: input['symbol_name'],
          kind: input['kind'] ?? 'any',
        });
      },
    }),

    vscode.lm.registerTool('codegraph_find_callers', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_find_callers', {
          symbol_name: input['symbol_name'],
          depth: input['depth'] ?? 1,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_get_dependencies', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_get_dependencies', {
          file_path: input['file_path'],
          depth: input['depth'] ?? 2,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_recent_changes', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_recent_changes', {
          limit: input['limit'] ?? 10,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_hot_paths', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_hot_paths', {
          top_n: input['top_n'] ?? 20,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_test_coverage', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_test_coverage', {
          symbol_name: input['symbol_name'],
        });
      },
    }),

    vscode.lm.registerTool('codegraph_public_api', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_public_api', {
          file_path: input['file_path'] ?? null,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_todos', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_todos', {
          kind: input['kind'] ?? null,
          limit: input['limit'] ?? 50,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_search', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_search', {
          query: input['query'],
          limit: input['limit'] ?? 20,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_architectural_layers', {
      async invoke(_input: ToolInput, _token) {
        return safecall(client, 'codegraph_architectural_layers', {});
      },
    }),

    vscode.lm.registerTool('codegraph_impact_analysis', {
      async invoke(input: ToolInput, _token) {
        return safecall(client, 'codegraph_impact_analysis', {
          symbol_name: input['symbol_name'],
          max_depth: input['max_depth'] ?? 3,
        });
      },
    }),

    vscode.lm.registerTool('codegraph_conventions', {
      async invoke(_input: ToolInput, _token) {
        return safecall(client, 'codegraph_conventions', {});
      },
    }),
  ];

  context.subscriptions.push(...tools);
}
