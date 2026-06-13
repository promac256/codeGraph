/**
 * Config-driven parser for the non-Python languages. A LangSpec declares the
 * tree-sitter node types for classes, functions, and calls plus a per-language
 * callee extractor; the engine handles span-based method qualification, call
 * attribution, builtin suppression, and todos — mirroring the Python parsers.
 */
import type Parser from 'web-tree-sitter';
import { BUILTIN_METHODS } from './builtins';
import type { CallScope, GEdge, GNode, ParseResult } from './types';

type TSNode = Parser.SyntaxNode;
type Tree = Parser.Tree;

export interface ClassType {
  type: string;
  nameField?: string; // default 'name' (Rust impl uses 'type')
}

export interface LangSpec {
  lang: string;
  grammarWasm: string;
  extensions: string[];
  classTypes: ClassType[];
  functionTypes: string[];
  callTypes: string[];
  /** Extract a call's [calleeName, scope] from a call node. */
  calleeInfo(call: TSNode): [string | null, CallScope];
  /** Override function name/owner (Go receivers, C declarators). */
  funcInfo?(fn: TSNode): { name: string; owner?: string } | null;
  /** Emit import edges. */
  imports?(root: TSNode, fileId: string, edges: GEdge[]): void;
  /** Test-file detector. */
  testRe?: RegExp;
}

const TODO_RE = /(?:#|\/\/|\/\*)\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)/i;

export function* iterType(node: TSNode, type: string): Generator<TSNode> {
  if (node.type === type) yield node;
  for (const child of node.children) if (child) yield* iterType(child, type);
}

export function firstDescendant(node: TSNode, type: string): TSNode | null {
  for (const n of iterType(node, type)) return n;
  return null;
}

export function parseGeneric(spec: LangSpec, tree: Tree, rel: string, source: string): ParseResult {
  const fileId = `file:${rel}`;
  const lines = source.split(/\r?\n/);
  const isTest = spec.testRe ? spec.testRe.test(rel) : false;
  const fileNode: GNode = {
    id: fileId, kind: 'file', name: rel, file: fileId, path: rel,
    lang: spec.lang, lineStart: 1, lineEnd: lines.length, isTest,
  };
  const nodes: GNode[] = [];
  const edges: GEdge[] = [];
  const root = tree.rootNode;

  // --- classes (and class-like containers used for method qualification) ---
  const classSpans: Array<{ name: string; start: number; end: number }> = [];
  for (const ct of spec.classTypes) {
    for (const node of iterType(root, ct.type)) {
      const nameNode = node.childForFieldName(ct.nameField ?? 'name');
      const name = nameNode?.text;
      if (!name) continue;
      const start = node.startPosition.row + 1;
      const end = node.endPosition.row + 1;
      const classId = `class:${rel}::${name}`;
      if (!nodes.some((n) => n.id === classId)) {
        nodes.push({ id: classId, kind: 'class', name, file: fileId, lineStart: start, lineEnd: end });
        edges.push({ src: fileId, dst: classId, kind: 'defines', meta: { line: start } });
      }
      classSpans.push({ name, start, end });
    }
  }

  const enclosingClass = (line: number): string | null => {
    let best: { name: string; size: number } | null = null;
    for (const s of classSpans) {
      if (s.start <= line && line <= s.end) {
        const size = s.end - s.start;
        if (!best || size < best.size) best = { name: s.name, size };
      }
    }
    return best?.name ?? null;
  };

  // --- functions / methods ---
  const functions: GNode[] = [];
  const seenFn = new Set<string>();
  for (const ftype of spec.functionTypes) {
    for (const node of iterType(root, ftype)) {
      const info = spec.funcInfo ? spec.funcInfo(node) : defaultFuncInfo(node);
      if (!info) continue;
      const start = node.startPosition.row + 1;
      const end = node.endPosition.row + 1;
      const owner = info.owner ?? enclosingClass(start) ?? undefined;
      const qualified = owner ? `${owner}.${info.name}` : info.name;
      const funcId = `func:${rel}::${qualified}`;
      if (seenFn.has(funcId)) continue;
      seenFn.add(funcId);
      const fn: GNode = {
        id: funcId, kind: 'function', name: info.name, qualifiedName: qualified,
        file: fileId, lineStart: start, lineEnd: end,
      };
      nodes.push(fn);
      functions.push(fn);
      const src = owner ? `class:${rel}::${owner}` : fileId;
      edges.push({ src, dst: funcId, kind: 'defines', meta: { line: start } });
    }
  }

  // --- calls ---
  emitCalls(spec, root, functions, edges);

  // --- imports ---
  spec.imports?.(root, fileId, edges);

  // --- todos ---
  const todos: ParseResult['todos'] = [];
  lines.forEach((line, i) => {
    const m = TODO_RE.exec(line);
    if (m) todos.push({ line: i + 1, kind: m[1].toUpperCase(), text: m[2].trim() });
  });

  return { fileNode, nodes, edges, todos };
}

function defaultFuncInfo(fn: TSNode): { name: string; owner?: string } | null {
  const nameNode = fn.childForFieldName('name');
  return nameNode?.text ? { name: nameNode.text } : null;
}

function emitCalls(spec: LangSpec, root: TSNode, functions: GNode[], edges: GEdge[]): void {
  if (functions.length === 0) return;
  const seen = new Set<string>();
  const enclosingFn = (line: number): GNode | null => {
    let best: GNode | null = null;
    for (const fn of functions) {
      if ((fn.lineStart ?? 0) <= line && line <= (fn.lineEnd ?? 0)) {
        if (!best || (fn.lineEnd! - fn.lineStart!) < (best.lineEnd! - best.lineStart!)) best = fn;
      }
    }
    return best;
  };
  for (const ctype of spec.callTypes) {
    for (const call of iterType(root, ctype)) {
      const [callee, scope] = spec.calleeInfo(call);
      if (!callee) continue;
      if (scope === 'attr' && BUILTIN_METHODS.has(callee)) continue;
      const line = call.startPosition.row + 1;
      const caller = enclosingFn(line);
      if (!caller) continue;
      const key = `${caller.id}|${callee}|${line}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const meta: Record<string, unknown> = { resolved: false, line, callee };
      if (scope === 'self') meta.self_call = true;
      edges.push({ src: caller.id, dst: `func:?::${callee}`, kind: 'calls', meta });
    }
  }
}
