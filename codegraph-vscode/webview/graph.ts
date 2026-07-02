/**
 * 3D force-graph visualization for codeGraph VS Code extension.
 * Bundled by esbuild into dist/webview.js (IIFE, no external imports at runtime).
 *
 * Architecture:
 *  - Nodes orbit in Z-planes partitioned by architectural layer
 *  - Each node kind gets a distinct Three.js geometry (sphere, octahedron, etc.)
 *  - Error paths: error node pulses red, ancestor nodes glow orange,
 *    error-path links emit animated particles
 */

import ForceGraph3D, { ForceGraph3DInstance } from '3d-force-graph';
import * as THREE from 'three';

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
// Constants
// ---------------------------------------------------------------------------

const LAYER_COLORS: Record<string, number> = {
  presentation:   0x4fc3f7,
  business:       0x81c784,
  data:           0xffb74d,
  infrastructure: 0xe57373,
  config:         0xb39ddb,
  utility:        0x90a4ae,
  test:           0xce93d8,
  unknown:        0x78909c,
};

/** Z-plane for each architectural layer — creates depth stratification */
const LAYER_Z: Record<string, number> = {
  presentation:   250,
  business:       130,
  utility:        60,
  unknown:        0,
  test:          -60,
  config:        -130,
  data:          -200,
  infrastructure:-280,
};

const ERROR_COLOR   = 0xff2222;
const ANCESTOR_COLOR = 0xff9800;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GNode {
  id: string;
  label: string;
  kind: string;
  layer?: string;
  pagerank?: number;
  commits?: number;
  complexity?: number;
  fullPath?: string;
  line_start?: number;
  // injected by 3d-force-graph
  x?: number; y?: number; z?: number;
  vx?: number; vy?: number; vz?: number;
  fx?: number; fy?: number; fz?: number;
  // injected by us
  __mesh?: THREE.Mesh;
  __errorState?: 'error' | 'ancestor' | null;
}

interface GLink {
  source: string | GNode;
  target: string | GNode;
  kind: string;
  __isErrorPath?: boolean;
}

// ---------------------------------------------------------------------------
// Graph state
// ---------------------------------------------------------------------------

let graph: ForceGraph3DInstance | null = null;
let allNodes: GNode[] = [];
let allLinks: GLink[] = [];
let filterQuery = '';

// ---------------------------------------------------------------------------
// Node geometry factory
// ---------------------------------------------------------------------------

function nodeSize(n: GNode): number {
  return Math.max(4, Math.min(14, 4 + (n.commits ?? 0) * 0.8 + (n.pagerank ?? 0) * 300));
}

function makeMaterial(color: number, emissive = 0x000000, emissiveIntensity = 0): THREE.MeshPhongMaterial {
  return new THREE.MeshPhongMaterial({
    color,
    emissive,
    emissiveIntensity,
    shininess: 50,
    transparent: true,
    opacity: 0.88,
  });
}

function makeGeometry(kind: string, s: number): THREE.BufferGeometry {
  switch (kind) {
    case 'file':     return new THREE.OctahedronGeometry(s * 1.4, 0);
    case 'class':    return new THREE.ConeGeometry(s, s * 2, 6);
    case 'type':     return new THREE.BoxGeometry(s * 1.2, s * 1.2, s * 1.2);
    case 'test':     return new THREE.TetrahedronGeometry(s * 1.2, 0);
    case 'module':   return new THREE.TorusGeometry(s * 0.9, s * 0.3, 6, 12);
    default:         return new THREE.SphereGeometry(s, 10, 10);  // function
  }
}

function makeTextSprite(text: string, color = '#e0e0e0'): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 320;
  canvas.height = 56;
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = 'rgba(10,12,20,0.72)';
  ctx.beginPath();
  ctx.roundRect(2, 6, canvas.width - 4, 44, 8);
  ctx.fill();
  ctx.font = 'bold 24px monospace';
  ctx.fillStyle = color;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text.length > 22 ? text.slice(0, 21) + '…' : text, canvas.width / 2, 28);
  const texture = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: texture, depthWrite: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(24, 4.5, 1);
  sprite.userData.__isLabel = true;
  return sprite;
}

