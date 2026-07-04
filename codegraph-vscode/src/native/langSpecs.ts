/**
 * Per-language LangSpecs for the generic parser. Node types and callee
 * extraction mirror the corresponding codegraph/parsers/*_parser.py.
 */
import type Parser from 'web-tree-sitter';
import { firstDescendant, type LangSpec } from './genericParser';
import type { CallScope, GEdge } from './types';

type TSNode = Parser.SyntaxNode;

const text = (n: TSNode | null | undefined) => n?.text ?? '';

// --- Go -------------------------------------------------------------------
const go: LangSpec = {
  lang: 'go',
  grammarWasm: 'tree-sitter-go.wasm',
  extensions: ['.go'],
  // Structs and interfaces are class-like (matches the Python Go parser);
  // plain type aliases are not.
  classTypes: [
    { type: 'type_spec', childType: 'struct_type' },
    { type: 'type_spec', childType: 'interface_type' },
  ],
  functionTypes: ['function_declaration', 'method_declaration'],
  callTypes: ['call_expression'],
  testRe: /_test\.go$/,
  funcInfo(fn) {
    const name = fn.childForFieldName('name')?.text;
    if (!name) return null;
    const recv = fn.childForFieldName('receiver');
    if (recv) {
      const tid = firstDescendant(recv, 'type_identifier');
      return { name, owner: tid?.text };
    }
    return { name };
  },
  calleeInfo(call): [string | null, CallScope] {
    const fn = call.childForFieldName('function');
    if (!fn) return [null, 'free'];
    if (fn.type === 'identifier') return [fn.text, 'free'];
    if (fn.type === 'selector_expression') {
      const field = fn.childForFieldName('field');
      return field ? [field.text, 'attr'] : [null, 'free'];
    }
    return [null, 'free'];
  },
  imports(root, fileId, edges: GEdge[]) {
    for (const spec of iter(root, 'import_spec')) {
      const p = spec.childForFieldName('path');
      if (p) edges.push({ src: fileId, dst: `module:${text(p).replace(/"/g, '')}`, kind: 'imports', meta: {} });
    }
  },
};

// --- Rust ------------------------------------------------------------------
const rust: LangSpec = {
  lang: 'rust',
  grammarWasm: 'tree-sitter-rust.wasm',
  extensions: ['.rs'],
  classTypes: [
    { type: 'struct_item' }, { type: 'enum_item' }, { type: 'trait_item' },
    { type: 'impl_item', nameField: 'type' },
  ],
  functionTypes: ['function_item'],
  callTypes: ['call_expression', 'method_call_expression'],
  calleeInfo(call): [string | null, CallScope] {
    if (call.type === 'method_call_expression') {
      const method = call.childForFieldName('method');
      const recv = call.childForFieldName('receiver');
      const isSelf = recv?.type === 'self';
      return method ? [method.text, isSelf ? 'self' : 'attr'] : [null, 'free'];
    }
    const fn = call.childForFieldName('function');
    return fn ? calleeFromExpr(fn) : [null, 'free'];
  },
  imports(root, fileId, edges: GEdge[]) {
    for (const use of iter(root, 'use_declaration')) {
      for (const child of use.children) {
        if (!child || ['use', ';', 'visibility_modifier'].includes(child.type)) continue;
        const p = child.text.trim();
        if (p) edges.push({ src: fileId, dst: `module:${p}`, kind: 'imports', meta: {} });
        break;
      }
    }
  },
};

function calleeFromExpr(fn: TSNode): [string | null, CallScope] {
  switch (fn.type) {
    case 'identifier': return [fn.text, 'free'];
    case 'field_expression': {
      const field = fn.childForFieldName('field');
      return field ? [field.text, 'attr'] : [null, 'free'];
    }
    case 'scoped_identifier': {
      const name = fn.childForFieldName('name');
      return name ? [name.text, 'free'] : [null, 'free'];
    }
    case 'generic_function': {
      const inner = fn.childForFieldName('function');
      return inner ? calleeFromExpr(inner) : [null, 'free'];
    }
    default: return [null, 'free'];
  }
}

