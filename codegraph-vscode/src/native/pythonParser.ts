/**
 * Python parser over a web-tree-sitter syntax tree. Mirrors the structure and
 * call-scope semantics of codegraph/parsers/python_parser.py so the native
 * backend produces the same graph the CLI would.
 */
import type Parser from 'web-tree-sitter';
import { BUILTIN_METHODS } from './builtins';

type TSNode = Parser.SyntaxNode;
type Tree = Parser.Tree;
import type { CallScope, CallSite, GEdge, GNode, ParseResult } from './types';

const TODO_RE = /#\s*(TODO|FIXME|HACK|NOTE|XXX|BUG)\b[:\s]*(.*)/i;
const TEST_RE = /(^|\/)tests?\/|_test\.py$|test_.*\.py$/i;

function* iterType(node: TSNode, type: string): Generator<TSNode> {
  if (node.type === type) yield node;
  for (const child of node.children) {
    if (child) yield* iterType(child, type);
  }
}

export class PythonParser {
  readonly lang = 'python';

  parse(tree: Tree, rel: string, source: string): ParseResult {
    const fileId = `file:${rel}`;
    const lines = source.split(/\r?\n/);
    const isTest = TEST_RE.test(rel);

    const fileNode: GNode = {
      id: fileId, kind: 'file', name: rel, file: fileId, path: rel,
      lang: this.lang, lineStart: 1, lineEnd: lines.length, isTest,
    };
    const nodes: GNode[] = [];
    const edges: GEdge[] = [];
    const root = tree.rootNode;

    // --- classes ---
    const classSpans: Array<{ name: string; start: number; end: number }> = [];
    for (const node of iterType(root, 'class_definition')) {
      const nameNode = node.childForFieldName('name');
      if (!nameNode) continue;
      const name = nameNode.text;
      const start = node.startPosition.row + 1;
      const end = node.endPosition.row + 1;
      const classId = `class:${rel}::${name}`;
      const bases = this.bases(node);
      nodes.push({
        id: classId, kind: 'class', name, file: fileId,
        lineStart: start, lineEnd: end, bases,
        docstring: this.docstring(node.childForFieldName('body')),
      });
      edges.push({ src: fileId, dst: classId, kind: 'defines', meta: { line: start } });
      classSpans.push({ name, start, end });
      for (const base of bases) {
        edges.push({ src: classId, dst: `class:?::${base}`, kind: 'inherits', meta: { resolved: false } });
      }
    }

    // --- functions ---
    const functions: GNode[] = [];
    for (const node of iterType(root, 'function_definition')) {
      const nameNode = node.childForFieldName('name');
      if (!nameNode) continue;
      const name = nameNode.text;
      const start = node.startPosition.row + 1;
      const end = node.endPosition.row + 1;
      const cls = this.enclosingClass(start, classSpans);
      const qualified = cls ? `${cls}.${name}` : name;
      const funcId = `func:${rel}::${qualified}`;
      const fn: GNode = {
        id: funcId, kind: 'function', name, qualifiedName: qualified, file: fileId,
        lineStart: start, lineEnd: end,
        signature: this.signature(name, node),
        docstring: this.docstring(node.childForFieldName('body')),
      };
      nodes.push(fn);
      functions.push(fn);
      const src = cls ? `class:${rel}::${cls}` : fileId;
      edges.push({ src, dst: funcId, kind: 'defines', meta: { line: start } });
    }

    // --- imports ---
    for (const node of iterType(root, 'import_statement')) {
      for (const dotted of iterType(node, 'dotted_name')) {
        edges.push({ src: fileId, dst: `module:${dotted.text}`, kind: 'imports', meta: {} });
        break;
      }
    }
    for (const node of iterType(root, 'import_from_statement')) {
      const mod = node.childForFieldName('module_name');
      const dst = mod ? `module:${mod.text}` : fileId;
      edges.push({ src: fileId, dst, kind: 'imports', meta: { module: mod?.text ?? '' } });
    }

    // --- calls ---
    this.emitCalls(root, functions, edges);

    // --- todos ---
    const todos: ParseResult['todos'] = [];
    lines.forEach((line, i) => {
      const m = TODO_RE.exec(line);
      if (m) todos.push({ line: i + 1, kind: m[1].toUpperCase(), text: m[2].trim() });
    });

    return { fileNode, nodes, edges, todos };
  }

  private bases(classNode: TSNode): string[] {
    const args = classNode.childForFieldName('superclasses');
    if (!args) return [];
    const out: string[] = [];
    for (const child of args.namedChildren) {
      const t = child?.text.trim();
      if (t) out.push(t);
    }
    return out;
  }

  private enclosingClass(
    line: number, spans: Array<{ name: string; start: number; end: number }>,
  ): string | null {
    for (const s of spans) if (s.start < line && line <= s.end) return s.name;
    return null;
  }

  private signature(name: string, fn: TSNode): string {
    const params = fn.childForFieldName('parameters')?.text ?? '()';
    const ret = fn.childForFieldName('return_type');
    return `${name}${params}${ret ? ' -> ' + ret.text : ''}`.slice(0, 200);
  }

  private docstring(body: TSNode | null): string | undefined {
    if (!body) return undefined;
    for (const child of body.namedChildren) {
      if (child?.type === 'expression_statement') {
        const expr = child.namedChildren[0];
        if (expr && (expr.type === 'string' || expr.type === 'concatenated_string')) {
          const cleaned = expr.text.replace(/^[\s'"]+|[\s'"]+$/g, '');
          return cleaned ? cleaned.slice(0, 512) : undefined;
        }
      }
    }
    return undefined;
  }

  /** Build CALLS placeholder edges, mirroring base._emit_call_edges. */
  private emitCalls(root: TSNode, functions: GNode[], edges: GEdge[]): void {
    if (functions.length === 0) return;
    const seen = new Set<string>();
    for (const call of iterType(root, 'call')) {
      const fnField = call.childForFieldName('function');
      if (!fnField) continue;
      const [callee, scope] = this.calleeInfo(fnField);
      if (!callee) continue;
      const line = call.startPosition.row + 1;
      if (scope === 'attr' && BUILTIN_METHODS.has(callee)) continue;
      const caller = this.enclosingFunction(line, functions);
      if (!caller) continue;
      const key = `${caller.id}|${callee}|${line}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const meta: Record<string, unknown> = { resolved: false, line, callee };
      if (scope === 'self') meta.self_call = true;
      edges.push({ src: caller.id, dst: `func:?::${callee}`, kind: 'calls', meta });
    }
  }

  private calleeInfo(fn: TSNode): [string | null, CallScope] {
    if (fn.type === 'identifier') return [fn.text, 'free'];
    if (fn.type === 'attribute') {
      const attr = fn.childForFieldName('attribute');
      if (!attr) return [null, 'free'];
      const obj = fn.childForFieldName('object');
      const isSelf =
        !!obj && obj.type === 'identifier' && (obj.text === 'self' || obj.text === 'cls');
      return [attr.text, isSelf ? 'self' : 'attr'];
    }
    return [null, 'free'];
  }

  private enclosingFunction(line: number, functions: GNode[]): GNode | null {
    let best: GNode | null = null;
    for (const fn of functions) {
      if ((fn.lineStart ?? 0) <= line && line <= (fn.lineEnd ?? 0)) {
        if (!best || (fn.lineEnd! - fn.lineStart!) < (best.lineEnd! - best.lineStart!)) best = fn;
      }
    }
    return best;
  }
}
