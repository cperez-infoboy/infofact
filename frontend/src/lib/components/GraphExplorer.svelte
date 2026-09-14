<script lang="ts">
  // GraphExplorer: grafo interactivo (Cytoscape.js + layout dagre) para el
  // mapa de descomposición y el explorador de trazabilidad.
  // - Clic en nodo → onNodeSelect(id) (el padre llena el panel de detalle).
  // - Clic en arista → onEdgeSelect(edge) (detalle del contrato).
  // - Doble clic en nodo → onNodeSelect(id, true) (expandir vecindario).
  // - Zoom/pan nativos de Cytoscape; colores por tipo de nodo/arista.
  // Runes OK (.svelte).
  import { onMount, onDestroy } from 'svelte';
  import cytoscape from 'cytoscape';
  import dagre from 'cytoscape-dagre';
  import type { GraphEdge, GraphNode } from '$lib/api/packages';

  if (!(cytoscape as unknown as { _dagreRegistered?: boolean })._dagreRegistered) {
    cytoscape.use(dagre);
    (cytoscape as unknown as { _dagreRegistered?: boolean })._dagreRegistered = true;
  }

  let {
    nodes,
    edges,
    onNodeSelect = null,
    onEdgeSelect = null,
    height = '480px'
  }: {
    nodes: GraphNode[];
    edges: GraphEdge[];
    onNodeSelect?: ((id: string, expand: boolean) => void) | null;
    onEdgeSelect?: ((edge: GraphEdge) => void) | null;
    height?: string;
  } = $props();

  let container: HTMLDivElement | undefined = $state();
  let cy: cytoscape.Core | null = null;

  const NODE_STYLE: Record<string, { color: string; shape: string; width: string }> = {
    project: { color: '#1e3a5f', shape: 'round-rectangle', width: 'label' },
    subproject: { color: '#1a4d3a', shape: 'round-rectangle', width: 'label' },
    entity: { color: '#4a3a1a', shape: 'ellipse', width: 'label' },
    req: { color: '#3a1a4a', shape: 'ellipse', width: 'label' },
    task: { color: '#4a1a3a', shape: 'ellipse', width: 'label' }
  };

  const EDGE_COLOR: Record<string, string> = {
    belongs: '#3a3a3a',
    contract: '#3b82f6',
    owns: '#555555',
    rel: '#444444',
    traces: '#7c3aed',
    nfr_of: '#ec4899',
    task_of: '#14b8a6',
    implements: '#d97706'
  };

  function cytoscapeElements() {
    return [
      ...nodes.map((n) => ({
        group: 'nodes' as const,
        data: {
          id: n.id,
          label: n.label,
          kind: n.kind,
          info: n
        },
        classes: `node-${n.kind}`
      })),
      ...edges.map((e, i) => ({
        group: 'edges' as const,
        data: {
          id: `e${i}`,
          source: e.from,
          target: e.to,
          label: e.label,
          kind: e.kind,
          info: e
        },
        classes: `edge-${e.kind}`
      }))
    ];
  }

  function buildGraph() {
    if (!container) return;
    cy?.destroy();
    // Layout adaptativo: dagre TB (jerárquico legible) en grafos chicos;
    // cose (basado en fuerzas) arriba de 60 nodos, donde dagre degenera
    // en columnas ilegibles.
    const useCose = nodes.length > 60;
    const layout: cytoscape.LayoutOptions = useCose
      ? ({
          name: 'cose',
          animate: false,
          nodeOverlap: 12,
          idealEdgeLength: () => 90,
          randomize: true
        } as cytoscape.LayoutOptions)
      : ({
          name: 'dagre',
          rankDir: 'TB',
          nodeSep: 36,
          rankSep: 70,
          animate: false
        } as cytoscape.LayoutOptions);
    cy = cytoscape({
      container,
      elements: cytoscapeElements(),
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'background-color': '#2a2a2a',
            color: '#e2e8f0',
            'font-size': '9px',
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': '110px',
            shape: 'round-rectangle',
            'border-width': 1,
            'border-color': '#666'
          }
        },
        ...Object.entries(NODE_STYLE).map(([kind, s]) => ({
          selector: `node.node-${kind}`,
          style: {
            'background-color': s.color,
            shape: s.shape as cytoscape.Css.Node['shape'],
            width: s.width,
            height: 'label',
            padding: kind === 'project' || kind === 'subproject' ? '10px' : '6px'
          }
        })),
        {
          // Sub-proyectos: el padding escala con el contenido (entidades,
          // reqs, tareas) para que el peso del nodo se lea a simple vista.
          selector: 'node.node-subproject',
          style: {
            padding: 'data(weight)'
          }
        },
        {
          selector: 'edge',
          style: {
            label: 'data(label)',
            'font-size': '7px',
            color: '#8899aa',
            'text-background-color': '#111',
            'text-background-opacity': 0.8,
            width: 1,
            'curve-style': 'bezier',
            'target-arrow-shape': 'triangle',
            'arrow-scale': 0.7,
            'line-color': '#555',
            'target-arrow-color': '#555'
          }
        },
        {
          // Solo los contratos llevan etiqueta visible (el resto viaja
          // vacío; evita el ruido "NFR" repetido en cada arista).
          selector: 'edge.edge-contract',
          style: {
            'line-color': '#3b82f6',
            'target-arrow-color': '#3b82f6',
            width: 1.6
          }
        },
        ...Object.entries(EDGE_COLOR).map(([kind, color]) => ({
          selector: `edge.edge-${kind}`,
          style: {
            'line-color': color,
            'target-arrow-color': color
          }
        })),
        {
          selector: 'node:selected',
          style: { 'border-width': 3, 'border-color': '#3b82f6' }
        },
        {
          selector: 'edge:selected',
          style: { width: 3 }
        },
        {
          // Hover: el nodo ilumina a sus vecinos y atenúa al resto.
          selector: 'node.hover',
          style: { 'border-width': 2, 'border-color': '#93c5fd' }
        },
        {
          selector: '.faded',
          style: { opacity: 0.25 }
        }
      ],
      layout,
      wheelSensitivity: 0.2
    });

    // Padding de sub-proyectos proporcional al contenido (entities/reqs).
    cy.nodes('.node-subproject').forEach((n) => {
      const ents = Number(n.data('info')?.entities ?? 0);
      const reqs = Number(n.data('info')?.reqs ?? 0);
      const weight = Math.min(40, 10 + Math.floor(Math.sqrt(ents * 6 + reqs / 4)));
      n.data('weight', weight);
    });

    cy.on('tap', 'node', (evt) => {
      const id = evt.target.id();
      onNodeSelect?.(id, false);
    });
    cy.on('cxttap', 'node', (evt) => {
      // Clic derecho = expandir vecindario del nodo.
      onNodeSelect?.(evt.target.id(), true);
    });
    cy.on('tap', 'edge', (evt) => {
      const info = evt.target.data('info') as GraphEdge;
      onEdgeSelect?.(info);
    });
    // Hover: resalta vecindario inmediato y atenúa el resto. La instancia
    // viaja en el evento (el `cy` del módulo es nullable para TS).
    cy.on('mouseover', 'node', (evt) => {
      const g: cytoscape.Core = evt.target.cy();
      const n = evt.target;
      n.addClass('hover');
      g.elements()
        .not(n.neighborhood())
        .not(n)
        .addClass('faded');
    });
    cy.on('mouseout', 'node', (evt) => {
      const g: cytoscape.Core = evt.target.cy();
      g.elements().removeClass('faded');
      g.nodes().removeClass('hover');
    });
    cy.fit(undefined, 30);
  }

  // Leyenda visible solo con pocos tipos (en el hairball no aporta).
  const legendKinds = $derived(
    [...new Set(nodes.map((n) => n.kind))].filter((k) => k in NODE_STYLE)
  );

  $effect(() => {
    // Re-render cuando cambian los datos (el padre controla el foco).
    void nodes;
    void edges;
    if (container) buildGraph();
  });

  onMount(() => {
    buildGraph();
  });

  onDestroy(() => {
    cy?.destroy();
    cy = null;
  });

  export function fit(): void {
    cy?.fit(undefined, 30);
  }
