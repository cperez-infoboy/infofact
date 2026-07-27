<script lang="ts">
  // Banner de estado del pipeline de captura. Se monta arriba del chat list
  // y reacciona a los eventos finos SSE (conflict.found, validation.report,
  // requirement.added) + al evento coarse extraction.progress.
  //
  // Visible mientras:
  //   - captureRunning === true (pipeline activo), O
  //   - hay datos residuales de la última corrida (requisitos, reporte, conflictos).
  //
  // Runes OK (.svelte). Stores del .ts plano se consumen con prefijo `$`.

  import {
    captureStage,
    captureRunning,
    conflicts,
    validationReport,
    addedRequirements,
    addedCount,
    conflictCount,
    duplicateCount,
    contradictionCount
  } from '$lib/stores/capture';
  import ToolIcon from './ToolIcon.svelte';

  let { } = $props();

  // paneles colapsables
  let showConflicts = $state(false);
  let showRecent = $state(false);

  // Mostrar el componente si hay pipeline activo o datos de la corrida reciente.
  let visible = $derived(
    $captureRunning ||
      $addedCount > 0 ||
      $validationReport !== null ||
      $conflictCount > 0
  );

  // Etiquetas legibles para cada stage del backend.
  const STAGE_LABELS: Record<string, string> = {
    ingest: 'Ingesta',
    extract: 'Extracción',
    consolidate: 'Consolidación',
    critique: 'Crítica',
    classify: 'Clasificación',
    persist: 'Persistencia',
    done: 'Listo'
  };

  // Últimos 5 requisitos añadidos (para no volcar la lista entera en DOM).
  let recentRequirements = $derived($addedRequirements.slice(-5).reverse());

  function stageLabel(stage: string | undefined): string {
    if (!stage) return 'Procesando';
    return STAGE_LABELS[stage] ?? stage;
  }
</script>

{#if visible}
  <aside
    class="border-b border-border bg-surface-2/60 backdrop-blur-sm px-3 py-2 text-xs space-y-2"
  >
    <!-- Encabezado: etapa actual + spinner -->
    <div class="flex items-center gap-2">
      {#if $captureRunning}
        <ToolIcon />
        <span class="font-mono uppercase tracking-wider text-text-faint">
          {stageLabel($captureStage?.stage)}
        </span>
        {#if $captureStage?.message}
          <span class="text-text-dim truncate">{$captureStage.message}</span>
        {/if}
        <span class="ml-auto inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
      {:else}
        <span class="font-mono uppercase tracking-wider text-text-faint">
          Captura finalizada
        </span>
      {/if}
    </div>

    <!-- Contadores en vivo -->
    <div class="flex flex-wrap gap-x-4 gap-y-1 font-mono">
      <span class="text-text">
        <span class="text-text-faint">REQ</span>
        <span class="ml-1">{$addedCount}</span>
      </span>
      <span class="text-text">
        <span class="text-text-faint">DUP</span>
        <span class="ml-1">{$duplicateCount}</span>
      </span>
      <span class="text-text">
        <span class="text-text-faint">CONTRA</span>
        <span class="ml-1">{$contradictionCount}</span>
      </span>
    </div>

    <!-- Reporte de validación (post-crítica) -->
    {#if $validationReport}
      <div class="rounded-sm border border-border bg-surface px-2 py-1.5">
        <div class="font-mono text-text-faint uppercase tracking-wider mb-1">
          Validación
        </div>
        <div class="grid grid-cols-4 gap-2 text-center font-mono">
          <div>
            <div class="text-text-faint text-[10px]">TOTAL</div>
            <div class="text-text">{$validationReport.total}</div>
          </div>
          <div>
            <div class="text-text-faint text-[10px]">OK</div>
            <div class="text-text">{$validationReport.kept}</div>
          </div>
          <div>
            <div class="text-text-faint text-[10px]">RECH</div>
            <div class="text-text-dim">{$validationReport.rejected}</div>
          </div>
          <div>
            <div class="text-text-faint text-[10px]">REV</div>
            <div class="text-text-dim">{$validationReport.flagged}</div>
          </div>
        </div>
      </div>
    {/if}

    <!-- Conflictos (colapsable) -->
    {#if $conflictCount > 0}
      <button
        type="button"
        class="flex w-full items-center gap-1 text-text-dim hover:text-text"
        onclick={() => (showConflicts = !showConflicts)}
      >
        <span class="font-mono">▶</span>
        <span class="font-mono uppercase tracking-wider">
          Conflictos ({$conflictCount})
        </span>
      </button>
      {#if showConflicts}
        <ul class="space-y-1 max-h-40 overflow-y-auto">
          {#each $conflicts as c, i (i)}
            <li class="rounded-sm border border-border bg-surface px-2 py-1">
              {#if c.kind === 'duplicate'}
                <span class="font-mono text-text-faint">DUP</span>
                <span class="ml-1 font-mono text-text">{c.kept_id}</span>
                <span class="text-text-dim">
                  fusiona {c.member_ids.join(', ')}
                </span>
              {:else}
                <span class="font-mono text-text-faint">CONTRA</span>
                <span class="ml-1 font-mono text-text">{c.a_id}</span>
                <span class="text-text-dim"> vs </span>
                <span class="font-mono text-text">{c.b_id}</span>
                <div class="text-text-dim truncate">{c.reason}</div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    {/if}

    <!-- Últimos requisitos añadidos (colapsable) -->
    {#if $addedCount > 0}
      <button
        type="button"
        class="flex w-full items-center gap-1 text-text-dim hover:text-text"
        onclick={() => (showRecent = !showRecent)}
      >
        <span class="font-mono">▶</span>
        <span class="font-mono uppercase tracking-wider">
          Últimos requisitos
        </span>
      </button>
      {#if showRecent}
        <ul class="space-y-1 max-h-40 overflow-y-auto">
          {#each recentRequirements as r, i (i)}
            <li class="rounded-sm border border-border bg-surface px-2 py-1">
              <div class="flex items-baseline gap-2">
                <span class="font-mono text-text">{r.code}</span>
                <span class="font-mono text-text-faint text-[10px]">
                  {r.type}{r.derived ? ' · derivado' : ''}
                </span>
                {#if !r.span_verified}
                  <span class="font-mono text-danger text-[10px]">SIN CITA</span>
                {/if}
              </div>
              <div class="text-text-dim line-clamp-2">{r.statement}</div>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </aside>
{/if}

<style>
  .line-clamp-2 {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
</style>
