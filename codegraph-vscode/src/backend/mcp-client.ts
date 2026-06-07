/**
 * MCP client supporting two transports:
 *
 *  SSE  — connects to a running `codegraph serve --transport sse` instance.
 *         Both Claude Code desktop and this extension share the same server
 *         process and the same in-memory graph.
 *
 *  stdio — spawns a private `codegraph serve --transport stdio` subprocess.
 *          Used as a fallback when no SSE server is detected.
 *
 * Transport selection:
 *   'auto'  → probe the configured port; use SSE if reachable, stdio otherwise.
 *   'sse'   → always SSE (error if server not running).
 *   'stdio' → always spawn a subprocess.
 */

import * as http from 'http';
import * as path from 'path';
import { ChildProcess, spawn } from 'child_process';
import * as vscode from 'vscode';

export type Transport = 'auto' | 'sse' | 'stdio';

interface PendingCall {
  resolve: (r: unknown) => void;
  reject: (e: Error) => void;
}

export class McpClient {
  private readonly repoPath: string;
  private activeTransport: 'sse' | 'stdio' | null = null;
  private _ready = false;

  // stdio state
  private proc: ChildProcess | null = null;
  private stdioBuf = '';

  // SSE state
  private sseReq: http.ClientRequest | null = null;
  private sseBuf = '';
  private messageEndpoint: string | null = null;
  private ssePort = 8765;

  // shared
  private pending = new Map<number, PendingCall>();
  private nextId = 0;

  constructor(repoPath: string) {
    this.repoPath = repoPath;
  }

  get ready(): boolean {
    return this._ready;
  }

  get transport(): 'sse' | 'stdio' | null {
    return this.activeTransport;
  }

  // ---------------------------------------------------------------------------
  // Public: start
  // ---------------------------------------------------------------------------

  async start(): Promise<void> {
    const config = vscode.workspace.getConfiguration('codegraph');
    const pref = config.get<Transport>('transport', 'auto');
    this.ssePort = config.get<number>('ssePort', 8765);

    if (pref === 'sse') {
      await this._connectSse(this.ssePort);
      return;
    }

    if (pref === 'stdio') {
      this._startStdio();
      return;
    }

    // 'auto': probe SSE, fall back to stdio
    try {
      await this._probeSse(this.ssePort, 2000);
      await this._connectSse(this.ssePort);
    } catch {
      this._startStdio();
    }
  }

  // ---------------------------------------------------------------------------
  // Public: call
  // ---------------------------------------------------------------------------

