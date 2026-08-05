<script lang="ts">
  // Explorador de archivos: árbol del workspace con refresh, polling y upload.
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
  import { uploadDocument } from '$lib/api/documents';
  import FileTreeNode from './FileTreeNode.svelte';

  let { } = $props();

  let fileInput = $state<HTMLInputElement | null>(null);
  let uploading = $state(false);
  let uploadError = $state<string | null>(null);

  onMount(() => {
    startPolling(5000);
  });

  // Refresca el árbol cuando cambia el proyecto activo (y al montar
  // si ya hay proyecto seleccionado). $effect se ejecuta post-mount,
  // así que cubre el primer render sin duplicar el fetch de onMount.
  $effect(() => {
    const id = $currentProjectId;
    if (id !== null) refreshTree('.', true);
  });

  onDestroy(() => {
    stopPolling();
  });

  async function handleFiles(files: FileList | null): Promise<void> {
    const id = $currentProjectId;
    if (id === null || !files || files.length === 0) return;
    uploading = true;
    uploadError = null;
    try {
      for (const f of Array.from(files)) {
        await uploadDocument(id, f);
      }
      await refreshTree('.', true);
    } catch (e) {
      uploadError = (e as Error).message;
    } finally {
      uploading = false;
    }
  }

  function onPick(e: Event): void {
    const input = e.target as HTMLInputElement;
    void handleFiles(input.files);
    // Resetea para permitir subir el mismo archivo dos veces seguidas.
    input.value = '';
  }

  function onDrop(e: DragEvent): void {
    e.preventDefault();
    void handleFiles(e.dataTransfer?.files ?? null);
  }

  function onDragOver(e: DragEvent): void {
    e.preventDefault();
  }
</script>

<aside
  class="h-full flex flex-col bg-surface text-text border-r border-border"
  ondragover={onDragOver}
  ondrop={onDrop}
>
  <div class="h-9 flex items-center justify-between px-3 border-b border-border">
    <span class="text-xs uppercase tracking-wider text-text-faint font-mono"
      >Explorador</span
    >
    <div class="flex items-center gap-1">
      <input
        bind:this={fileInput}
        type="file"
        multiple
        class="hidden"
        onchange={onPick}
      />
      <button
        type="button"
        class="text-xs px-2 py-0.5 hover:bg-surface-3 hover:text-text text-text-dim transition-colors disabled:opacity-40"
        onclick={() => fileInput?.click()}
        disabled={$workspaceLoading || uploading}
        title="Subir documentos"
        aria-label="Subir documentos">↑</button
      >
      <button
        type="button"
        class="text-xs px-2 py-0.5 hover:bg-surface-3 hover:text-text text-text-dim transition-colors disabled:opacity-40"
        onclick={() => refreshTree('.', true)}
        disabled={$workspaceLoading || uploading}
        title="Refrescar"
        aria-label="Refrescar árbol">↻</button
      >
    </div>
  </div>

  {#if $workspaceError}
    <div
      class="px-3 py-2 text-xs text-danger bg-danger/5 border-b border-danger/30"
    >
      ! {$workspaceError}
    </div>
  {/if}
  {#if uploadError}
    <div
      class="px-3 py-2 text-xs text-danger bg-danger/5 border-b border-danger/30"
    >
      ! {uploadError}
    </div>
  {/if}

  <div class="flex-1 overflow-auto py-1">
    {#if uploading}
      <div class="px-3 py-2 text-xs text-text-dim font-mono">subiendo…</div>
    {:else if $tree === null}
      <div class="px-3 py-4 text-xs text-text-dim font-mono">
        {$workspaceLoading ? 'cargando…' : 'workspace vacío'}
      </div>
    {:else}
      <FileTreeNode node={$tree} />
    {/if}
  </div>
</aside>