</script>

<div class="graph-wrap" style="height: {height}">
  <div bind:this={container} class="graph-container"></div>
  <div class="graph-hint">
    clic: seleccionar · doble clic: vecindario · rueda: zoom · arrastrar: mover
  </div>
  {#if legendKinds.length > 0 && nodes.length <= 80}
    <div class="graph-legend">
      {#each legendKinds as k (k)}
        <span class="legend-item">
          <span
            class="legend-swatch"
            style="background:{NODE_STYLE[k].color};border-radius:{NODE_STYLE[k].shape === 'ellipse' ? '50%' : '2px'}"
          ></span>
          {k === 'project'
            ? 'proyecto'
            : k === 'subproject'
              ? 'sub-proyecto'
              : k === 'entity'
                ? 'entidad'
                : k === 'req'
                  ? 'requerimiento'
                  : 'tarea'}
        </span>
      {/each}
      {#if edges.some((e) => e.kind === 'contract')}
        <span class="legend-item">
          <span class="legend-swatch legend-line"></span>
          contrato
        </span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .graph-wrap {
    position: relative;
    border: 1px solid var(--color-border, #333);
    background: var(--color-bg, #161618);
    border-radius: 4px;
    overflow: hidden;
  }
  .graph-container {
    width: 100%;
    height: 100%;
  }
  .graph-hint {
    position: absolute;
    bottom: 4px;
    left: 8px;
    font-size: 9px;
    font-family: monospace;
    color: #667;
    pointer-events: none;
  }
  .graph-legend {
    position: absolute;
    top: 6px;
    left: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 10px;
    max-width: 70%;
    font-size: 9px;
    font-family: monospace;
    color: #8899aa;
    pointer-events: none;
    background: rgba(22, 22, 24, 0.75);
    border: 1px solid #333;
    border-radius: 3px;
    padding: 3px 7px;
  }
  .legend-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .legend-swatch {
    display: inline-block;
    width: 9px;
    height: 9px;
  }
  .legend-line {
    height: 2px;
    width: 14px;
    background: #3b82f6;
    border-radius: 0;
  }
</style>
