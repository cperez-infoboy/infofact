<script lang="ts">
  // Visor del SRS (Software Requirements Specification) generado desde el
  // store de RequirementItem del proyecto actual. El Markdown viene del
  // backend (build_srs en srs_builder.py) — el frontend solo lo renderiza.
  //
  // Trazabilidad: las citas literales (> "quote" (section, p.N)) ya están
  // embebidas en el Markdown; este componente las muestra tal cual. En el
  // futuro se puede añadir navegación click→fuente cuando DocViewer soporte
  // saltos por page/section.
  //
  // Runes OK (.svelte).

  import { onMount } from 'svelte';
  import { getProjectSrs, type SrsOut } from '$lib/api/projects';
  import { renderMarkdown } from '$lib/utils/markdown';

  let {
    projectId,
    onClose
  }: { projectId: number | null; onClose?: () => void } = $props();

  let loading = $state(false);
  let error = $state<string | null>(null);
  let srs = $state<SrsOut | null>(null);

  // Markdown sanitizado para inyectar en el DOM.
  let html = $derived(srs ? renderMarkdown(srs.markdown) : '');

  async function load() {
    if (projectId === null) return;
    loading = true;
    error = null;
    try {
      srs = await getProjectSrs(projectId);
    } catch (e) {
      error = (e as Error).message;
      srs = null;
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    load();
  });

  // Re-fetch si cambia el projectId.
  $effect(() => {
    if (projectId !== null) load();
  });

  // Formatea fecha ISO → legible.
  function fmtDate(iso: string): string {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <!-- Header -->
  <header
    class="h-9 flex items-center justify-between px-3 border-b border-border bg-surface-2 text-xs"
  >
    <div class="flex items-baseline gap-3 min-w-0">
      <span class="font-mono uppercase tracking-wider text-text">SRS</span>
      {#if srs}
        <span class="text-text-dim font-mono truncate">
          {srs.counts.live} vivos
          {#if srs.counts.soft_deleted > 0}
            <span class="text-text-faint">· {srs.counts.soft_deleted} históricos</span>
          {/if}
          {#if srs.counts.open_relations > 0}
            <span class="text-warning">· {srs.counts.open_relations} conflictos</span>
          {/if}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="px-2 py-0.5 text-text-dim hover:text-text font-mono disabled:opacity-50"
        onclick={load}
        disabled={loading || projectId === null}
        title="Refrescar SRS"
      >↻</button>
      {#if onClose}
        <button
          type="button"
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          onclick={onClose}
          title="Cerrar visor SRS"
          aria-label="Cerrar visor SRS">×</button
        >
      {/if}
    </div>
  </header>

  <!-- Body -->
  <div class="flex-1 min-h-0 overflow-y-auto">
    {#if projectId === null}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Selecciona un proyecto
      </div>
    {:else if loading}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Generando SRS…
      </div>
    {:else if error}
      <div
        class="m-3 p-3 border border-danger/40 bg-danger/5 text-danger text-xs font-mono"
      >
        ! {error}
      </div>
    {:else if srs}
      <article class="md-body p-6 max-w-4xl mx-auto">
        {@html html}
      </article>
      <footer
        class="px-6 py-2 border-t border-border text-[10px] font-mono text-text-faint"
      >
        Generado: {fmtDate(srs.generated_at)}
      </footer>
    {/if}
  </div>
</section>
