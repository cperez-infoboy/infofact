<script lang="ts">
  // Banner de estado del pipeline de generación de SRS. Se monta arriba del
  // chat list (junto a CaptureStatus) y reacciona a los eventos finos SSE del
  // subagente srs-agent: srs.progress, quality.found, goal.inferred,
  // coverage.report, srs.ready.
  //
  // A diferencia de la captura, las stage tools del subagente quedan ocultas
  // tras el tool `task` de DeepAgents -> el store arranca el run en el primer
  // srs.progress y lo cierra en srs.ready (ver stores/srs.ts). Por eso aquí el
  // encabezado usa srsRunning (no el nombre de una tool).
  //
  // Visible mientras:
  //   - srsRunning === true (generación activa), O
  //   - hay datos residuales de la última corrida (hallazgos, goals, coverage,
  //     o srsReady).
  //
  // Runes OK (.svelte). Stores del .ts plano se consumen con prefijo `$`.

  import {
    srsStage,
    srsRunning,
    liveFindings,
    liveGoals,
    liveCoverage,
    srsReady,
    liveFindingCount,
    liveBlockerCount,
    liveGoalCount
  } from '$lib/stores/srs';
  import ToolIcon from './ToolIcon.svelte';

  let { } = $props();

  // paneles colapsables
  let showFindings = $state(false);
  let showGoals = $state(false);

  // Mostrar el componente si hay generación activa o datos de la corrida reciente.
  let visible = $derived(
    $srsRunning ||
      $liveFindingCount > 0 ||
      $liveGoalCount > 0 ||
      $liveCoverage !== null ||
      $srsReady !== null
  );

  // Etiquetas legibles para cada stage del backend (srs.progress.stage).
  const STAGE_LABELS: Record<string, string> = {
    quality: 'Calidad',
    goals: 'Goals',
    coverage: 'Cobertura',
    commit: 'Persistencia'
  };

  // Color del badge de severidad (espejo de SrsViewer.svelte::SEV_CLASS).
  function severityClass(severity: string): string {
    if (severity === 'blocker') return 'text-danger';
    if (severity === 'major') return 'text-warning';
    return 'text-text-dim';
  }

  // Últimos 5 hallazgos accionables (para no volcar la lista entera en DOM).
  let recentFindings = $derived($liveFindings.slice(-5).reverse());

  // Últimos 5 goals inferidos.
  let recentGoals = $derived($liveGoals.slice(-5).reverse());

  function stageLabel(stage: string | undefined): string {
    if (!stage) return 'Procesando';
    return STAGE_LABELS[stage] ?? stage;
  }

  // Etiqueta corta del tipo de goal (GORE).
  const GOAL_KIND_LABEL: Record<string, string> = {
    functional_goal: 'funcional',
    softgoal: 'softgoal',
    obstacle: 'obstáculo'
  };
</script>

{#if visible}
  <aside
    class="border-b border-border bg-surface-2/60 backdrop-blur-sm px-3 py-2 text-xs space-y-2"
  >
    <!-- Encabezado: etapa actual + spinner -->
    <div class="flex items-center gap-2">
      {#if $srsRunning}
        <ToolIcon />
        <span class="font-mono uppercase tracking-wider text-text-faint">
          {stageLabel($srsStage?.stage)}
        </span>
        {#if $srsStage?.message}
          <span class="text-text-dim truncate">{$srsStage.message}</span>
        {/if}
        <span class="ml-auto inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
      {:else if $srsReady}
        <span class="font-mono uppercase tracking-wider text-text-faint">
          SRS v{$srsReady.version} listo
        </span>
        {#if $srsReady.requirement_count !== undefined}
          <span class="text-text-dim font-mono">
            · {$srsReady.requirement_count} reqs
          </span>
        {/if}
      {:else}
        <span class="font-mono uppercase tracking-wider text-text-faint">
          Generación finalizada
        </span>
      {/if}
    </div>

    <!-- Contadores en vivo -->
    <div class="flex flex-wrap gap-x-4 gap-y-1 font-mono">
      <span class="text-text">
        <span class="text-text-faint">HALL</span>
        <span class="ml-1">{$liveFindingCount}</span>
      </span>
      <span class="text-text">
        <span class="text-text-faint">BLOQ</span>
        <span class="ml-1">{$liveBlockerCount}</span>
      </span>
      <span class="text-text">
        <span class="text-text-faint">GOALS</span>
        <span class="ml-1">{$liveGoalCount}</span>
      </span>
    </div>

    <!-- Reporte de cobertura (ISO 25010 + goals) -->
    {#if $liveCoverage}
      <div class="rounded-sm border border-border bg-surface px-2 py-1.5">
        <div class="font-mono text-text-faint uppercase tracking-wider mb-1">
          Cobertura
        </div>
        <div class="grid grid-cols-4 gap-2 text-center font-mono">
          <div>
            <div class="text-text-faint text-[10px]">FUNC</div>
            <div class="text-text">{$liveCoverage.functional}</div>
          </div>
          <div>
            <div class="text-text-faint text-[10px]">NFR</div>
            <div class="text-text">{$liveCoverage.nfr}</div>
          </div>
          <div>
            <div class="text-text-faint text-[10px]">GAPS 25010</div>
            <div class="{$liveCoverage.gaps_25010?.length > 0 ? 'text-warning' : 'text-text'}">
              {$liveCoverage.gaps_25010?.length ?? 0}
            </div>
          </div>
          {#if $liveCoverage.unlinked_requirements !== undefined}
            <div>
              <div class="text-text-faint text-[10px]">SIN GOAL</div>
              <div class="{$liveCoverage.unlinked_requirements > 0 ? 'text-warning' : 'text-text'}">
                {$liveCoverage.unlinked_requirements}
              </div>
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <!-- Últimos hallazgos accionables (colapsable) -->
    {#if $liveFindingCount > 0}
      <button
        type="button"
        class="flex w-full items-center gap-1 text-text-dim hover:text-text"
        onclick={() => (showFindings = !showFindings)}
      >
        <span class="font-mono">▶</span>
        <span class="font-mono uppercase tracking-wider">
          Hallazgos ({$liveFindingCount})
        </span>
      </button>
      {#if showFindings}
        <ul class="space-y-1 max-h-40 overflow-y-auto">
          {#each recentFindings as f, i (i)}
            <li class="rounded-sm border border-border bg-surface px-2 py-1">
              <div class="flex items-baseline gap-2">
                <span class="font-mono {severityClass(f.severity)} uppercase text-[10px]">
                  {f.severity}
                </span>
                <span class="font-mono text-text-faint text-[10px]">{f.dimension}</span>
              </div>
              <div class="text-text-dim line-clamp-2">{f.message}</div>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}

    <!-- Últimos goals inferidos (colapsable) -->
    {#if $liveGoalCount > 0}
      <button
        type="button"
        class="flex w-full items-center gap-1 text-text-dim hover:text-text"
        onclick={() => (showGoals = !showGoals)}
      >
        <span class="font-mono">▶</span>
        <span class="font-mono uppercase tracking-wider">Goals</span>
      </button>
      {#if showGoals}
        <ul class="space-y-1 max-h-40 overflow-y-auto">
          {#each recentGoals as g, i (i)}
            <li class="rounded-sm border border-border bg-surface px-2 py-1">
              <div class="flex items-baseline gap-2">
                <span class="font-mono text-text">{g.code}</span>
                <span class="font-mono text-text-faint text-[10px]">
                  {GOAL_KIND_LABEL[g.kind] ?? g.kind}
                </span>
              </div>
              <div class="text-text-dim line-clamp-2">{g.statement}</div>
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
