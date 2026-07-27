<script lang="ts">
  // Página principal: IDE layout estilo Claude Code plugin.
  // 3 paneles (Explorer | Viewer | Chat) con resize handles persistentes
  // en localStorage. Header (MenuBar) arriba, StatusBar abajo. Full viewport.
  //
  // Runes OK (.svelte).
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { authStore } from '$lib/stores/auth';
  import MenuBar from '$lib/components/MenuBar.svelte';
  import FileExplorer from '$lib/components/FileExplorer.svelte';
  import FileViewer from '$lib/components/FileViewer.svelte';
  import SrsViewer from '$lib/components/SrsViewer.svelte';
  import ChatPanel from '$lib/components/ChatPanel.svelte';
  import StatusBar from '$lib/components/StatusBar.svelte';
  import ResizeHandle from '$lib/components/ResizeHandle.svelte';
  import { currentProjectId } from '$lib/stores/project';

  let { } = $props();

  // Toggle del visor de SRS (overlay sobre el FileViewer).
  let srsOpen = $state(false);

  // Redirige a /login si no hay sesión.
  $effect(() => {
    if ($authStore === null) {
      goto('/login');
    }
  });

  // Anchos persistentes.
  const EXPLORER_KEY = 'infofact.layout.explorerWidth';
  const CHAT_KEY = 'infofact.layout.chatWidth';
  const DEFAULT_EXPLORER = 240;
  const DEFAULT_CHAT = 380;
  const MIN_W = 120;

  let explorerWidth = $state(DEFAULT_EXPLORER);
  let chatWidth = $state(DEFAULT_CHAT);

  onMount(() => {
    explorerWidth = readStored(EXPLORER_KEY, DEFAULT_EXPLORER);
    chatWidth = readStored(CHAT_KEY, DEFAULT_CHAT);
  });

  function readStored(key: string, fallback: number): number {
    if (typeof localStorage === 'undefined') return fallback;
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const v = Number(raw);
    if (!Number.isFinite(v)) return fallback;
    return Math.max(MIN_W, v);
  }

  function persist(key: string, value: number) {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(key, String(Math.max(MIN_W, value)));
  }

  // Explorer: drag derecha (delta positivo) → explorer crece.
  function onExplorerResize(delta: number) {
    explorerWidth = Math.max(MIN_W, explorerWidth + delta);
    persist(EXPLORER_KEY, explorerWidth);
  }

  // Chat: drag derecha (delta positivo) → chat se achica.
  function onChatResize(delta: number) {
    chatWidth = Math.max(MIN_W, chatWidth - delta);
    persist(CHAT_KEY, chatWidth);
  }
</script>

<svelte:head><title>InfoFact — IDE</title></svelte:head>

{#if $authStore}
  <div
    class="h-screen w-screen flex flex-col bg-bg text-text overflow-hidden"
  >
    <MenuBar />

    <main class="flex-1 flex min-h-0">
      <!-- Explorer -->
      <div style="width: {explorerWidth}px" class="shrink-0">
        <FileExplorer />
      </div>

      <ResizeHandle onResize={onExplorerResize} />

      <!-- Viewer (flex-1): FileViewer o SrsViewer overlay -->
      <div class="flex-1 min-w-0 relative">
        <FileViewer />
        {#if srsOpen}
          <div class="absolute inset-0 z-10">
            <SrsViewer projectId={$currentProjectId} onClose={() => (srsOpen = false)} />
          </div>
        {/if}
        <!-- Toggle SRS: botón flotante esquina superior del viewer -->
        <button
          type="button"
          class="absolute top-1 right-2 z-20 px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider rounded-sm border border-border bg-surface-2 text-text-dim hover:bg-surface-3 hover:text-text transition-colors {srsOpen
            ? 'bg-accent text-bg border-accent'
            : ''}"
          onclick={() => (srsOpen = !srsOpen)}
          title="Ver SRS generado desde los requerimientos capturados"
        >
          SRS
        </button>
      </div>

      <ResizeHandle onResize={onChatResize} />

      <!-- Chat -->
      <div style="width: {chatWidth}px" class="shrink-0">
        <ChatPanel />
      </div>
    </main>

    <StatusBar />
  </div>
{/if}
