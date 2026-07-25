<script lang="ts">
  // Visor de archivos con tabs (LRU≤15 gestionado por el store).
  // - Ctrl/Cmd+S dispara saveActive().
  // - Textarea enlazado bidireccional con el contenido del tab activo.
  // - El indicador "•" marca tabs dirty.
  // Runes OK (.svelte).
  import { openTabs, activeTabPath, setActive, closeTab, markDirty, saveActive, tabsError } from '$lib/stores/tabs';

  let { } = $props();

  let activeTab = $derived(
    $openTabs.find((t) => t.path === $activeTabPath) ?? null
  );

  function handleInput(e: Event) {
    const value = (e.target as HTMLTextAreaElement).value;
    if ($activeTabPath) {
      markDirty($activeTabPath, value);
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    // Ctrl/Cmd+S → guardar.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveActive();
    }
  }

  function handleClose(path: string, e: MouseEvent) {
    e.stopPropagation();
    closeTab(path);
  }
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <!-- Tab bar -->
  <div
    class="h-9 flex items-stretch border-b border-border overflow-x-auto bg-surface-2"
  >
    {#each $openTabs as tab (tab.path)}
      <div
        class="group flex items-center gap-2 px-3 text-xs whitespace-nowrap border-r border-border transition-colors cursor-pointer
               {tab.path === $activeTabPath
          ? 'bg-surface text-text border-b-2 border-b-accent'
          : 'bg-bg text-text-dim hover:bg-surface-2 hover:text-text'}"
        onclick={() => setActive(tab.path)}
        title={tab.path}
        role="tab"
        aria-selected={tab.path === $activeTabPath}
        tabindex="0"
        onkeydown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setActive(tab.path);
          }
        }}
      >
        <span>{tab.name}</span>
        {#if tab.dirty}<span class="text-text-dim" title="sin guardar">●</span>{/if}
        <button
          type="button"
          class="opacity-0 group-hover:opacity-100 hover:bg-surface-3 px-1 text-text-dim hover:text-text"
          onclick={(e) => handleClose(tab.path, e)}
          aria-label="Cerrar tab {tab.name}">×</button
        >
      </div>
    {/each}
  </div>

  {#if $tabsError}
    <div
      class="px-3 py-1 text-xs text-danger bg-danger/5 border-b border-danger/30"
    >
      ! {$tabsError}
    </div>
  {/if}

  <!-- Contenido -->
  <div class="flex-1 min-h-0">
    {#if activeTab}
      <textarea
        class="w-full h-full resize-none bg-bg text-text font-mono text-sm p-4 focus:outline-none"
        value={activeTab.content}
        oninput={handleInput}
        onkeydown={handleKeydown}
        spellcheck="false"
        autocomplete="off"
        autocapitalize="off"
      ></textarea>
    {:else}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Abre un archivo del explorador
      </div>
    {/if}
  </div>

  <!-- Footer del viewer: guardar -->
  {#if activeTab}
    <div
      class="h-8 flex items-center justify-between px-3 border-t border-border bg-bg text-xs"
    >
      <span class="text-text-dim truncate" title={activeTab.path}
        >{activeTab.path}</span
      >
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
