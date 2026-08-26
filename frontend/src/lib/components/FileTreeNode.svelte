<script module lang="ts">
  // Path arrastrado actualmente (nivel módulo: compartido por todas las
  // instancias recursivas; dataTransfer no se puede leer en dragover).
  let draggedPath: string | null = null;
</script>

<script lang="ts">
  // Nodo recursivo del árbol del workspace con carga lazy.
  //
  // Además de expandir/abrir: selección, menú contextual (via callback al
  // FileExplorer), renombrado y creación inline, drag&drop interno para
  // mover (MIME custom), indent guides por nivel y tipografía mono.
  //
  // Componente aparte porque `<svelte:self>` está deprecado en Svelte 5: se
  // usa un componente independiente que se referencia a sí mismo por import.
  //
  // Runes OK (.svelte).
  import type { TreeNode } from '$lib/api/workspaces';
  import { openTab, activeTabId } from '$lib/stores/tabs';
  import {
    loadChildren,
    loadingPaths,
    selectedPath,
    renamingPath,
    pendingCreate,
    selectNode,
    createEntry,
    renameEntry,
    DND_MIME
  } from '$lib/stores/workspace';
  import FileIcon from './FileIcon.svelte';
  import ExplorerInlineInput from './ExplorerInlineInput.svelte';
  import Self from './FileTreeNode.svelte';

  let {
    node,
    depth = 0,
    oncontext,
    ondelete,
    ondropinto
  }: {
    node: TreeNode;
    depth?: number;
    oncontext: (node: TreeNode, x: number, y: number) => void;
    ondelete: (node: TreeNode) => void;
    ondropinto: (src: string, dstDir: string) => void;
  } = $props();

  // Top-level (depth 0) arranca expandido; el resto colapsado, para
  // soportar lazy loading (no expandir lo que aún no se cargó).
  // svelte-ignore state_referenced_locally
  let expanded = $state(depth < 1);
  let dropTarget = $state(false);

  let isDir = $derived(node.type === 'dir');
  let isRoot = $derived(node.path === '.');
  let isActive = $derived($activeTabId === node.path);
  let isSelected = $derived($selectedPath === node.path);
  let loaded = $derived(node.loaded ?? false);
  let isLoading = $derived($loadingPaths.has(node.path));
  let renaming = $derived($renamingPath === node.path);
  let creating = $derived($pendingCreate?.parentPath === node.path);
  let createKind = $derived(creating ? $pendingCreate!.kind : 'file');

  // Creación inline: forzar expansión y carga de la carpeta destino.
  $effect(() => {
    if (creating && isDir) {
      expanded = true;
      if (!loaded) void loadChildren(node.path);
    }
  });

  async function handleClick() {
    selectNode(node.path, isDir);
    if (isDir) {
      expanded = !expanded;
      if (expanded && !loaded) await loadChildren(node.path);
      return;
    }
    await openTab(node.path);
  }

  function onContextMenu(e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    selectNode(node.path, isDir);
    oncontext(node, e.clientX, e.clientY);
  }

  function onKeydown(e: KeyboardEvent) {
    if (renaming) return;
    if (e.key === 'F2' && !isRoot) {
      e.preventDefault();
      renamingPath.set(node.path);
    } else if (e.key === 'Delete' && !isRoot) {
      e.preventDefault();
      ondelete(node);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      void handleClick();
    }
  }

  // --- Drag & drop interno (mover) ---

  function canDropInto(): boolean {
    if (!draggedPath || !isDir) return false;
    if (draggedPath === node.path) return false;
    // No soltar dentro de sí mismo ni de un descendiente.
    if (node.path !== '.' && node.path.startsWith(draggedPath + '/')) return false;
    return true;
  }

  function onDragStart(e: DragEvent) {
    if (isRoot || renaming) {
      e.preventDefault();
      return;
    }
    draggedPath = node.path;
    e.dataTransfer?.setData(DND_MIME, node.path);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }

  function onDragOverNode(e: DragEvent) {
    // Drag interno que este nodo no acepta (p.ej. una fila de archivo):
    // cortar la propagación para que el drop no caiga al panel (raíz).
    if (draggedPath !== null && !canDropInto()) {
      e.stopPropagation();
      return;
    }
    if (!canDropInto()) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    dropTarget = true;
  }

  function onDropNode(e: DragEvent) {
    dropTarget = false;
    const src = e.dataTransfer?.getData(DND_MIME);
    if (!src || !canDropInto()) return;
    e.preventDefault();
    e.stopPropagation();
    ondropinto(src, node.path);
  }
