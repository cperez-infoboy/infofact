<script lang="ts">
  // Nodo recursivo del árbol del workspace. Componente aparte porque
  // `<svelte:self>` está deprecado en Svelte 5: se usa un componente
  // independiente que se referencia a sí mismo por import explícito.
  //
  // Runes OK (.svelte).
  import type { TreeNode } from '$lib/api/workspaces';
  import { openTab, activeTabPath } from '$lib/stores/tabs';
  import Self from './FileTreeNode.svelte';

  let { node, depth = 0 }: { node: TreeNode; depth?: number } = $props();

  // Top-level (depth 0) expandido por defecto. Como `depth` viene de prop
  // inmutable por render, lo leemos en un derived para que Svelte 5 no
  // flaggee "state referenced locally".
  let top = $derived(depth < 1);
  let expanded = $state(true);
  $effect(() => {
    if (top) expanded = true;
  });

  let isDir = $derived(node.type === 'dir');
  let isActive = $derived($activeTabPath === node.path);
  let indent = $derived(depth * 12 + 4);

  async function handleClick() {
    if (isDir) {
      expanded = !expanded;
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
      <span class="text-xs w-3 inline-block text-text-faint"
        >{expanded ? '▾' : '▸'}</span
      >
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
