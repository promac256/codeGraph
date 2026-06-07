import { ChildProcess, spawn } from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';

interface JsonRpcRequest {
  jsonrpc: '2.0';
  id: number;
  method: string;
  params: unknown;
}

interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: number;
  result?: unknown;
  error?: { code: number; message: string };
}

export class McpClient {
  private proc: ChildProcess | null = null;
  private pending = new Map<number, { resolve: (r: unknown) => void; reject: (e: Error) => void }>();
  private nextId = 0;
  private buf = '';
  private repoPath: string;
  private _ready = false;

  constructor(repoPath: string) {
    this.repoPath = repoPath;
  }

  get ready(): boolean {
    return this._ready && this.proc !== null && !this.proc.killed;
  }

  start(): void {
    const config = vscode.workspace.getConfiguration('codegraph');
    const pythonPath = config.get<string>('pythonPath') || '';

    const cmd = pythonPath
      ? path.join(path.dirname(pythonPath), 'codegraph')
      : 'codegraph';

    this.proc = spawn(cmd, ['serve', '--transport', 'stdio'], {
      cwd: this.repoPath,
      env: { ...process.env, CODEGRAPH_REPO_PATH: this.repoPath },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    this.proc.stdout!.on('data', (chunk: Buffer) => {
      this.buf += chunk.toString();
      const lines = this.buf.split('\n');
      this.buf = lines.pop()!;
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const msg: JsonRpcResponse = JSON.parse(line);
          const handler = this.pending.get(msg.id);
          if (handler) {
            this.pending.delete(msg.id);
            if (msg.error) {
              handler.reject(new Error(msg.error.message));
            } else {
              handler.resolve(msg.result);
            }
          }
        } catch {
          // non-JSON line (e.g. startup log) — ignore
        }
      }
    });

    this.proc.stderr!.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      // Treat "starting" message as ready signal
      if (text.includes('starting') || text.includes('MCP')) {
        this._ready = true;
      }
    });

    this.proc.on('exit', () => {
      this._ready = false;
      // Reject all pending calls
      for (const [, h] of this.pending) {
        h.reject(new Error('MCP server process exited'));
      }
      this.pending.clear();
    });

    // Give the process a moment to start
    setTimeout(() => { this._ready = true; }, 1500);
  }

  async call<T = unknown>(toolName: string, args: Record<string, unknown> = {}): Promise<T> {
    if (!this.proc || this.proc.killed) {
      throw new Error('MCP client not started');
    }

    return new Promise<T>((resolve, reject) => {
      const id = ++this.nextId;
      this.pending.set(id, {
        resolve: (r) => resolve(r as T),
        reject,
      });

      const req: JsonRpcRequest = {
        jsonrpc: '2.0',
        id,
        method: 'tools/call',
        params: { name: toolName, arguments: args },
      };

      const line = JSON.stringify(req) + '\n';
      this.proc!.stdin!.write(line, (err) => {
        if (err) {
          this.pending.delete(id);
          reject(err);
        }
      });

      // Timeout after 30 seconds
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`MCP call '${toolName}' timed out`));
        }
      }, 30_000);
    });
  }

  dispose(): void {
    this._ready = false;
    if (this.proc && !this.proc.killed) {
      this.proc.kill('SIGTERM');
    }
    this.proc = null;
  }
}