</script>

<div class="select-none">
  {#if renaming}
    <!-- Fila de renombrado: mismas guías de indentación + icono + input. -->
    <div class="flex items-center gap-1 h-6 pr-2">
      {#each Array(depth) as _, i (i)}
        <span class="w-4 shrink-0 self-stretch border-l border-border-strong/40"></span>
      {/each}
      <span class="w-4 shrink-0 flex items-center justify-center"></span>
      <span class="shrink-0 mr-1 flex items-center">
        <FileIcon name={node.name} kind={node.type} open={expanded} />
      </span>
      <div class="flex-1 min-w-0">
        <ExplorerInlineInput
          initial={node.name}
          selectBaseOnly
          onconfirm={(v) => void renameEntry(node, v)}
          oncancel={() => renamingPath.set(null)}
        />
      </div>
    </div>
  {:else}
    <div
      role="button"
      tabindex="-1"
      draggable={isRoot || renaming ? undefined : 'true'}
      class="flex items-center w-full h-6 text-left outline-none cursor-default
             transition-colors font-mono
             {isActive
        ? 'bg-surface-2 text-text'
        : isSelected
          ? 'bg-surface-3/70 text-text'
          : 'text-text-dim hover:bg-surface-3/60 hover:text-text'}
             {dropTarget ? 'bg-surface-3 ring-1 ring-inset ring-border-strong' : ''}"
      aria-expanded={isDir ? expanded : undefined}
      onclick={handleClick}
      onkeydown={onKeydown}
      oncontextmenu={onContextMenu}
      ondragstart={onDragStart}
      ondragend={() => {
        draggedPath = null;
        dropTarget = false;
      }}
      ondragover={onDragOverNode}
      ondragleave={() => (dropTarget = false)}
      ondrop={onDropNode}
    >
      {#each Array(depth) as _, i (i)}
        <span
          class="w-4 shrink-0 self-stretch border-l
                 {dropTarget ? 'border-transparent' : 'border-border-strong/40'}"
        ></span>
      {/each}
      <span class="w-4 shrink-0 flex items-center justify-center text-text-faint text-xs">
        {#if isDir}
          {#if isLoading}⟳{:else if expanded}▾{:else}▸{/if}
        {/if}
      </span>
      <span class="shrink-0 mr-1 flex items-center">
        <FileIcon name={node.name} kind={node.type} open={expanded} />
      </span>
      <span class="truncate text-sm pr-2">{node.name}</span>
    </div>
  {/if}

  {#if isDir && expanded}
    <!-- Input de creación como primer hijo (estilo VSCode). -->
    {#if creating}
      <div class="flex items-center gap-1 h-6 pr-2">
        {#each Array(depth + 1) as _, i (i)}
          <span class="w-4 shrink-0 self-stretch border-l border-border-strong/40"></span>
        {/each}
        <span class="w-4 shrink-0"></span>
        <span class="shrink-0 mr-1 flex items-center">
          <FileIcon
            name={createKind === 'file' ? 'nuevo.md' : 'nueva'}
            kind={createKind === 'file' ? 'file' : 'dir'}
          />
        </span>
        <div class="flex-1 min-w-0">
          <ExplorerInlineInput
            onconfirm={(v) => void createEntry(node.path, createKind, v)}
            oncancel={() => pendingCreate.set(null)}
          />
        </div>
      </div>
    {/if}
    {#if node.children}
      {#each node.children as child (child.path)}
        <Self node={child} depth={depth + 1} {oncontext} {ondelete} {ondropinto} />
      {/each}
    {/if}
  {/if}
</div>
