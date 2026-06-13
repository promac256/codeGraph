/**
 * Shared types for the in-process (Node/WASM) backend — a Python-free
 * alternative to the codegraph CLI for the VS Code extension.
 *
 * The backend mirrors the Python MCP tools' return shapes so it is a drop-in
 * replacement for McpClient (see Backend below).
 */

export type NodeKind = 'file' | 'function' | 'class' | 'type';
export type EdgeKind = 'defines' | 'imports' | 'calls' | 'inherits';
export type CallScope = 'free' | 'self' | 'attr';

export interface GNode {
  id: string;
  kind: NodeKind;
  name: string;
  file: string; // owning "file:<rel>" id (for symbols); own id for file nodes
  path?: string; // relative path (file nodes)
  lang?: string;
  lineStart?: number;
  lineEnd?: number;
  signature?: string;
  docstring?: string;
  qualifiedName?: string;
  bases?: string[];
  isTest?: boolean;
  isExported?: boolean;
  pagerank?: number;
  commitCount?: number;
  complexity?: number;
}

export interface GEdge {
  src: string;
  dst: string;
  kind: EdgeKind;
  meta?: Record<string, unknown>;
}

export interface CallSite {
  callee: string;
  line: number;
  scope: CallScope;
}

export interface ParseResult {
  fileNode: GNode;
  nodes: GNode[]; // functions, classes, types
  edges: GEdge[]; // defines, imports, inherits, and unresolved call placeholders
  todos: Array<{ line: number; kind: string; text: string }>;
}

/**
 * Common surface shared by McpClient (Python subprocess/SSE) and NativeBackend
 * (in-process Node). The extension depends only on this.
 */
export interface Backend {
  start(): Promise<void>;
  readonly ready: boolean;
  readonly transport: string | null;
  call<T = unknown>(toolName: string, args?: Record<string, unknown>): Promise<T>;
  dispose(): void;
}
