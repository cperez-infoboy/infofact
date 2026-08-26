<script lang="ts">
  // Explorador de archivos: árbol del workspace con refresh, polling, upload
  // y gestión completa (crear/renombrar/mover/copiar/pegar/eliminar/descargar)
  // via menú contextual, botones de header y drag&drop interno.
  // Runes OK (.svelte).
  import { onMount, onDestroy } from 'svelte';
  import {
    tree,
    workspaceLoading,
    workspaceError,
    refreshTree,
    startPolling,
    stopPolling,
    pendingCreate,
    clipboardPath,
    selectedPath,
    selectedIsDir,
    renamingPath,
    opsBusy,
    parentOf,
    deleteEntry,
    moveEntryInto,
    copyEntryInto,
    humanizeWorkspaceError,
    DND_MIME
  } from '$lib/stores/workspace';
  import { currentProjectId } from '$lib/stores/project';
  import { uploadDocument } from '$lib/api/documents';
  import { downloadFile as downloadWsFile, type TreeNode } from '$lib/api/workspaces';
  import { openTab } from '$lib/stores/tabs';
  import FileTreeNode from './FileTreeNode.svelte';
  import ContextMenu, { type MenuItem } from './ContextMenu.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';

  let { } = $props();

  let fileInput = $state<HTMLInputElement | null>(null);
  let uploading = $state(false);
  let uploadError = $state<string | null>(null);
  let rootDrop = $state(false);

  // Menú contextual y confirmación de borrado.
  let menu = $state<{ x: number; y: number; node: TreeNode } | null>(null);
  let confirmTarget = $state<TreeNode | null>(null);

  onMount(() => {
    startPolling(5000);
  });

  // Refresca el árbol cuando cambia el proyecto activo (y al montar
  // si ya hay proyecto seleccionado).
  $effect(() => {
    const id = $currentProjectId;
    if (id !== null) refreshTree('.', true);
  });

  onDestroy(() => {
    stopPolling();
  });

  // --- Upload de archivos del SO (drag&drop al panel o botón ↑) ---

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

  // Drop sobre el panel: si trae el MIME interno es mover a la raíz;
  // si trae archivos del SO, es upload.
  function onDrop(e: DragEvent): void {
    e.preventDefault();
    rootDrop = false;
    const src = e.dataTransfer?.getData(DND_MIME);
    if (src) {
      void moveEntryInto(src, '.');
      return;
    }
    void handleFiles(e.dataTransfer?.files ?? null);
  }

  function onDragOver(e: DragEvent): void {
    e.preventDefault();
    if (e.dataTransfer?.types.includes(DND_MIME)) rootDrop = true;
  }

  // --- Acciones del header ---

  // Botones de header actúan sobre la carpeta seleccionada (o la raíz).
  function targetDirForCreate(): string {
    return $selectedIsDir && $selectedPath ? $selectedPath : '.';
  }

  function newFile(): void {
    pendingCreate.set({ parentPath: targetDirForCreate(), kind: 'file' });
  }

  function newFolder(): void {
    pendingCreate.set({ parentPath: targetDirForCreate(), kind: 'dir' });
  }

  // --- Menú contextual ---

  function openNodeMenu(node: TreeNode, x: number, y: number): void {
    menu = { x, y, node };
  }

  // Fondo del árbol (no un nodo): menú de la raíz.
  function onBgContextMenu(e: MouseEvent): void {
    if (e.target !== e.currentTarget) return;
    if (!$tree) return;
    e.preventDefault();
    openNodeMenu($tree, e.clientX, e.clientY);
  }

  async function doDownload(node: TreeNode): Promise<void> {
    const id = $currentProjectId;
    if (id === null) return;
    try {
      await downloadWsFile(id, node.path);
    } catch (e) {
      workspaceError.set((e as Error).message);
    }
  }

  function requestDelete(node: TreeNode): void {
    confirmTarget = node;
  }

  function doDropInto(src: string, dstDir: string): void {
    void moveEntryInto(src, dstDir);
  }

  let menuItems = $derived.by(() => {
    const n = menu?.node;
    if (!n) return [] as MenuItem[];
    const isRoot = n.path === '.';
    const items: MenuItem[] = [];

    if (isRoot) {
      items.push(
        { label: 'Nuevo archivo', action: () => pendingCreate.set({ parentPath: '.', kind: 'file' }) },
        { label: 'Nueva carpeta', action: () => pendingCreate.set({ parentPath: '.', kind: 'dir' }) },
        { separator: true, label: '' },
        { label: 'Pegar', disabled: !$clipboardPath, action: () => { if ($clipboardPath) void copyEntryInto($clipboardPath, '.'); } },
        { separator: true, label: '' },
        { label: 'Refrescar', action: () => void refreshTree('.', true) }
      );
      return items;
    }

    if (n.type === 'file') {
      items.push(
        { label: 'Abrir', action: () => void openTab(n.path) },
        { label: 'Descargar', action: () => void doDownload(n) },
        { separator: true, label: '' },
        { label: 'Renombrar', action: () => renamingPath.set(n.path) },
        { label: 'Duplicar', action: () => void copyEntryInto(n.path, parentOf(n.path)) },
        { label: 'Copiar', action: () => clipboardPath.set(n.path) },
        { separator: true, label: '' },
        { label: 'Eliminar', danger: true, action: () => requestDelete(n) }
      );
      return items;
    }

    // Carpeta (no raíz)
    items.push(
      { label: 'Nuevo archivo', action: () => pendingCreate.set({ parentPath: n.path, kind: 'file' }) },
      { label: 'Nueva carpeta', action: () => pendingCreate.set({ parentPath: n.path, kind: 'dir' }) },
      { separator: true, label: '' },
      { label: 'Renombrar', action: () => renamingPath.set(n.path) },
      { label: 'Duplicar', action: () => void copyEntryInto(n.path, parentOf(n.path)) },
      { label: 'Copiar', action: () => clipboardPath.set(n.path) },
      { label: 'Pegar', disabled: !$clipboardPath, action: () => { if ($clipboardPath) void copyEntryInto($clipboardPath, n.path); } },
      { separator: true, label: '' },
      { label: 'Eliminar', danger: true, action: () => requestDelete(n) }
    );
    return items;
  });
