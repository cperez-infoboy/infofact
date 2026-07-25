<script lang="ts">
  // Status bar: rail de fases, sesión actual, indicador de streaming, tabs.
  // Runes OK porque es .svelte.
  import { currentSession } from '$lib/stores/project';
  import { isStreaming } from '$lib/stores/chat';
  import { openTabs } from '$lib/stores/tabs';

  let { } = $props();

  type Phase = { id: string; num: string; label: string };

  // Fases del agente (ver CLAUDE.md — tabla de fases).
  const PHASES: Phase[] = [
    { id: 'requirements', num: '01', label: 'Requerimientos' },
    { id: 'analysis', num: '02', label: 'Análisis' },
    { id: 'implementation', num: '03', label: 'Implementación' },
    { id: 'testing', num: '04', label: 'Testing' },
    { id: 'deploy', num: '05', label: 'Deploy' }
  ];

  let tabCount = $derived($openTabs.length);
  let sessionLabel = $derived(
    $currentSession ? `sesión #${$currentSession.id}` : 'sin sesión'
  );
  let currentPhaseId = $derived($currentSession?.phase ?? 'requirements');
</script>

<footer
  class="shrink-0 flex items-center gap-3 px-3 py-1.5 text-xs bg-surface text-text-dim border-t border-border font-mono"
>
  <!-- Phase rail -->
  <div class="flex items-center gap-1">
    {#each PHASES as phase (phase.id)}
      <div
        class="flex items-center gap-1.5 px-2 py-0.5 rounded border transition-colors
               {phase.id === currentPhaseId
          ? 'text-text bg-surface-2 border-border-strong'
          : 'text-text-faint border-transparent'}"
        title="Fase {phase.num} — {phase.label}"
      >
        <span class="hidden sm:inline">{phase.num} · {phase.label}</span>
        <span class="sm:hidden">{phase.num}</span>
      </div>
    {/each}
  </div>

  <span class="ml-auto flex items-center gap-2">
    <span class="text-text">{sessionLabel}</span>
    <span class="text-text-faint">·</span>
    {#if $isStreaming}
      <span
        class="inline-block h-2 w-2 bg-text-dim animate-pulse"
        aria-label="streaming"
      ></span>
      <span class="text-text-dim">streaming</span>
    {:else}
      <span
        class="inline-block h-2 w-2 bg-text-faint"
        aria-label="idle"
      ></span>
      <span class="text-text-faint">idle</span>
    {/if}
    <span class="text-text-faint">·</span>
    <span class="text-text">{tabCount} {tabCount === 1 ? 'tab' : 'tabs'}</span>
  </span>
</footer>
