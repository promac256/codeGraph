import * as vscode from 'vscode';
import type { Backend } from '../native/types';

type ToolInput = Record<string, unknown>;
type InvokeOpts = vscode.LanguageModelToolInvocationOptions<ToolInput>;

function makeToolResult(content: unknown): vscode.LanguageModelToolResult {
  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  return new vscode.LanguageModelToolResult([new vscode.LanguageModelTextPart(text)]);
}

async function safecall(
  client: Backend,
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
  client: Backend,
): void {
  const tools: vscode.Disposable[] = [

    vscode.lm.registerTool<ToolInput>('codegraph_find_symbol', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_find_symbol', {
          name: input['symbol_name'] ?? input['name'],
          kind: input['kind'] ?? 'any',
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_find_callers', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_find_callers', {
          symbol_name: input['symbol_name'],
          depth: input['depth'] ?? 1,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_get_dependencies', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_get_dependencies', {
          file_path: input['file_path'],
          depth: input['depth'] ?? 2,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_recent_changes', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_recent_changes', {
          limit: input['limit'] ?? 10,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_hot_paths', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_hot_paths', {
          top_n: input['top_n'] ?? 20,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_test_coverage', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_test_coverage', {
          symbol_name: input['symbol_name'],
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_public_api', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_public_api', {
          file_path: input['file_path'] ?? null,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_todos', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_todos', {
          kind: input['kind'] ?? null,
          limit: input['limit'] ?? 50,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_search', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_search', {
          query: input['query'],
          limit: input['limit'] ?? 20,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_architectural_layers', {
      async invoke(_options: InvokeOpts, _token) {
        return safecall(client, 'codegraph_architectural_layers', {});
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_impact_analysis', {
      async invoke(options: InvokeOpts, _token) {
        const input = options.input;
        return safecall(client, 'codegraph_impact_analysis', {
          symbol_name: input['symbol_name'],
          max_depth: input['max_depth'] ?? 3,
        });
      },
    }),

    vscode.lm.registerTool<ToolInput>('codegraph_conventions', {
      async invoke(_options: InvokeOpts, _token) {
        return safecall(client, 'codegraph_conventions', {});
      },
    }),
  ];

  context.subscriptions.push(...tools);
}
