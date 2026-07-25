<script lang="ts">
  // Explorador de archivos: árbol del workspace con refresh + polling.
  // Runes OK (.svelte).
  import { onMount, onDestroy } from 'svelte';
  import {
    tree,
    workspaceLoading,
    workspaceError,
    refreshTree,
    startPolling,
    stopPolling
  } from '$lib/stores/workspace';
  import { currentProjectId } from '$lib/stores/project';
  import FileTreeNode from './FileTreeNode.svelte';

  let { } = $props();

  onMount(() => {
    startPolling(5000);
  });

  // Refresca el árbol cuando cambia el proyecto activo (y al montar
  // si ya hay proyecto seleccionado). $effect se ejecuta post-mount,
  // así que cubre el primer render sin duplicar el fetch de onMount.
  $effect(() => {
    const id = $currentProjectId;
    if (id !== null) refreshTree();
  });

  onDestroy(() => {
    stopPolling();
  });
</script>

<aside class="h-full flex flex-col bg-surface text-text border-r border-border">
  <div class="h-9 flex items-center justify-between px-3 border-b border-border">
    <span class="text-xs uppercase tracking-wider text-text-faint font-mono"
      >Explorador</span
    >
    <button
      type="button"
      class="text-xs px-2 py-0.5 hover:bg-surface-3 hover:text-text text-text-dim transition-colors"
      onclick={() => refreshTree()}
      disabled={$workspaceLoading}
      title="Refrescar"
      aria-label="Refrescar árbol"
      >↻</button
    >
  </div>

  {#if $workspaceError}
    <div
      class="px-3 py-2 text-xs text-danger bg-danger/5 border-b border-danger/30"
    >
      ! {$workspaceError}
    </div>
  {/if}

  <div class="flex-1 overflow-auto py-1">
    {#if $tree === null}
      <div class="px-3 py-4 text-xs text-text-dim font-mono">
        {$workspaceLoading ? 'cargando…' : 'workspace vacío'}
      </div>
    {:else}
      <FileTreeNode node={$tree} />
    {/if}
  </div>
</aside>