// --- Java ------------------------------------------------------------------
const java: LangSpec = {
  lang: 'java',
  grammarWasm: 'tree-sitter-java.wasm',
  extensions: ['.java'],
  classTypes: [
    { type: 'class_declaration' }, { type: 'interface_declaration' }, { type: 'enum_declaration' },
  ],
  functionTypes: ['method_declaration', 'constructor_declaration'],
  callTypes: ['method_invocation'],
  calleeInfo(call): [string | null, CallScope] {
    const name = call.childForFieldName('name');
    if (!name) return [null, 'free'];
    const obj = call.childForFieldName('object');
    const isSelf = !obj || obj.type === 'this';
    return [name.text, isSelf ? 'self' : 'attr'];
  },
  imports(root, fileId, edges: GEdge[]) {
    for (const imp of root.children) {
      if (imp?.type !== 'import_declaration') continue;
      const scoped = firstDescendant(imp, 'scoped_identifier');
      if (scoped) edges.push({ src: fileId, dst: `module:${scoped.text}`, kind: 'imports', meta: {} });
    }
  },
};

// --- C / C++ ---------------------------------------------------------------
const c: LangSpec = {
  lang: 'cpp',
  grammarWasm: 'tree-sitter-cpp.wasm',
  extensions: ['.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.hh', '.hxx'],
  classTypes: [{ type: 'class_specifier' }, { type: 'struct_specifier' }],
  functionTypes: ['function_definition'],
  callTypes: ['call_expression'],
  funcInfo(fn) {
    const decl = firstDescendant(fn, 'function_declarator');
    const d = decl?.childForFieldName('declarator');
    if (!d) return null;
    if (d.type === 'qualified_identifier') {
      const n = d.childForFieldName('name');
      return n ? { name: n.text } : null;
    }
    if (d.type === 'identifier' || d.type === 'field_identifier') return { name: d.text };
    return null;
  },
  calleeInfo(call): [string | null, CallScope] {
    const fn = call.childForFieldName('function');
    if (!fn) return [null, 'free'];
    if (fn.type === 'identifier') return [fn.text, 'free'];
    if (fn.type === 'field_expression') {
      const field = fn.childForFieldName('field');
      return field ? [field.text, 'attr'] : [null, 'free'];
    }
    if (fn.type === 'qualified_identifier') {
      const n = fn.childForFieldName('name');
      return n && n.type !== 'qualified_identifier' ? [n.text, 'free'] : [null, 'free'];
    }
    return [null, 'free'];
  },
};

// --- TypeScript / JavaScript ----------------------------------------------
const typescript: LangSpec = {
  lang: 'typescript',
  grammarWasm: 'tree-sitter-typescript.wasm',
  extensions: ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'],
  classTypes: [{ type: 'class_declaration' }, { type: 'interface_declaration' }],
  functionTypes: ['function_declaration', 'method_definition'],
  callTypes: ['call_expression'],
  testRe: /\.(test|spec)\.[jt]sx?$/,
  calleeInfo(call): [string | null, CallScope] {
    const fn = call.childForFieldName('function');
    if (!fn) return [null, 'free'];
    if (fn.type === 'identifier') return [fn.text, 'free'];
    if (fn.type === 'member_expression') {
      const prop = fn.childForFieldName('property');
      if (!prop) return [null, 'free'];
      const obj = fn.childForFieldName('object');
      return [prop.text, obj?.type === 'this' ? 'self' : 'attr'];
    }
    return [null, 'free'];
  },
  imports(root, fileId, edges: GEdge[]) {
    for (const imp of iter(root, 'import_statement')) {
      const src = imp.childForFieldName('source');
      if (src) edges.push({ src: fileId, dst: `module:${src.text.replace(/['"]/g, '')}`, kind: 'imports', meta: {} });
    }
  },
};

// tiny local iterator (avoid circular import of the generator)
function* iter(node: TSNode, type: string): Generator<TSNode> {
  if (node.type === type) yield node;
  for (const ch of node.children) if (ch) yield* iter(ch, type);
}

export const LANG_SPECS: LangSpec[] = [go, rust, java, c, typescript];

export function specForFile(file: string): LangSpec | null {
  const lower = file.toLowerCase();
  for (const s of LANG_SPECS) if (s.extensions.some((e) => lower.endsWith(e))) return s;
  return null;
}
