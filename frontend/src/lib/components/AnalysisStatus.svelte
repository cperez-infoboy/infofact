<script lang="ts">
  // Banner de estado vivo del /analisis (subagente analysis-agent).
  // Espejo del patrón de GroupingStatus.svelte / CaptureStatus.svelte,
  // alimentado por los eventos analysis.progress del chat store: sin esto,
  // el único indicador en el chat era el chip de la tool (texto fijo de la
  // etapa, sin avance). El message de cada evento viaja con el lote en curso
  // ("entidades identificadas (12/41 lotes)"); el contador se extrae para la
  // barra de progreso.
  //
  // Runes OK (.svelte). Stores del .ts plano se consumen con prefijo `$`.

  import {
    analysisStage,
    analysisRunning,
    analysisReady,
    liveEntityCount,
    liveAdrCount,
    liveSubProjectCount,
    liveProcessDiagramCount
  } from '$lib/stores/analysis';
  import ToolIcon from './ToolIcon.svelte';

  let { } = $props();

  let visible = $derived($analysisRunning || $analysisReady !== null);

  // Etiqueta legible por stage del pipeline.
  const STAGE_LABELS: Record<string, string> = {
    mer: 'MER',
    nfr: 'NFR',
    process: 'Procesos',
    adr: 'ADRs',
    projects: 'Proyectos',
    subproject: 'Sub-proyectos',
    architecture: 'Arquitectura',
    commit: 'Persistiendo',
    done: 'Listo'
  };

  // Orden canónico del pipeline para el resumen final.
  const STAGE_ORDER = [
    'mer',
    'nfr',
    'process',
    'adr',
    'projects',
    'subproject',
    'architecture',
    'commit'
  ];

  function stageLabel(stage: string | undefined): string {
    if (!stage) return 'Generando análisis';
    return STAGE_LABELS[stage] ?? stage;
  }

  /** Extrae "(12/41 lotes)" (o "(3/7)") del message para la barra. */
  let progress = $derived.by(() => {
    const msg = $analysisStage?.message ?? '';
    const m = msg.match(/\((\d+)\/(\d+)\s/);
    if (!m) return null;
    const current = Number(m[1]);
    const total = Number(m[2]);
    if (!total) return null;
    return { current, total, pct: Math.round((current / total) * 100) };
  });

  /** Formatea milisegundos: 950ms / 12.3s / 3m 05s. */
  function fmtMs(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
    const totalSec = Math.round(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }

  type StageTiming = { stage: string; label: string; ms: number };
  let timings = $derived.by<StageTiming[]>(() => {
    const raw = $analysisReady?.timings as Record<string, number> | undefined;
    if (!raw) return [];
    return STAGE_ORDER.filter((k) => k in raw).map((k) => ({
      stage: k,
      label: STAGE_LABELS[k] ?? k,
      ms: raw[k]
    }));
  });
</script>

{#if visible}
  <aside
    class="border-b border-border bg-surface-2/60 backdrop-blur-sm px-3 py-2 text-xs space-y-2"
  >
    <!-- Encabezado: etapa actual + message del lote + spinner -->
    <div class="flex items-center gap-2 min-w-0">
      {#if $analysisRunning}
        <ToolIcon />
        <span class="font-mono uppercase tracking-wider text-text-faint shrink-0">
          {stageLabel($analysisStage?.stage)}
        </span>
        {#if $analysisStage?.message}
          <span class="text-text-dim truncate" title={$analysisStage.message}>
            {$analysisStage.message}
          </span>
        {/if}
        <span class="ml-auto inline-block h-2 w-2 animate-pulse rounded-full bg-accent shrink-0"></span>
      {:else if $analysisReady}
        <span class="font-mono uppercase tracking-wider text-text-faint">
          Análisis listo
        </span>
        <span class="font-mono text-text-dim">
          v{$analysisReady.version} · {$analysisReady.entities} entidades ·
          {$analysisReady.adrs} ADRs · {$analysisReady.sub_projects} sub-proyectos
        </span>
      {/if}
    </div>

    <!-- Barra de progreso por lote (cuando el message trae "x/y lotes") -->
    {#if $analysisRunning && progress}
      <div class="flex items-center gap-2">
        <div class="relative flex-1 h-2 rounded-sm bg-surface-2 overflow-hidden">
          <div
            class="absolute inset-y-0 left-0 bg-accent transition-all duration-300"
            style="width: {progress.pct}%"
          ></div>
        </div>
        <span class="font-mono text-text-faint text-[10px] shrink-0 w-16 text-right">
          {progress.current}/{progress.total}
        </span>
      </div>
    {/if}

    <!-- Conteos vivos (llegan por los *_ready intermedios) -->
    {#if $analysisRunning}
      <div class="flex flex-wrap gap-2 font-mono text-[10px] text-text-dim">
        {#if $liveEntityCount > 0}<span>{$liveEntityCount} entidades</span>{/if}
        {#if $liveProcessDiagramCount > 0}<span>{$liveProcessDiagramCount} diagramas</span>{/if}
        {#if $liveAdrCount > 0}<span>{$liveAdrCount} ADRs</span>{/if}
        {#if $liveSubProjectCount > 0}<span>{$liveSubProjectCount} sub-proyectos</span>{/if}
      </div>
    {/if}

    <!-- Tiempos por etapa (tras analysis.ready con timings) -->
    {#if !$analysisRunning && timings.length > 0}
      <div class="rounded-sm border border-border bg-surface px-2 py-1.5">
        <div class="font-mono text-text-faint uppercase tracking-wider mb-1">
          Tiempos por etapa
        </div>
        <div class="space-y-1">
          {#each timings as row (row.stage)}
            <div class="flex items-center gap-2 font-mono text-[11px]">
              <span class="w-24 shrink-0 text-text-dim">{row.label}</span>
              <span class="w-16 shrink-0 text-right text-text">{fmtMs(row.ms)}</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </aside>
{/if}
