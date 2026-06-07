// Cytoscape.js hive graph visualization for codeGraph VS Code extension
// Bundled via esbuild into dist/webview.js (IIFE, no external deps)

import cytoscape, { Core, NodeSingular } from 'cytoscape';
// @ts-ignore
import coseBilkent from 'cytoscape-cose-bilkent';

cytoscape.use(coseBilkent);

// ---------------------------------------------------------------------------
// VS Code WebView API
// ---------------------------------------------------------------------------

declare function acquireVsCodeApi(): {
  postMessage(msg: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
};
const vscode = acquireVsCodeApi();

// ---------------------------------------------------------------------------
// Layer colors (dark-theme palette)
// ---------------------------------------------------------------------------

const LAYER_COLORS: Record<string, string> = {
  presentation: '#4fc3f7',
  business:     '#81c784',
  data:         '#ffb74d',
  infrastructure:'#e57373',
  config:       '#b39ddb',
  utility:      '#90a4ae',
  unknown:      '#78909c',
};

const KIND_SHAPES: Record<string, string> = {
  file:     'hexagon',
  function: 'ellipse',
  class:    'diamond',
  type:     'rectangle',
  test:     'triangle',
  module:   'round-rectangle',
};

// ---------------------------------------------------------------------------
// Graph instance
// ---------------------------------------------------------------------------

let cy: Core | null = null;
let allNodes: cytoscape.ElementDefinition[] = [];
let allEdges: cytoscape.ElementDefinition[] = [];

function initCy(nodes: cytoscape.ElementDefinition[], edges: cytoscape.ElementDefinition[]): void {
  allNodes = nodes;
  allEdges = edges;

  if (cy) cy.destroy();

  cy = cytoscape({
    container: document.getElementById('cy')!,
    elements: [...nodes, ...edges],
    style: buildStyle(),
    layout: {
      name: 'cose-bilkent',
      animate: 'end',
      animationDuration: 800,
      randomize: true,
      nodeRepulsion: 8000,
      idealEdgeLength: 80,
      edgeElasticity: 0.1,
      nestingFactor: 0.1,
      gravity: 0.25,
      numIter: 2500,
      tile: true,
      tilingPaddingVertical: 10,
      tilingPaddingHorizontal: 10,
    } as cytoscape.LayoutOptions,
    minZoom: 0.05,
    maxZoom: 4,
    wheelSensitivity: 0.3,
  });

  attachInteractions(cy);
  setStatus(`${nodes.length} nodes, ${edges.length} edges`);
}

function buildStyle(): cytoscape.Stylesheet[] {
  const base: cytoscape.Stylesheet[] = [
    {
      selector: 'node',
      style: {
        'background-color': (ele: NodeSingular) => LAYER_COLORS[ele.data('layer') ?? 'unknown'] ?? '#78909c',
        'shape': (ele: NodeSingular) => (KIND_SHAPES[ele.data('kind')] ?? 'ellipse') as cytoscape.Css.NodeShape,
        'label': 'data(label)',
        'font-size': 9,
        'color': '#e0e0e0',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 4,
        'text-outline-color': '#0f1117',
        'text-outline-width': 1,
        'width': (ele: NodeSingular) => Math.max(20, Math.min(50, 20 + (ele.data('commits') ?? 0) * 2)),
        'height': (ele: NodeSingular) => Math.max(20, Math.min(50, 20 + (ele.data('commits') ?? 0) * 2)),
        'border-width': 1,
        'border-color': '#2a2d3e',
        'transition-property': 'background-color, border-color, border-width',
        'transition-duration': 300,
      } as cytoscape.Css.Node,
    },
    {
      selector: 'node[kind="file"]',
      style: {
        'font-size': 10,
        'font-weight': 'bold',
        'width': 40,
        'height': 40,
      } as cytoscape.Css.Node,
    },
    {
      selector: 'edge',
      style: {
        'width': 1,
        'line-color': '#2a2d3e',
        'target-arrow-color': '#3a3d52',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'opacity': 0.6,
      } as cytoscape.Css.Edge,
    },
    {
      selector: 'edge[kind="calls"]',
      style: {
        'line-color': '#ff9800',
        'target-arrow-color': '#ff9800',
        'line-style': 'dotted',
        'opacity': 0.5,
      } as cytoscape.Css.Edge,
    },
    {
      selector: 'edge[kind="imports"]',
      style: {
        'line-color': '#4fc3f7',
        'target-arrow-color': '#4fc3f7',
        'line-style': 'dashed',
        'opacity': 0.4,
      } as cytoscape.Css.Edge,
    },
    {
      selector: 'edge[kind="inherits"], edge[kind="implements"]',
      style: {
        'line-color': '#81c784',
        'target-arrow-color': '#81c784',
      } as cytoscape.Css.Edge,
    },
    {
      selector: 'edge[kind="tests"]',
      style: {
        'line-color': '#b39ddb',
        'target-arrow-color': '#b39ddb',
      } as cytoscape.Css.Edge,
    },
    // Error highlighting styles
    {
      selector: '.error-node',
      style: {
        'border-color': '#ff4444',
        'border-width': 3,
        'background-color': '#ff4444',
        'shadow-blur': 20,
        'shadow-color': '#ff4444',
        'shadow-opacity': 0.8,
      } as cytoscape.Css.Node,
    },
    {
      selector: '.ancestor-node',
      style: {
        'border-color': '#ff9800',
        'border-width': 2,
        'background-color': (ele: NodeSingular) => {
          const base = LAYER_COLORS[ele.data('layer') ?? 'unknown'] ?? '#78909c';
          return base;
        },
        'shadow-blur': 10,
        'shadow-color': '#ff9800',
        'shadow-opacity': 0.5,
      } as cytoscape.Css.Node,
    },
    {
      selector: '.error-edge',
      style: {
        'line-color': '#ff9800',
        'target-arrow-color': '#ff9800',
        'width': 2,
        'line-style': 'dashed',
        'opacity': 1,
      } as cytoscape.Css.Edge,
    },
    {
      selector: '.faded',
      style: { 'opacity': 0.15 } as cytoscape.Css.Node,
    },
    {
      selector: ':selected',
      style: {
        'border-color': '#ffffff',
        'border-width': 2,
      } as cytoscape.Css.Node,
    },
  ];
  return base;
}

// ---------------------------------------------------------------------------
// Interactions
// ---------------------------------------------------------------------------

function attachInteractions(cy: Core): void {
  const tooltip = document.getElementById('tooltip')!;

  cy.on('mouseover', 'node', (evt) => {
    const node = evt.target;
    const d = node.data();
    tooltip.style.display = 'block';
    tooltip.innerHTML = `
      <div class="t-name">${d.label}</div>
      <div class="t-kind">${d.kind}${d.layer ? ` · ${d.layer}` : ''}</div>
      ${d.fullPath ? `<div class="t-file">${d.fullPath}</div>` : ''}
      ${d.complexity ? `<div>complexity: ${d.complexity}</div>` : ''}
      ${d.commits ? `<div>commits: ${d.commits}</div>` : ''}
      ${d.pagerank ? `<div>pagerank: ${Number(d.pagerank).toFixed(4)}</div>` : ''}
    `;
    vscode.postMessage({ type: 'nodeHover', label: d.label });
  });

  cy.on('mousemove', (evt) => {
    const pos = (evt as unknown as MouseEvent);
    tooltip.style.left = `${pos.clientX + 14}px`;
    tooltip.style.top = `${pos.clientY - 10}px`;
  });

  cy.on('mouseout', 'node', () => {
    tooltip.style.display = 'none';
  });

  cy.on('dblclick', 'node', (evt) => {
    const d = evt.target.data();
    if (d.fullPath) {
      vscode.postMessage({ type: 'openFile', filePath: d.fullPath, line: d.line_start ?? 0 });
    }
  });
}

// ---------------------------------------------------------------------------
// Toolbar
// ---------------------------------------------------------------------------

document.getElementById('btn-fit')!.addEventListener('click', () => cy?.fit());
document.getElementById('btn-refresh')!.addEventListener('click', () => {
  vscode.postMessage({ type: 'requestRefresh' });
});
document.getElementById('btn-clear-errors')!.addEventListener('click', () => {
  clearErrorHighlights();
});

const searchInput = document.getElementById('search') as HTMLInputElement;
searchInput.addEventListener('input', () => {
  const q = searchInput.value.toLowerCase().trim();
  if (!cy) return;
  if (!q) {
    cy.elements().removeClass('faded');
    return;
  }
  cy.nodes().forEach((n) => {
    const label = String(n.data('label') ?? '').toLowerCase();
    const path = String(n.data('fullPath') ?? '').toLowerCase();
    const matches = label.includes(q) || path.includes(q);
    n.toggleClass('faded', !matches);
  });
});

// ---------------------------------------------------------------------------
// Error path highlighting
// ---------------------------------------------------------------------------

function highlightErrorPath(errorNodeId: string, ancestorIds: string[]): void {
  if (!cy) return;
  clearErrorHighlights();

  const errorNode = cy.$id(errorNodeId);
  if (errorNode.length) {
    errorNode.addClass('error-node');
    cy.animate({ fit: { eles: errorNode, padding: 100 }, duration: 600 } as cytoscape.AnimateOptions);
  }

  for (const id of ancestorIds) {
    cy.$id(id).addClass('ancestor-node');
  }

  // Highlight edges connecting error node to ancestors
  if (errorNode.length) {
    const ancestorSet = new Set(ancestorIds);
    errorNode.connectedEdges().forEach((edge) => {
      const src = edge.source().id();
      const tgt = edge.target().id();
      if (ancestorSet.has(src) || ancestorSet.has(tgt)) {
        edge.addClass('error-edge');
      }
    });
  }

  const banner = document.getElementById('error-banner')!;
  banner.style.display = 'block';
  banner.textContent = `Error path: ${errorNodeId.split('::').pop()} → ${ancestorIds.length} affected nodes`;
}

function clearErrorHighlights(): void {
  cy?.elements().removeClass('error-node ancestor-node error-edge faded');
  const banner = document.getElementById('error-banner')!;
  banner.style.display = 'none';
}

function focusNode(nodeId: string): void {
  if (!cy) return;
  const node = cy.$id(nodeId);
  if (node.length) {
    cy.animate({ fit: { eles: node, padding: 120 }, duration: 600 } as cytoscape.AnimateOptions);
    node.select();
  }
}

// ---------------------------------------------------------------------------
// Message handler from extension host
// ---------------------------------------------------------------------------

function setStatus(msg: string): void {
  const el = document.getElementById('status');
  if (el) el.textContent = msg;
}

window.addEventListener('message', (event) => {
  const msg = event.data as { type: string; [key: string]: unknown };
  switch (msg.type) {
    case 'loadGraph': {
      setStatus('Building layout…');
      const nodes = (msg.nodes as cytoscape.ElementDefinition[]) ?? [];
      const edges = (msg.edges as cytoscape.ElementDefinition[]) ?? [];
      initCy(nodes, edges);
      break;
    }
    case 'highlightErrorPath': {
      highlightErrorPath(
        msg.errorNodeId as string,
        (msg.ancestorIds as string[]) ?? [],
      );
      break;
    }
    case 'focusNode': {
      focusNode(msg.nodeId as string);
      break;
    }
    case 'clearHighlights': {
      clearErrorHighlights();
      break;
    }
    case 'error': {
      setStatus(`Error: ${msg.message}`);
      break;
    }
  }
});
