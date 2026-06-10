"""Self-contained interactive HTML report generator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import BaseLoader, Environment

if TYPE_CHECKING:
    from codegraph.context.pack_generator import ContextPack

# Minimal inline CSS + JS (no external dependencies)
_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>codeGraph — {{ pack.repo_name }}</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d2e; --border: #2d3148;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #7c3aed;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444; --blue: #3b82f6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 1.25rem; font-weight: 700; }
  header .meta { color: var(--muted); font-size: 0.8rem; }
  .container { max-width: 1400px; margin: 0 auto; padding: 1.5rem 2rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; }
  .card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: .75rem; }
  .stat { font-size: 2rem; font-weight: 700; color: var(--accent); }
  .stat-label { font-size: 0.8rem; color: var(--muted); }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { text-align: left; padding: .5rem .75rem; background: var(--surface); color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .06em; border-bottom: 1px solid var(--border); position: sticky; top: 0; }
  td { padding: .45rem .75rem; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:hover td { background: rgba(124,58,237,.06); }
  .code { font-family: 'Fira Code', 'Cascadia Code', monospace; font-size: 0.8rem; background: rgba(0,0,0,.3); padding: 1px 5px; border-radius: 3px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; }
  .badge-fn { background: rgba(59,130,246,.2); color: var(--blue); }
  .badge-cls { background: rgba(124,58,237,.2); color: #a78bfa; }
  .badge-file { background: rgba(34,197,94,.15); color: var(--green); }
  .badge-TODO { background: rgba(59,130,246,.2); color: var(--blue); }
  .badge-FIXME { background: rgba(239,68,68,.2); color: var(--red); }
  .badge-HACK { background: rgba(234,179,8,.2); color: var(--yellow); }
  .badge-BUG { background: rgba(239,68,68,.3); color: var(--red); }
  .badge-NOTE { background: rgba(148,163,184,.2); color: var(--muted); }
  .section { margin-bottom: 2rem; }
  .section-header { font-size: 1rem; font-weight: 600; margin-bottom: .75rem; padding-bottom: .5rem; border-bottom: 1px solid var(--border); }
  .layer-block { margin-bottom: .5rem; }
  .layer-name { font-weight: 600; color: var(--accent); margin-bottom: .25rem; }
  .file-list { color: var(--muted); font-size: 0.8rem; font-family: monospace; padding-left: 1rem; }
  input[type=search] { background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: .4rem .75rem; border-radius: 6px; font-size: 0.85rem; width: 100%; max-width: 300px; margin-bottom: .75rem; outline: none; }
  input[type=search]:focus { border-color: var(--accent); }
  .commit-block { border-left: 3px solid var(--accent); padding-left: .75rem; margin-bottom: .75rem; }
  .commit-sha { font-family: monospace; font-size: 0.8rem; color: var(--accent); }
  .commit-msg { font-weight: 500; margin: .2rem 0; }
  .commit-meta { color: var(--muted); font-size: 0.78rem; }
  footer { text-align: center; padding: 2rem; color: var(--muted); font-size: 0.78rem; border-top: 1px solid var(--border); margin-top: 2rem; }
</style>
</head>
<body>

<header>
  <h1>&#9678; codeGraph &mdash; {{ pack.repo_name }}</h1>
  <span class="meta">Generated {{ pack.generated_at[:19] }} UTC &bull; {{ pack.symbol_count }} nodes &bull; {{ pack.file_count }} files</span>
</header>

<div class="container">

  <!-- Overview stats -->
  <div class="grid">
    <div class="card">
      <h2>Files</h2>
      <div class="stat">{{ pack.repo_overview.get('files', 0) }}</div>
      <div class="stat-label">source files</div>
    </div>
    <div class="card">
      <h2>Functions</h2>
      <div class="stat">{{ pack.repo_overview.get('functions', 0) }}</div>
      <div class="stat-label">functions &amp; methods</div>
    </div>
    <div class="card">
      <h2>Classes</h2>
      <div class="stat">{{ pack.repo_overview.get('classes', 0) }}</div>
      <div class="stat-label">classes &amp; types</div>
    </div>
    <div class="card">
      <h2>Graph Edges</h2>
      <div class="stat">{{ pack.repo_overview.get('edge_count', 0) }}</div>
      <div class="stat-label">relationships</div>
    </div>
    {% for lang, count in pack.repo_overview.get('languages', {}).items() | sort(attribute='1', reverse=True) %}
    {% if loop.index <= 2 %}
    <div class="card">
      <h2>{{ lang | title }}</h2>
      <div class="stat">{{ count }}</div>
      <div class="stat-label">files</div>
    </div>
    {% endif %}
    {% endfor %}
  </div>

  <!-- Hot Paths -->
  <div class="section">
    <div class="section-header">&#128293; Hot Paths (Most Active Code)</div>
    <div style="overflow-x:auto">
    <table id="hotpaths-table">
      <thead><tr>
        <th>#</th><th>Name</th><th>Kind</th><th>File</th><th>Commits</th><th>Complexity</th>
      </tr></thead>
      <tbody>
      {% for h in pack.hot_paths %}
      <tr>
        <td>{{ loop.index }}</td>
        <td><span class="code">{{ h.name }}</span></td>
        <td><span class="badge badge-{{ h.kind }}">{{ h.kind }}</span></td>
        <td><span class="code" style="font-size:.75rem">{{ h.get('file','').replace('file:','') | truncate(50) }}</span></td>
        <td>{{ h.commit_count }}</td>
        <td>{{ h.complexity }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
  </div>

  <!-- Architectural Layers -->
  <div class="section">
    <div class="section-header">&#127963; Architectural Layers</div>
    <div class="grid">
    {% for layer, files in pack.architectural_layers.items() | sort %}
    <div class="card">
      <h2>{{ layer | title }} ({{ files | length }})</h2>
      <div class="file-list">
        {% for f in files[:8] %}<div>{{ f | truncate(45) }}</div>{% endfor %}
        {% if files | length > 8 %}<div style="color:var(--accent)">+{{ files|length - 8 }} more</div>{% endif %}
      </div>
    </div>
    {% endfor %}
    </div>
  </div>

  <!-- Public API -->
  <div class="section">
    <div class="section-header">&#128196; Public API Surface</div>
    <input type="search" id="api-search" placeholder="Search symbols..." oninput="filterTable('api-table', this.value)">
    <div style="overflow-x:auto">
    <table id="api-table">
      <thead><tr><th>Name</th><th>Kind</th><th>File</th><th>Signature</th></tr></thead>
      <tbody>
      {% for api in pack.public_api_summary %}
      <tr>
        <td><span class="code">{{ api.name }}</span></td>
        <td><span class="badge badge-{{ api.kind }}">{{ api.kind }}</span></td>
        <td><span class="code" style="font-size:.75rem">{{ api.file | truncate(45) }}</span></td>
        <td><span style="color:var(--muted);font-size:.8rem">{{ api.sig | truncate(80) }}</span></td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
  </div>

  <!-- Key Classes -->
  {% if pack.key_classes %}
  <div class="section">
    <div class="section-header">&#129528; Key Classes</div>
    <div class="grid">
    {% for c in pack.key_classes %}
    <div class="card">
      <h2><span class="code">{{ c.name }}</span></h2>
      <div style="font-size:.78rem;color:var(--muted)">{{ c.file | truncate(45) }}</div>
      {% if c.bases %}<div style="margin-top:.4rem;font-size:.8rem">extends <span class="code">{{ c.bases | join(', ') }}</span></div>{% endif %}
      {% if c.docstring %}<div style="margin-top:.4rem;color:var(--muted);font-size:.8rem">{{ c.docstring | truncate(100) }}</div>{% endif %}
    </div>
    {% endfor %}
    </div>
  </div>
  {% endif %}

  <!-- Recent Changes -->
  {% if pack.recent_changes %}
  <div class="section">
    <div class="section-header">&#128198; Recent Changes</div>
    {% for c in pack.recent_changes %}
    <div class="commit-block">
      <span class="commit-sha">{{ c.get('short_sha', c.get('sha','')[:7]) }}</span>
      <div class="commit-msg">{{ c.get('message','') | truncate(120) }}</div>
      <div class="commit-meta">
        {{ c.get('author','') }}
        {% if c.get('impacted_symbols') %}&bull; impacts: {{ c.impacted_symbols | map(attribute='name') | select | list | join(', ') | truncate(100) }}{% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <!-- TODOs -->
  {% if pack.todos %}
  <div class="section">
    <div class="section-header">&#9888;&#65039; Open TODOs ({{ pack.todos | length }})</div>
    <table>
      <thead><tr><th>Kind</th><th>File</th><th>Line</th><th>Note</th></tr></thead>
      <tbody>
      {% for t in pack.todos %}
      <tr>
        <td><span class="badge badge-{{ t.kind }}">{{ t.kind }}</span></td>
        <td><span class="code" style="font-size:.75rem">{{ t.file | truncate(40) }}</span></td>
        <td>{{ t.line }}</td>
        <td style="color:var(--muted)">{{ t.text | truncate(100) }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

</div>

<footer>
  codeGraph &bull; query: <code>codegraph query &lt;symbol&gt;</code> or via MCP tool <code>codegraph_find_symbol</code>
</footer>

<script>
function filterTable(tableId, query) {
  const q = query.toLowerCase();
  const rows = document.getElementById(tableId).querySelectorAll('tbody tr');
  rows.forEach(row => {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
</body>
</html>
"""


class HtmlReporter:
    def __init__(self, pack: "ContextPack"):
        self.pack = pack

    def render(self) -> str:
        env = Environment(loader=BaseLoader(), autoescape=True)
        env.filters["truncate"] = self._truncate_filter
        tmpl = env.from_string(_TEMPLATE)
        return tmpl.render(pack=self.pack)

    @staticmethod
    def _truncate_filter(s: str, length: int = 80, *args, **kwargs) -> str:
        if not isinstance(s, str):
            s = str(s)
        if len(s) <= length:
            return s
        return s[: length - 3] + "..."