function buildNodeObject(node: GNode): THREE.Object3D {
  const s = nodeSize(node);
  const baseColor = LAYER_COLORS[node.layer ?? 'unknown'] ?? 0x78909c;
  const geo = makeGeometry(node.kind, s);
  const mat = makeMaterial(baseColor);
  const mesh = new THREE.Mesh(geo, mat);
  node.__mesh = mesh;

  // Label — always for file nodes; for functions only if high-importance
  const showLabel = node.kind === 'file' || (node.pagerank ?? 0) > 0.002 || (node.commits ?? 0) > 2;
  if (showLabel) {
    const sprite = makeTextSprite(node.label);
    sprite.position.set(0, s + 5, 0);
    mesh.add(sprite);
  }

  // Point light pulse for high-pagerank nodes
  if ((node.pagerank ?? 0) > 0.005) {
    const light = new THREE.PointLight(baseColor, 1.2, 60);
    mesh.add(light);
  }

  return mesh;
}

// ---------------------------------------------------------------------------
// Graph initialisation
// ---------------------------------------------------------------------------

function initGraph(nodes: GNode[], links: GLink[]): void {
  allNodes = nodes;
  allLinks = links;

  // Pin Z per architectural layer so layers stratify in depth
  nodes.forEach((n) => {
    n.fz = LAYER_Z[n.layer ?? 'unknown'] ?? 0;
  });

  const container = document.getElementById('cy')!;

  if (graph) {
    graph.graphData({ nodes, links: links as unknown[] });
    return;
  }

  graph = ForceGraph3D({ antialias: true, alpha: false })(container)
    .backgroundColor('#0f1117')
    .graphData({ nodes, links: links as unknown[] })
    .nodeId('id')
    .nodeLabel((n: unknown) => '')  // we use custom sprites; suppress built-in tooltip
    .nodeThreeObject((n: unknown) => buildNodeObject(n as GNode))
    .nodeThreeObjectExtend(false)
    .linkSource('source')
    .linkTarget('target')
    .linkWidth((l: unknown) => ((l as GLink).__isErrorPath ? 2 : 0.5))
    .linkColor((l: unknown) => {
      const link = l as GLink;
      if (link.__isErrorPath) return '#ff9800';
      switch (link.kind) {
        case 'calls':    return '#ff980066';
        case 'imports':  return '#4fc3f766';
        case 'inherits': return '#81c78477';
        case 'tests':    return '#b39ddb88';
        default:         return '#2a2d3e99';
      }
    })
    .linkOpacity(0.7)
    .linkDirectionalArrowLength((l: unknown) => ((l as GLink).kind !== 'defines' ? 4 : 0))
    .linkDirectionalArrowRelPos(1)
    // Error path animated particles
    .linkDirectionalParticles((l: unknown) => ((l as GLink).__isErrorPath ? 5 : 0))
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleWidth(2)
    .linkDirectionalParticleColor(() => '#ff6600')
    .onNodeClick((n: unknown) => {
      const node = n as GNode;
      if (node.fullPath) {
        vscode.postMessage({ type: 'openFile', filePath: node.fullPath, line: node.line_start ?? 0 });
      }
    })
    .onNodeHover((n: unknown) => {
      const node = n as GNode | null;
      showTooltip(node);
    });

  // Custom D3 force to keep Z anchored to layer after initial layout
  (graph as any).d3Force('z-layer', () => {
    allNodes.forEach((n) => {
      n.fz = LAYER_Z[n.layer ?? 'unknown'] ?? 0;
    });
  });

  // Bloom-like ambient from scene
  const scene = (graph as any).scene() as THREE.Scene;
  scene.add(new THREE.AmbientLight(0x303050, 2));
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
  dirLight.position.set(200, 300, 200);
  scene.add(dirLight);

  // Subtle fog for depth perception
  scene.fog = new THREE.FogExp2(0x0f1117, 0.0008);

  setStatus(`${nodes.length} nodes · ${links.length} edges`);
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

const tooltip = document.getElementById('tooltip')!;

/** Escape text that originates from user code (symbol names, file paths)
 *  before interpolating into HTML — a symbol named `<img onerror=…>` must
 *  render as text, not execute. */
function esc(s: unknown): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showTooltip(node: GNode | null): void {
  if (!node) {
    tooltip.style.display = 'none';
    vscode.postMessage({ type: 'nodeHover', label: '' });
    return;
  }
  tooltip.style.display = 'block';
  tooltip.innerHTML = `
    <div class="t-name">${esc(node.label)}</div>
    <div class="t-kind">${esc(node.kind)}${node.layer ? ` · ${esc(node.layer)}` : ''}</div>
    ${node.fullPath ? `<div class="t-file">${esc(node.fullPath)}</div>` : ''}
    ${node.complexity != null ? `<div>complexity: ${Number(node.complexity)}</div>` : ''}
    ${node.commits    != null ? `<div>commits: ${Number(node.commits)}</div>` : ''}
    ${node.pagerank   != null ? `<div>pagerank: ${Number(node.pagerank).toFixed(5)}</div>` : ''}
    <div class="t-hint">click to open file</div>
  `;
  vscode.postMessage({ type: 'nodeHover', label: node.label });
}

document.getElementById('cy')!.addEventListener('mousemove', (e) => {
  tooltip.style.left = `${e.clientX + 14}px`;
  tooltip.style.top  = `${e.clientY - 10}px`;
});

// ---------------------------------------------------------------------------
// Error path highlighting
// ---------------------------------------------------------------------------

function highlightErrorPath(errorNodeId: string, ancestorIds: string[]): void {
  clearErrorHighlights(false);

  const ancestorSet = new Set(ancestorIds);

  allNodes.forEach((n) => {
    if (!n.__mesh) return;
    const mat = n.__mesh.material as THREE.MeshPhongMaterial;
    if (n.id === errorNodeId) {
      n.__errorState = 'error';
      mat.color.setHex(ERROR_COLOR);
      mat.emissive.setHex(ERROR_COLOR);
      mat.emissiveIntensity = 1.6;
      // Pulse animation via repeated timer
      startPulse(n.__mesh);
    } else if (ancestorSet.has(n.id)) {
      n.__errorState = 'ancestor';
      mat.color.setHex(ANCESTOR_COLOR);
      mat.emissive.setHex(ANCESTOR_COLOR);
      mat.emissiveIntensity = 0.7;
    } else {
      // Fade non-involved nodes
      mat.opacity = 0.12;
    }
  });

  // Mark error-path links so particle effect activates
  allLinks.forEach((l) => {
    const srcId = typeof l.source === 'object' ? (l.source as GNode).id : l.source;
    const dstId = typeof l.target === 'object' ? (l.target as GNode).id : l.target;
    l.__isErrorPath =
      (srcId === errorNodeId && ancestorSet.has(dstId)) ||
      (dstId === errorNodeId && ancestorSet.has(srcId)) ||
      ancestorSet.has(srcId) && ancestorSet.has(dstId);
  });

  // Refresh graph to apply updated link colors/particles
  graph?.graphData({ nodes: allNodes, links: allLinks as unknown[] });

  // Pan camera to error node
  const errNode = allNodes.find((n) => n.id === errorNodeId);
  if (errNode && errNode.x != null) {
    (graph as any).cameraPosition(
      { x: (errNode.x ?? 0) + 150, y: (errNode.y ?? 0) + 80, z: (errNode.fz ?? 0) + 180 },
      { x: errNode.x, y: errNode.y, z: errNode.fz ?? 0 },
      1200,
    );
  }

  const banner = document.getElementById('error-banner')!;
  banner.style.display = 'block';
  banner.textContent = `Error: ${errorNodeId.split('::').pop()} — ${ancestorIds.length} affected nodes`;
}

const _pulseTimers = new Map<THREE.Mesh, ReturnType<typeof setInterval>>();

function startPulse(mesh: THREE.Mesh): void {
  if (_pulseTimers.has(mesh)) return;
  let t = 0;
  const timer = setInterval(() => {
    t += 0.12;
    (mesh.material as THREE.MeshPhongMaterial).emissiveIntensity = 1.0 + Math.sin(t) * 0.8;
  }, 40);
  _pulseTimers.set(mesh, timer);
}

function stopPulse(mesh: THREE.Mesh): void {
  const t = _pulseTimers.get(mesh);
  if (t !== undefined) {
    clearInterval(t);
    _pulseTimers.delete(mesh);
  }
}

function clearErrorHighlights(refreshGraph = true): void {
  allNodes.forEach((n) => {
    if (!n.__mesh) return;
    const mat = n.__mesh.material as THREE.MeshPhongMaterial;
    stopPulse(n.__mesh);
    const baseColor = LAYER_COLORS[n.layer ?? 'unknown'] ?? 0x78909c;
    mat.color.setHex(baseColor);
    mat.emissive.setHex(0x000000);
    mat.emissiveIntensity = 0;
    mat.opacity = 0.88;
    n.__errorState = null;
  });

  allLinks.forEach((l) => { l.__isErrorPath = false; });

  if (refreshGraph) {
    graph?.graphData({ nodes: allNodes, links: allLinks as unknown[] });
  }

  const banner = document.getElementById('error-banner')!;
  banner.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Focus a node (pan camera)
// ---------------------------------------------------------------------------

function focusNode(nodeId: string): void {
  const n = allNodes.find((x) => x.id === nodeId);
  if (!n || !graph) return;
  (graph as any).cameraPosition(
    { x: (n.x ?? 0) + 120, y: (n.y ?? 0) + 80, z: (n.fz ?? 0) + 160 },
    { x: n.x, y: n.y, z: n.fz ?? 0 },
    900,
  );
}

// ---------------------------------------------------------------------------
// Search / filter
// ---------------------------------------------------------------------------

const searchInput = document.getElementById('search') as HTMLInputElement;
searchInput.addEventListener('input', () => {
  filterQuery = searchInput.value.toLowerCase().trim();
  applyFilter();
});

function applyFilter(): void {
  if (!filterQuery) {
    allNodes.forEach((n) => {
      if (n.__mesh) (n.__mesh.material as THREE.MeshPhongMaterial).opacity = 0.88;
    });
    return;
  }
  allNodes.forEach((n) => {
    const match = n.label.toLowerCase().includes(filterQuery)
      || (n.fullPath ?? '').toLowerCase().includes(filterQuery)
      || (n.layer ?? '').includes(filterQuery);
    if (n.__mesh) {
      (n.__mesh.material as THREE.MeshPhongMaterial).opacity = match ? 0.95 : 0.07;
    }
  });
}

// ---------------------------------------------------------------------------
// Toolbar buttons
// ---------------------------------------------------------------------------

document.getElementById('btn-fit')!.addEventListener('click', () => {
  (graph as any)?.zoomToFit(600, 40);
});

document.getElementById('btn-refresh')!.addEventListener('click', () => {
  vscode.postMessage({ type: 'requestRefresh' });
  setStatus('Refreshing…');
});

document.getElementById('btn-clear-errors')!.addEventListener('click', () => {
  clearErrorHighlights();
});

// ---------------------------------------------------------------------------
// Status bar helper
// ---------------------------------------------------------------------------

function setStatus(msg: string): void {
  const el = document.getElementById('status');
  if (el) el.textContent = msg;
}

// ---------------------------------------------------------------------------
// Message handler from extension host
// ---------------------------------------------------------------------------

window.addEventListener('message', (event) => {
  const msg = event.data as { type: string; [k: string]: unknown };

  switch (msg.type) {
    case 'loadGraph': {
      setStatus('Building 3D layout…');
      const nodes = (msg.nodes as GNode[]) ?? [];
      const links = (msg.edges as GLink[]) ?? [];
      initGraph(nodes, links);
      break;
    }

    case 'highlightErrorPath':
      highlightErrorPath(
        msg.errorNodeId as string,
        (msg.ancestorIds as string[]) ?? [],
      );
      break;

    case 'focusNode':
      focusNode(msg.nodeId as string);
      break;

    case 'clearHighlights':
      clearErrorHighlights();
      break;

    case 'error':
      setStatus(`Error: ${msg.message as string}`);
      break;
  }
});