</script>

<aside
  class="h-full flex flex-col bg-surface text-text border-r border-border
         {rootDrop ? 'ring-1 ring-inset ring-border-strong' : ''}"
  ondragover={onDragOver}
  ondragleave={() => (rootDrop = false)}
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
        class="p-1 hover:bg-surface-3 hover:text-text text-text-dim transition-colors disabled:opacity-40"
        onclick={newFile}
        disabled={$workspaceLoading || $opsBusy || uploading}
        title="Nuevo archivo (en la carpeta seleccionada)"
        aria-label="Nuevo archivo"
      >
        <svg viewBox="0 0 16 16" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M4 1.5h5l3 3v10H4z" /><path d="M9 1.5v3h3" /><path d="M8 7v4M6 9h4" />
        </svg>
      </button>
      <button
        type="button"
        class="p-1 hover:bg-surface-3 hover:text-text text-text-dim transition-colors disabled:opacity-40"
        onclick={newFolder}
        disabled={$workspaceLoading || $opsBusy || uploading}
        title="Nueva carpeta (en la carpeta seleccionada)"
        aria-label="Nueva carpeta"
      >
        <svg viewBox="0 0 16 16" class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M1.5 13V4.5A1.5 1.5 0 0 1 3 3h3.2l1.1 1.1h4.3A1.5 1.5 0 0 1 14 6v7H1.5z" /><path d="M8 7v4M6 9h4" />
        </svg>
      </button>
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
      ! {humanizeWorkspaceError($workspaceError)}
    </div>
  {/if}
  {#if uploadError}
    <div
      class="px-3 py-2 text-xs text-danger bg-danger/5 border-b border-danger/30"
    >
      ! {humanizeWorkspaceError(uploadError)}
    </div>
  {/if}

  <!-- svelte-ignore a11y_no_static_element_interactions — el fondo abre el
       menú de la raíz; los nodos manejan su propio contexto. -->
  <div
    class="flex-1 overflow-auto py-1 font-mono"
    oncontextmenu={onBgContextMenu}
  >
    {#if uploading}
      <div class="px-3 py-2 text-xs text-text-dim">subiendo…</div>
    {:else if $tree === null}
      <div class="px-3 py-4 text-xs text-text-dim">
        {$workspaceLoading ? 'cargando…' : 'workspace vacío'}
      </div>
    {:else}
      <!-- La creación en la raíz la renderiza FileTreeNode como primer hijo
           del nodo raíz (mismo camino que cualquier carpeta). -->
      <FileTreeNode
        node={$tree}
        oncontext={openNodeMenu}
        ondelete={requestDelete}
        ondropinto={doDropInto}
      />
    {/if}
  </div>
</aside>

{#if menu}
  <ContextMenu items={menuItems} x={menu.x} y={menu.y} onclose={() => (menu = null)} />
{/if}

{#if confirmTarget}
  {@const target = confirmTarget}
  <ConfirmDialog
    title={target.type === 'dir' ? 'Eliminar carpeta' : 'Eliminar archivo'}
    message={'¿Eliminar definitivamente "' + target.name + '"'
      + (target.type === 'dir' ? ' y todo su contenido?' : '?')}
    confirmLabel="Eliminar"
    danger={true}
    onconfirm={() => {
      confirmTarget = null;
      void deleteEntry(target);
    }}
    oncancel={() => (confirmTarget = null)}
  />
{/if}
