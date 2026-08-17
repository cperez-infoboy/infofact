<script lang="ts">
  // Banner de estado del /agrupar (ruta directa del comando /agrupar).
  // Espejo del patrón de CaptureStatus.svelte, alimentado por los eventos
  // grouping.progress del backend (etapas + lotes del juez + timings).
  //
  // Visible mientras corre la revisión o si quedó el resultado del último run.
  //
  // Runes OK (.svelte). Stores del .ts plano se consumen con prefijo `$`.

  import {
    groupingStage,
    groupingRunning,
    groupingTimings,
    groupingTotalMs,
    groupingResult
  } from '$lib/stores/grouping';
  import ToolIcon from './ToolIcon.svelte';

  let { } = $props();

  // Mostrar el componente si hay revisión activa o resultado reciente.
  let visible = $derived($groupingRunning || $groupingResult !== null);

  // Etiquetas legibles para cada stage del backend.
  const STAGE_LABELS: Record<string, string> = {
    load: 'Carga',
    dedup: 'Dedup exacto',
    embedding: 'Embeddings',
    judge: 'Juez LLM',
    cluster: 'Clustering',
    persist: 'Persistencia',
    done: 'Listo'
  };

  // Orden canónico del pipeline; las etapas sin tiempo se filtran al render.
  const STAGE_ORDER = [
    'load',
    'dedup',
    'embedding',
    'judge',
    'cluster',
    'persist'
  ];

  /** Formatea milisegundos a algo legible: 950ms / 12.3s / 3m 05s. */
  function fmtMs(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    const totalSec = Math.round(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }

  /** Filas de tiempos en orden de pipeline (sólo las etapas con tiempo). */
  let timingRows = $derived(
    STAGE_ORDER.filter((k) => k in $groupingTimings).map((k) => ({
      stage: k,
      label: STAGE_LABELS[k] ?? k,
      ms: $groupingTimings[k]
    }))
  );

  let hasTimings = $derived(timingRows.length > 0);

  function stageLabel(stage: string | undefined): string {
    if (!stage) return 'Revisando';
    return STAGE_LABELS[stage] ?? stage;
  }
</script>

{#if visible}
  <aside
    class="border-b border-border bg-surface-2/60 backdrop-blur-sm px-3 py-2 text-xs space-y-2"
  >
    <!-- Encabezado: etapa actual + spinner -->
    <div class="flex items-center gap-2">
      {#if $groupingRunning}
        <ToolIcon />
        <span class="font-mono uppercase tracking-wider text-text-faint">
          {stageLabel($groupingStage?.stage)}
        </span>
        {#if $groupingStage?.message}
          <span class="text-text-dim truncate">{$groupingStage.message}</span>
        {/if}
        <span class="ml-auto inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
      {:else}
        <span class="font-mono uppercase tracking-wider text-text-faint">
          Agrupamiento listo
        </span>
        {#if $groupingResult}
          <span class="font-mono text-text-dim">
            plan #{$groupingResult.plan_id} · {$groupingResult.group_count}
            {$groupingResult.group_count === 1 ? 'grupo' : 'grupos'}
          </span>
        {/if}
      {/if}
    </div>

    <!-- Termómetro del juez por lote (cuando la etapa reporta current/total) -->
    {#if $groupingRunning && $groupingStage?.current != null && $groupingStage?.total != null && $groupingStage.total > 0}
      {@const pct = Math.round(($groupingStage.current / $groupingStage.total) * 100)}
      <div class="flex items-center gap-2">
        <div class="relative flex-1 h-2 rounded-sm bg-surface-2 overflow-hidden">
          <div
            class="absolute inset-y-0 left-0 bg-accent transition-all duration-300"
            style="width: {pct}%"
          ></div>
        </div>
        <span class="font-mono text-text-faint text-[10px] shrink-0 w-16 text-right">
          {$groupingStage.current}/{$groupingStage.total}
        </span>
      </div>
    {/if}

    <!-- Tiempos por etapa (profiling, como el banner de captura) -->
    {#if hasTimings}
      <div class="rounded-sm border border-border bg-surface px-2 py-1.5">
        <div class="flex items-center justify-between mb-1">
          <span class="font-mono text-text-faint uppercase tracking-wider">
            Tiempos por etapa
          </span>
          {#if $groupingTotalMs}
            <span class="font-mono text-text-faint text-[10px]">
              total {fmtMs($groupingTotalMs)}
            </span>
          {/if}
        </div>
        <div class="space-y-1">
          {#each timingRows as row (row.stage)}
            {@const pct = $groupingTotalMs ? Math.round((row.ms / $groupingTotalMs) * 100) : 0}
            <div class="flex items-center gap-2 font-mono text-[11px]">
              <span class="w-24 shrink-0 text-text-dim">
                {row.label}
              </span>
              <div class="relative flex-1 h-1.5 rounded-sm bg-surface-2 overflow-hidden">
                <div
                  class="absolute inset-y-0 left-0 bg-accent/40"
                  style="width: {pct}%"
                ></div>
              </div>
              <span class="w-14 shrink-0 text-right text-text">{fmtMs(row.ms)}</span>
              <span class="w-8 shrink-0 text-right text-text-faint">{pct}%</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </aside>
{/if}
