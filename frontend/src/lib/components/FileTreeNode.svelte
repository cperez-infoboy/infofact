<script lang="ts">
  // Nodo recursivo del árbol del workspace con carga lazy.
  //
  // Las carpetas cuyos hijos no se cargaron (loaded=false) los traen del
  // backend al expandirse por primera vez (loadChildren). Componente
  // aparte porque `<svelte:self>` está deprecado en Svelte 5: se usa un
  // componente independiente que se referencia a sí mismo por import.
  //
  // Runes OK (.svelte).
  import type { TreeNode } from '$lib/api/workspaces';
  import { openTab, activeTabId } from '$lib/stores/tabs';
  import { loadChildren, loadingPaths } from '$lib/stores/workspace';
  import Self from './FileTreeNode.svelte';

  let { node, depth = 0 }: { node: TreeNode; depth?: number } = $props();

  // Top-level (depth 0) arranca expandido; el resto colapsado, para
  // soportar lazy loading (no expandir lo que aún no se cargó). `depth`
  // es un prop inmutable por render: solo interesa su valor inicial
  // como semilla de $state, no su reactividad.
  // svelte-ignore state_referenced_locally
  let expanded = $state(depth < 1);

  let isDir = $derived(node.type === 'dir');
  let isActive = $derived($activeTabId === node.path);
  let indent = $derived(depth * 12 + 4);
  let loaded = $derived(node.loaded ?? false);
  let isLoading = $derived($loadingPaths.has(node.path));

  async function handleClick() {
    if (isDir) {
      const willExpand = !expanded;
      expanded = willExpand;
      // Si se expande y los hijos no están cargados, traerlos.
      if (willExpand && !loaded) {
        await loadChildren(node.path);
      }
      return;
    }
    await openTab(node.path);
  }
</script>

<div class="select-none">
  <button
    type="button"
    class="w-full flex items-center gap-1 text-left text-sm px-1 py-0.5 transition-colors
           hover:bg-surface-3 hover:text-text
           {isActive
      ? 'bg-surface-2 text-text border-l-2 border-l-border-strong'
      : 'text-text border-l-2 border-l-transparent'}"
    style="padding-left: {indent}px"
    onclick={handleClick}
    aria-expanded={isDir ? expanded : undefined}
  >
    {#if isDir}
      <span class="text-xs w-3 inline-block text-text-faint">
        {#if isLoading}
          ⟳
        {:else if expanded}
          ▾
        {:else}
          ▸
        {/if}
      </span>
    {:else}
      <span class="text-xs w-3 inline-block"></span>
    {/if}
    <span class="truncate">{node.name}</span>
  </button>

  {#if isDir && expanded && node.children}
    {#each node.children as child (child.path)}
      <Self node={child} depth={depth + 1} />
    {/each}
  {/if}
</div>
