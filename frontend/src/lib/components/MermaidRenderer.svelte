<script lang="ts">
  // Renderer de diagramas Mermaid con zoom, pan, selector de layout
  // y modo edición opcional.
  // Los renders se serializan via una cola global porque mermaid.render()
  // NO es concurrente: múltiples llamadas simultáneas corrompen el estado
  // interno y producen "Cannot read properties of null (reading 'firstChild')".
  import { onMount } from 'svelte';
  import mermaid from 'mermaid';
  import elkLayouts from '@mermaid-js/layout-elk';

  // crypto.randomUUID() SOLO existe en contextos seguros (HTTPS/localhost).
  // Servida por HTTP en LAN lanza TypeError y el componente no se instancia
  // (el "Cargando MER" se queda colgado). Fallback determinista sin crypto.
  function genId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `id-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 10)}`;
  }

  let {
    code,
    id = genId(),
    editable = false,
    onsave = null,
  }: {
    code: string;
    id?: string;
    editable?: boolean;
    onsave?: ((code: string) => Promise<void>) | null;
  } = $props();

  let canvas: HTMLDivElement | undefined = $state();
  let viewport: HTMLDivElement | undefined = $state();
  let error: string | undefined = $state();
  let initialized = false;

  // Sanitize the id for use as a DOM element ID (Mermaid uses querySelector
  // internally — spaces and non-ASCII chars break CSS selectors).
  const safeId = $derived(
    (id || genId()).replace(/[^A-Za-z0-9_-]/g, '_')
  );

  // -----------------------------------------------------------------------
  // Zoom + Pan state
  // -----------------------------------------------------------------------
  let scale = $state(1.0);
  let tx = $state(0);
  let ty = $state(0);
  let dragging = $state(false);
  let dragStartX = 0;
  let dragStartY = 0;

  // 0.02: un MER de cientos de entidades produce SVGs de decenas de miles de
  // pixeles; ajustar el diagrama completo a la vista exige escalas de ese orden.
  const MIN_SCALE = 0.02;
  const MAX_SCALE = 4.0;

  function onWheel(e: WheelEvent) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * delta));
    // Zoom toward cursor position.
    const rect = viewport!.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    tx = cx - (cx - tx) * (newScale / scale);
    ty = cy - (cy - ty) * (newScale / scale);
    scale = newScale;
  }

  function onPointerDown(e: PointerEvent) {
    if (e.button !== 0) return;
    dragging = true;
    dragStartX = e.clientX - tx;
    dragStartY = e.clientY - ty;
    viewport!.setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging) return;
    tx = e.clientX - dragStartX;
    ty = e.clientY - dragStartY;
  }

  function onPointerUp(e: PointerEvent) {
    dragging = false;
    viewport!.releasePointerCapture(e.pointerId);
  }

  function zoomIn() {
    scale = Math.min(MAX_SCALE, scale * 1.25);
  }
  function zoomOut() {
    scale = Math.max(MIN_SCALE, scale * 0.8);
  }
  function resetView() {
    scale = 1.0;
    tx = 0;
    ty = 0;
  }
  // Ajusta el diagrama COMPLETO a la vista (ancho y alto): con MERs de
  // cientos de entidades el SVG mide decenas de miles de pixeles y ajustar
  // solo el ancho deja el resto del diagrama fuera de encuadre.
  function fitWidth() {
    if (!canvas || !viewport) return;
    const svg = canvas.querySelector('svg');
    if (!svg) return;
    const svgWidth = origSvgW || parseFloat(svg.getAttribute('width') || '0') || svg.viewBox.baseVal.width || 0;
    const svgHeight = origSvgH || parseFloat(svg.getAttribute('height') || '0') || svg.viewBox.baseVal.height || 0;
    if (svgWidth === 0) return;
    const sx = (viewport.clientWidth - 32) / svgWidth;
    const sy = svgHeight > 0 ? (viewport.clientHeight - 32) / svgHeight : 1;
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(sx, sy)));
    tx = 16;
    ty = 16;
  }

  // -----------------------------------------------------------------------
  // Layout engine selector
  // -----------------------------------------------------------------------
  type LayoutEngine = 'dagre-d3' | 'elk';
  let layoutEngine = $state<LayoutEngine>('dagre-d3');

  function onLayoutChange() {
    viewRenderCounter++;
    renderMermaid(code, `mmd-${safeId}-${viewRenderCounter}`);
    resetView();
  }

  // -----------------------------------------------------------------------
  // Edit mode state
  // -----------------------------------------------------------------------
  let editing = $state(false);
  let draft = $state('');
  let saving = $state(false);
  let saveError = $state<string | undefined>(undefined);

  // -----------------------------------------------------------------------
  // Global render queue: serializes all mermaid.render() calls across all
  // MermaidRenderer instances. Without this, concurrent renders corrupt
  // Mermaid's internal state (firstChild null error).
  // -----------------------------------------------------------------------
  let renderQueue: Promise<void> = Promise.resolve();

  function enqueueRender(fn: () => Promise<void>): Promise<void> {
    renderQueue = renderQueue.then(fn).catch(() => {});
    return renderQueue;
  }

  onMount(() => {
    mermaid.registerLayoutLoaders(elkLayouts);
    // maxTextSize/maxEdges default (50k chars / 500 aristas) truncan diagramas
    // grandes (el MER de un corpus con ~1000 requerimientos los excede).
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      maxTextSize: 500_000,
      maxEdges: 5_000
    });
    initialized = true;
  });

  /**
   * Sanitize Mermaid code: strip trailing semicolons from classDef/class
   * statements. They are optional in flowcharts/state diagrams but cause
   * parse errors in erDiagram. This keeps existing stored diagrams working.
   * Also prepend a layout init directive for the selected engine.
   */
  function prepareCode(rawCode: string): string {
    // Strip any existing init directive first.
    let code = rawCode.replace(/^%%\{init:.*?\}%%\s*\n?/i, '');
    // Remove trailing semicolons from classDef and class lines.
    code = code.replace(
      /^(\s*(?:classDef|class)\s+.+?);(\s*)$/gm,
      '$1$2',
    );
    if (layoutEngine === 'elk') {
      code = `%%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%\n${code}`;
    }
    return code;
  }

  // Original SVG dimensions — captured after each render so we can scale
  // the SVG element itself (vector-crisp) instead of using CSS scale (raster).
  let origSvgW = 0;
  let origSvgH = 0;

  function applySvgScale() {
    const svgEl = canvas?.querySelector('svg');
    if (!svgEl || origSvgW === 0) return;
    svgEl.setAttribute('width', String(origSvgW * scale));
    svgEl.setAttribute('height', String(origSvgH * scale));
  }

  function renderMermaid(renderCode: string, renderId: string): Promise<void> {
    return enqueueRender(async () => {
      if (!canvas || !initialized || !renderCode) return;
      error = undefined;

      try {
        const effectiveCode = prepareCode(renderCode);
        const { svg } = await mermaid.render(renderId, effectiveCode);
        if (canvas) {
          canvas.innerHTML = svg;
          // Capture original dimensions for vector zoom.
          const svgEl = canvas.querySelector('svg');
          if (svgEl) {
            // Mermaid 11 sets width="100%" with max-width in style.
            // Parse the real pixel width BEFORE removing constraints.
            const maxW = parseFloat(svgEl.style.maxWidth) || 0;
            const vbW = svgEl.viewBox.baseVal.width || 0;
            const vbH = svgEl.viewBox.baseVal.height || 0;
            const attrW = svgEl.getAttribute('width') || '';
            const attrH = svgEl.getAttribute('height') || '';
            // Use viewBox (always in pixel units for Mermaid) as primary source,
            // fall back to max-width style, then attributes (only if not %).
            origSvgW = vbW || maxW || (attrW && !attrW.includes('%') ? parseFloat(attrW) : 0) || 0;
            origSvgH = vbH || (attrH && !attrH.includes('%') ? parseFloat(attrH) : 0) || 0;
            // Remove constraints so the SVG can scale freely.
            svgEl.style.maxWidth = 'none';
            svgEl.style.maxHeight = 'none';
            applySvgScale();
          }
        }
      } catch (err) {
        error = err instanceof Error ? err.message : String(err);
        // Clean up any DOM artifacts from the failed render.
        document.querySelectorAll(`#${renderId}`).forEach((el) => el.remove());
      }
    });
  }

  // Re-apply vector scaling when scale changes (crisp at every zoom level).
  $effect(() => {
    const _ = scale; // track dependency
    applySvgScale();
  });

  // Render the main code (view mode)
  let viewRenderCounter = 0;
  $effect(() => {
    const renderCode = code;
    if (initialized && renderCode && !editing) {
      viewRenderCounter++;
      renderMermaid(renderCode, `mmd-${safeId}-${viewRenderCounter}`);
    }
  });

  // Live preview in edit mode (debounced)
  let previewTimer: ReturnType<typeof setTimeout> | undefined;
  let editRenderCounter = 0;
  $effect(() => {
    if (!editing || !draft) return;
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      editRenderCounter++;
      renderMermaid(draft, `mmd-edit-${safeId}-${editRenderCounter}`);
    }, 500);
  });

  function startEdit() {
    draft = code;
    editing = true;
    saveError = undefined;
  }

  function cancelEdit() {
    editing = false;
    draft = '';
    error = undefined;
    if (initialized && code) {
      viewRenderCounter++;
      renderMermaid(code, `mmd-${safeId}-${viewRenderCounter}`);
    }
  }

  async function save() {
    if (!onsave) return;
    saving = true;
    saveError = undefined;
    try {
      await onsave(draft);
      editing = false;
    } catch (err) {
      saveError = err instanceof Error ? err.message : 'Error al guardar';
    } finally {
      saving = false;
    }
  }

  const dirty = $derived(draft !== code);
  const zoomPct = $derived(Math.round(scale * 100));
