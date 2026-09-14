<script lang="ts">
  // Visor central con tabs. Soporta tabs de archivo (textarea editable) y
  // tabs de "vista" (requerimientos / agrupamiento), discriminados por `kind`.
  // - Ctrl/Cmd+S dispara saveActive() (solo aplica a archivos).
  // - Store LRU≤15 (stores/tabs.ts); los view tabs son sticky.
  // Runes OK (.svelte).
  import {
    openTabs,
    activeTabId,
    setActive,
    closeTab,
    markDirty,
    saveActive,
    tabsError
  } from '$lib/stores/tabs';
  import { currentProjectId } from '$lib/stores/project';
  import RequirementsExplorer from '$lib/components/RequirementsExplorer.svelte';
  import GroupingPlanPanel from '$lib/components/GroupingPlanPanel.svelte';
  import SrsViewer from '$lib/components/SrsViewer.svelte';
  import AnalysisViewer from '$lib/components/AnalysisViewer.svelte';
  import PackagesViewer from '$lib/components/PackagesViewer.svelte';
  import MarkdownEditor from '$lib/components/MarkdownEditor.svelte';

  let { } = $props();

  let activeTab = $derived($openTabs.find((t) => t.id === $activeTabId) ?? null);

  // Archivos .md: editor WYSIWYG (Tiptap) por defecto, toggle a texto crudo.
  let isMd = $derived(
    activeTab?.kind === 'file' && /\.md$/i.test(activeTab.path)
  );
  let mdMode = $state<'editor' | 'texto'>('editor');

  // Al cambiar de tab, los .md arrancan en el editor WYSIWYG.
  $effect(() => {
    void $activeTabId;
    mdMode = 'editor';
  });

  function handleInput(e: Event) {
    const value = (e.target as HTMLTextAreaElement).value;
    if ($activeTabId) {
      markDirty($activeTabId, value);
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveActive();
    }
  }

  function handleClose(id: string, e: MouseEvent) {
    e.stopPropagation();
    closeTab(id);
  }
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <!-- Tab bar -->
  <div
    class="h-9 flex items-stretch border-b border-border overflow-x-auto bg-surface-2"
  >
    {#each $openTabs as tab (tab.id)}
      <div
        class="group flex items-center gap-2 px-3 text-xs whitespace-nowrap border-r border-border transition-colors cursor-pointer {tab.id ===
        $activeTabId
          ? 'bg-surface text-text border-b-2 border-b-accent'
          : 'bg-bg text-text-dim hover:bg-surface-2 hover:text-text'}"
        onclick={() => setActive(tab.id)}
        title={tab.kind === 'file' ? tab.path : tab.name}
        role="tab"
        aria-selected={tab.id === $activeTabId}
        tabindex="0"
        onkeydown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setActive(tab.id);
          }
        }}
      >
        <span>{tab.name}</span>
        {#if tab.kind === 'file' && tab.dirty}
          <span class="text-text-dim" title="sin guardar">●</span>
        {/if}
        <button
          type="button"
          class="opacity-0 group-hover:opacity-100 hover:bg-surface-3 px-1 text-text-dim hover:text-text"
          onclick={(e) => handleClose(tab.id, e)}
          aria-label="Cerrar tab {tab.name}">×</button
        >
      </div>
    {/each}
  </div>

  {#if $tabsError}
    <div class="px-3 py-1 text-xs text-danger bg-danger/5 border-b border-danger/30">
      ! {$tabsError}
    </div>
  {/if}

  <!-- Contenido: switch por kind -->
  <div class="flex-1 min-h-0 min-w-0 overflow-hidden">
    {#if activeTab?.kind === 'file'}
      {#if isMd && mdMode === 'editor'}
        <!-- key por tab: remonta el editor al cambiar de archivo (estado y
             undo history limpios por instancia). -->
        {#key activeTab.id}
          <MarkdownEditor
            initialContent={activeTab.content}
            onchange={(md) => {
              if ($activeTabId) markDirty($activeTabId, md);
            }}
          />
        {/key}
      {:else}
        <textarea
          class="w-full h-full resize-none bg-bg text-text font-mono text-sm p-4 focus:outline-none"
          value={activeTab.content}
          oninput={handleInput}
          onkeydown={handleKeydown}
          spellcheck="false"
          autocomplete="off"
          autocapitalize="off"
        ></textarea>
      {/if}
    {:else if activeTab?.kind === 'requirements'}
      <RequirementsExplorer projectId={$currentProjectId} />
    {:else if activeTab?.kind === 'grouping'}
      <GroupingPlanPanel projectId={$currentProjectId} />
    {:else if activeTab?.kind === 'srs'}
      <SrsViewer projectId={$currentProjectId} />
    {:else if activeTab?.kind === 'analysis'}
      <AnalysisViewer projectId={$currentProjectId} />
    {:else if activeTab?.kind === 'packages'}
      <PackagesViewer projectId={$currentProjectId} />
    {:else}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Abre un archivo o una vista del proyecto
      </div>
    {/if}
  </div>

  <!-- Footer del viewer: guardar (solo archivos) + toggle md (solo .md) -->
  {#if activeTab?.kind === 'file'}
    <div
      class="h-8 flex items-center gap-3 px-3 border-t border-border bg-bg text-xs"
    >
      <span class="text-text-dim truncate" title={activeTab.path}>{activeTab.path}</span>
      <div class="flex-1"></div>
      {#if isMd}
        <div
          class="flex items-center rounded-sm border border-border overflow-hidden"
          role="group"
          aria-label="Modo de edición del markdown"
        >
          <button
            type="button"
            class="px-2 py-0.5 text-xs transition-colors {mdMode === 'editor'
              ? 'bg-surface-3 text-text'
              : 'bg-surface-2 text-text-dim hover:text-text'}"
            onclick={() => (mdMode = 'editor')}
            title="Editor visual (WYSIWYG)"
          >
            editor
          </button>
          <button
            type="button"
            class="px-2 py-0.5 text-xs transition-colors border-l border-border {mdMode === 'texto'
              ? 'bg-surface-3 text-text'
              : 'bg-surface-2 text-text-dim hover:text-text'}"
            onclick={() => (mdMode = 'texto')}
            title="Editar el markdown como texto crudo"
          >
            texto
          </button>
        </div>
      {/if}
      <button
        type="button"
        class="px-2 py-0.5 bg-accent text-bg hover:bg-accent-hover disabled:opacity-50 transition-colors"
        onclick={() => saveActive()}
        disabled={!activeTab.dirty}>guardar {#if activeTab.dirty}<span
            class="text-bg">●</span
          >{/if}</button
      >
    </div>
  {/if}
</section>