  call<T = unknown>(toolName: string, args: Record<string, unknown> = {}): Promise<T> {
    if (!this._ready) {
      return Promise.reject(
        new Error('codeGraph MCP server not ready — run "codeGraph: Initialize / Rebuild Graph" first.'),
      );
    }

    const id = ++this.nextId;
    const payload = {
      jsonrpc: '2.0',
      id,
      method: 'tools/call',
      params: { name: toolName, arguments: args },
    };

    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: (r) => resolve(r as T),
        reject,
      });

      const timer = setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`MCP call '${toolName}' timed out`));
        }
      }, 30_000);

      const send =
        this.activeTransport === 'sse'
          ? this._sendSse(payload)
          : this._sendStdio(payload);

      send.catch((err) => {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(err);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Public: dispose
  // ---------------------------------------------------------------------------

  dispose(): void {
    this._ready = false;
    this.activeTransport = null;

    this.sseReq?.destroy();
    this.sseReq = null;

    if (this.proc && !this.proc.killed) {
      this.proc.kill('SIGTERM');
    }
    this.proc = null;

    for (const [, h] of this.pending) {
      h.reject(new Error('McpClient disposed'));
    }
    this.pending.clear();
  }

  // ---------------------------------------------------------------------------
  // SSE transport
  // ---------------------------------------------------------------------------

  private _probeSse(port: number, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { req.destroy(); reject(new Error('probe timeout')); }, timeoutMs);
      const req = http.get(`http://127.0.0.1:${port}/sse`, (res) => {
        clearTimeout(timer);
        res.destroy();
        // Any HTTP response means the server is up
        resolve();
      });
      req.on('error', (e) => { clearTimeout(timer); reject(e); });
    });
  }

  private _connectSse(port: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => { req.destroy(); reject(new Error('SSE connect timeout')); },
        5000,
      );

      let resolved = false;

      const req = http.get(`http://127.0.0.1:${port}/sse`, (res) => {
        if (res.statusCode !== 200) {
          clearTimeout(timer);
          reject(new Error(`SSE server returned HTTP ${res.statusCode}`));
          return;
        }

        res.setEncoding('utf8');
        this.sseBuf = '';

        res.on('data', (chunk: string) => {
          this.sseBuf += chunk;

          // Split on double-newline (SSE event boundary)
          const parts = this.sseBuf.split('\n\n');
          this.sseBuf = parts.pop()!;

          for (const part of parts) {
            const lines = part.trim().split('\n');
            let evtType = 'message';
            let evtData = '';

            for (const line of lines) {
              if (line.startsWith('event:')) evtType = line.slice(6).trim();
              else if (line.startsWith('data:')) evtData = line.slice(5).trim();
            }

            if (!evtData) continue;

            if (evtType === 'endpoint') {
              // Server sends the POST endpoint URL as the first event
              this.messageEndpoint = evtData;
              this.activeTransport = 'sse';
              this._ready = true;
              if (!resolved) {
                resolved = true;
                clearTimeout(timer);
                resolve();
              }
            } else {
              // JSON-RPC response
              this._handleJsonRpc(evtData);
            }
          }
        });

        res.on('error', (err) => {
          this._ready = false;
          this._rejectAllPending('SSE stream error');
          if (!resolved) { clearTimeout(timer); reject(err); }
        });

        res.on('end', () => {
          this._ready = false;
          this._rejectAllPending('SSE connection closed');
        });
      });

      req.on('error', (err) => {
        clearTimeout(timer);
        if (!resolved) reject(err);
      });

      this.sseReq = req;
    });
  }

  private async _sendSse(payload: object): Promise<void> {
    if (!this.messageEndpoint) throw new Error('No SSE message endpoint');

    const url = `http://127.0.0.1:${this.ssePort}${this.messageEndpoint}`;
    const body = JSON.stringify(payload);

    // fetch is available in Node 18+ (Electron ≥ 28 ships Node 18+)
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });

    if (!res.ok) {
      throw new Error(`SSE POST failed: ${res.status}`);
    }
  }

  // ---------------------------------------------------------------------------
  // stdio transport
  // ---------------------------------------------------------------------------

  private _startStdio(): void {
    const config = vscode.workspace.getConfiguration('codegraph');
    const pythonPath = config.get<string>('pythonPath') ?? '';
    const cmd = pythonPath
      ? path.join(path.dirname(pythonPath), 'codegraph')
      : 'codegraph';

    this.proc = spawn(cmd, ['serve', '--transport', 'stdio'], {
      cwd: this.repoPath,
      env: { ...process.env, CODEGRAPH_REPO_PATH: this.repoPath },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    this.proc.stdout!.setEncoding('utf8');
    this.proc.stdout!.on('data', (chunk: string) => {
      this.stdioBuf += chunk;
      const lines = this.stdioBuf.split('\n');
      this.stdioBuf = lines.pop()!;
      for (const line of lines) {
        if (line.trim()) this._handleJsonRpc(line);
      }
    });

    this.proc.stderr!.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      if (!this._ready && (text.includes('starting') || text.includes('MCP') || text.includes('stdio'))) {
        this.activeTransport = 'stdio';
        this._ready = true;
      }
    });

    this.proc.on('exit', () => {
      this._ready = false;
      this._rejectAllPending('stdio subprocess exited');
    });

    // Stdio servers are ready almost immediately; mark ready after brief startup window
    setTimeout(() => {
      if (!this._ready && this.proc && !this.proc.killed) {
        this.activeTransport = 'stdio';
        this._ready = true;
      }
    }, 1500);
  }

  private _sendStdio(payload: object): Promise<void> {
    return new Promise((resolve, reject) => {
      if (!this.proc?.stdin) {
        reject(new Error('stdio subprocess not running'));
        return;
      }
      this.proc.stdin.write(JSON.stringify(payload) + '\n', (err) => {
        if (err) reject(err); else resolve();
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Shared
  // ---------------------------------------------------------------------------

  private _handleJsonRpc(raw: string): void {
    try {
      const msg = JSON.parse(raw);
      if (msg.id == null) return;
      const handler = this.pending.get(msg.id);
      if (!handler) return;
      this.pending.delete(msg.id);
      if (msg.error) handler.reject(new Error(msg.error.message ?? 'MCP error'));
      else handler.resolve(msg.result);
    } catch {
      // unparseable line — ignore
    }
  }

  private _rejectAllPending(reason: string): void {
    for (const [, h] of this.pending) h.reject(new Error(reason));
    this.pending.clear();
  }
}
