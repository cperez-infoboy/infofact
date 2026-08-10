<script lang="ts">
  // Renderer de diagramas Mermaid con modo edición opcional.
  // Los renders se serializan via una cola global porque mermaid.render()
  // NO es concurrente: múltiples llamadas simultáneas corrompen el estado
  // interno y producen "Cannot read properties of null (reading 'firstChild')".
  import { onMount } from 'svelte';
  import mermaid from 'mermaid';

  let {
    code,
    id = crypto.randomUUID(),
    editable = false,
    onsave = null,
  }: {
    code: string;
    id?: string;
    editable?: boolean;
    onsave?: ((code: string) => Promise<void>) | null;
  } = $props();

  let container: HTMLDivElement | undefined = $state();
  let error: string | undefined = $state();
  let initialized = false;

  // Sanitize the id for use as a DOM element ID (Mermaid uses querySelector
  // internally — spaces and non-ASCII chars break CSS selectors).
  const safeId = $derived(
    (id || crypto.randomUUID()).replace(/[^A-Za-z0-9_-]/g, '_')
  );

  // Edit mode state
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
    mermaid.initialize({ startOnLoad: false, theme: 'dark' });
    initialized = true;
  });

  function renderMermaid(renderCode: string, renderId: string): Promise<void> {
    return enqueueRender(async () => {
      if (!container || !initialized || !renderCode) return;
      error = undefined;

      try {
        const { svg } = await mermaid.render(renderId, renderCode);
        if (container) container.innerHTML = svg;
      } catch (err) {
        error = err instanceof Error ? err.message : String(err);
        // Clean up any DOM artifacts from the failed render.
        document.querySelectorAll(`#${renderId}`).forEach((el) => el.remove());
      }
    });
  }

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
      <div bind:this={container} class="mermaid-preview"></div>
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
      {#if editable}
        <button onclick={startEdit} class="btn-edit-top" title="Editar código Mermaid">
          ✎
        </button>
      {/if}
      <div bind:this={container} class="mermaid-container"></div>
    </div>
  {/if}
{/if}

<style>
  .mermaid-wrapper {
    position: relative;
  }
  .mermaid-container {
    overflow-x: auto;
    padding: 1rem;
  }
  .btn-edit-top {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    background: #1e1e1e;
    border: 1px solid #333;
    color: #888;
    cursor: pointer;
    font-size: 1rem;
    padding: 0.2rem 0.5rem;
    border-radius: 0.25rem;
    z-index: 1;
    transition: color 0.15s, border-color 0.15s;
  }
  .btn-edit-top:hover {
    color: #58a6ff;
    border-color: #58a6ff;
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
