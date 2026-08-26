<script lang="ts">
  // Popup de autocompletado de archivos para el chat (mención "@ruta").
  // Presentacional: lista plana filtrada + índice activo que maneja el padre
  // (ChatPanel), que también resuelve el teclado. Anclado sobre el textarea,
  // dentro de su wrapper `relative`. El focus nunca sale del textarea: los
  // clicks prevent-ean mousedown y confirman en click.
  import FileIcon from './FileIcon.svelte';
  import type { SearchEntry } from '$lib/api/workspaces';

  let {
    items,
    activeIndex,
    loading = false,
    onpick,
    onhover
  }: {
    items: SearchEntry[];
    activeIndex: number;
    loading?: boolean;
    onpick: (item: SearchEntry) => void;
    onhover: (index: number) => void;
  } = $props();

  /** Directorio padre de un path relativo ('docs/a.md' → 'docs'). */
  function parentOf(path: string): string {
    const idx = path.lastIndexOf('/');
    return idx <= 0 ? '' : path.slice(0, idx);
  }

  // Mantiene visible la fila activa cuando se navega con flechas.
  let listEl: HTMLDivElement | null = $state(null);
  $effect(() => {
    // Lectura de `activeIndex` = dependencia reactiva.
    void activeIndex;
    listEl
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  });
</script>

<div
  bind:this={listEl}
  role="listbox"
  aria-label="Archivos del workspace"
  class="absolute bottom-full left-0 right-0 mb-1 z-20 max-h-64 overflow-y-auto bg-surface border border-border-strong rounded-md shadow-lg"
>
  {#if loading && items.length === 0}
    <div class="px-2 py-2 text-xs text-text-faint font-mono">buscando…</div>
  {:else if items.length === 0}
    <div class="px-2 py-2 text-xs text-text-faint font-mono">
      sin coincidencias
    </div>
  {:else}
    {#each items as item, i (item.path)}
      <button
        type="button"
        role="option"
        aria-selected={i === activeIndex}
        onmousedown={(e) => e.preventDefault()}
        onmouseenter={() => onhover(i)}
        onclick={() => onpick(item)}
        class="w-full flex items-center gap-2 px-2 py-1 text-left text-xs hover:bg-surface-3 {i
          === activeIndex
          ? 'bg-surface-3'
          : ''}"
      >
        <FileIcon name={item.name} kind={item.type} />
        <span class="text-text truncate">{item.name}</span>
        {#if parentOf(item.path)}
          <span class="ml-auto text-text-faint font-mono truncate pl-2">
            {parentOf(item.path)}
          </span>
        {/if}
      </button>
    {/each}
  {/if}
</div>