</script>

{#if editing}
  <div class="mermaid-edit">
    <textarea bind:value={draft} class="mermaid-textarea" spellcheck="false"></textarea>
    <div class="mermaid-edit-controls">
      <button onclick={save} disabled={!onsave || saving || !dirty} class="btn-save">
        {saving ? 'Guardando…' : 'Guardar'}
      </button>
      <button onclick={cancelEdit} class="btn-cancel" disabled={saving}>Cancelar</button>
    </div>
    {#if saveError}
      <p class="mermaid-save-error">{saveError}</p>
    {/if}
    {#if error}
      <div class="mermaid-error">
        <p>Error de sintaxis:</p>
        <pre>{error}</pre>
      </div>
    {/if}
    {#if !error}
      <div bind:this={canvas} class="mermaid-preview"></div>
    {/if}
  </div>
{:else}
  {#if error}
    <div class="mermaid-error">
      <p>Error al renderizar el diagrama:</p>
      <pre>{error}</pre>
      {#if editable}
        <button onclick={startEdit} class="btn-edit">Editar código</button>
      {/if}
      <details>
        <summary>Código fuente</summary>
        <pre>{code}</pre>
      </details>
    </div>
  {:else}
    <div class="mermaid-wrapper">
      <div class="mermaid-toolbar">
        <button onclick={zoomOut} title="Alejar">−</button>
        <span class="zoom-level">{zoomPct}%</span>
        <button onclick={zoomIn} title="Acercar">+</button>
        <button onclick={fitWidth} title="Ajustar a la vista">⤢</button>
        <button onclick={resetView} title="Restablecer">⟲</button>
        <select
          value={layoutEngine}
          onchange={(e) => {
            layoutEngine = (e.target as HTMLSelectElement).value as LayoutEngine;
            onLayoutChange();
          }}
          title="Motor de layout"
        >
          <option value="dagre-d3">Jerárquico</option>
          <option value="elk">Adaptativo</option>
        </select>
        {#if editable}
          <button onclick={startEdit} title="Editar código Mermaid">✎</button>
        {/if}
      </div>
      <div
        bind:this={viewport}
        class="mermaid-viewport"
        class:dragging
        onwheel={onWheel}
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}
        ondblclick={resetView}
        role="application"
        aria-label="Diagrama interactivo: scroll para zoom, arrastrar para mover"
      >
        <div
          bind:this={canvas}
          class="mermaid-canvas"
          style="transform: translate({tx}px, {ty}px)"
        ></div>
      </div>
    </div>
  {/if}
{/if}

<style>
  .mermaid-wrapper {
    position: relative;
  }
  .mermaid-viewport {
    overflow: hidden;
    height: 60vh;
    position: relative;
    cursor: grab;
    user-select: none;
    touch-action: none;
  }
  .mermaid-viewport.dragging {
    cursor: grabbing;
  }
  .mermaid-canvas {
    transform-origin: 0 0;
  }
  .mermaid-toolbar {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 0.25rem;
    background: rgba(30, 30, 30, 0.9);
    border: 1px solid #333;
    border-radius: 0.25rem;
    padding: 0.2rem 0.4rem;
    font-size: 0.75rem;
  }
  .mermaid-toolbar button {
    background: transparent;
    border: 1px solid transparent;
    color: #888;
    cursor: pointer;
    padding: 0.1rem 0.35rem;
    border-radius: 0.2rem;
    font-size: 0.8rem;
    line-height: 1;
  }
  .mermaid-toolbar button:hover {
    color: #58a6ff;
    border-color: #444;
  }
  .mermaid-toolbar select {
    background: transparent;
    border: 1px solid #333;
    color: #888;
    font-size: 0.7rem;
    padding: 0.1rem 0.2rem;
    border-radius: 0.2rem;
    cursor: pointer;
  }
  .mermaid-toolbar select:hover {
    border-color: #58a6ff;
  }
  .zoom-level {
    color: #888;
    font-family: monospace;
    min-width: 3rem;
    text-align: center;
  }
  .mermaid-edit {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .mermaid-textarea {
    width: 100%;
    min-height: 200px;
    background: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #333;
    border-radius: 0.25rem;
    padding: 0.5rem;
    font-family: monospace;
    font-size: 0.875rem;
    resize: vertical;
  }
  .mermaid-textarea:focus {
    outline: none;
    border-color: #58a6ff;
  }
  .mermaid-edit-controls {
    display: flex;
    gap: 0.5rem;
  }
  .btn-save,
  .btn-cancel {
    padding: 0.3rem 0.75rem;
    border-radius: 0.25rem;
    border: 1px solid #333;
    cursor: pointer;
    font-size: 0.75rem;
  }
  .btn-save {
    background: #238636;
    color: #fff;
    border-color: #238636;
  }
  .btn-save:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .btn-cancel {
    background: transparent;
    color: #888;
  }
  .mermaid-preview {
    overflow-x: auto;
    padding: 1rem;
    border: 1px dashed #444;
    border-radius: 0.25rem;
    margin-top: 0.5rem;
  }
  .mermaid-error {
    padding: 1rem;
    color: #f87171;
  }
  .mermaid-error pre {
    background: #1e1e1e;
    padding: 0.5rem;
    border-radius: 0.25rem;
    overflow-x: auto;
    font-size: 0.875rem;
  }
  .btn-edit {
    background: #1e1e1e;
    border: 1px solid #444;
    color: #58a6ff;
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0.3rem 0.75rem;
    border-radius: 0.25rem;
    margin: 0.5rem 0;
  }
  .btn-edit:hover {
    border-color: #58a6ff;
  }
  .mermaid-save-error {
    color: #f87171;
    font-size: 0.75rem;
  }
</style>
