/**
 * Method names that overwhelmingly belong to language/stdlib types rather than
 * project code. A call like `path.exists()` or `buf.read()` would otherwise be
 * mis-bound to a same-named project function. Only applied to `attr`-scope
 * calls (unknown receiver). Kept in sync with codegraph/parsers/base.py.
 *
 * Deliberately conservative: ambiguous names that are also common project
 * methods (get/set/update/add/run/build/...) are excluded.
 */
export const BUILTIN_METHODS: ReadonlySet<string> = new Set([
  // containers / iterables
  'append', 'extend', 'keys', 'values', 'items', 'setdefault', 'popitem',
  // io / files
  'read', 'readline', 'readlines', 'write', 'writelines', 'flush', 'seek', 'tell',
  // strings
  'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'splitlines',
  'encode', 'decode', 'lower', 'upper', 'title', 'capitalize',
  'startswith', 'endswith', 'isdigit', 'isalpha', 'isalnum', 'isspace',
  // pathlib / os
  'exists', 'is_file', 'is_dir', 'mkdir', 'resolve', 'glob', 'rglob',
  'unlink', 'iterdir', 'absolute', 'as_posix',
  'read_text', 'write_text', 'read_bytes', 'write_bytes',
  // js/ts array & string extras (shared set covers ts too)
  'forEach', 'map', 'filter', 'reduce', 'slice', 'splice', 'push', 'pop',
  'shift', 'unshift', 'concat', 'indexOf', 'includes', 'trim', 'toString',
  'then', 'catch', 'finally',
]);
