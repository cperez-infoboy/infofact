<script lang="ts">
  // Visor del Análisis versiónado (Phase 2: Analysis & Design). Muestra el
  // MER (Modelo Entidad-Relación), diagramas de procesos, análisis NFR, ADRs
  // y descomposición en sub-proyectos. Cada /analysis genera una versión
  // CANDIDATE persistida. Renderizado como view tab del FileViewer.
  //
  // Pestañas: Viso general · MER · Procesos · NFR · ADRs · Sub-proyectos.
  // El progreso vivo del subagente analysis-agent (stores/analysis) se
  // refleja en el header mientras corre un /analysis. Runes OK (.svelte).

  import { onMount } from 'svelte';
  import {
    listAnalysisVersions,
    getAnalysisVersion,
    getAnalysisEntities,
    getAnalysisRelationships,
    getAnalysisAdrs,
    getAnalysisSubProjects,
    patchAnalysisDiagrams,
    type AnalysisVersion,
    type DomainEntity,
    type DomainRelationship,
    type Adr,
    type SubProject,
    type SubProjectContract,
    type ProcessDiagram,
    type NfrAnalysis
  } from '$lib/api/analysis';
  import {
    analysisRunning,
    analysisStage,
    analysisReady,
    liveEntityCount,
    liveAdrCount,
    liveSubProjectCount,
    liveRelationshipCount,
    liveProcessDiagramCount
  } from '$lib/stores/analysis';
  import MermaidRenderer from '$lib/components/MermaidRenderer.svelte';

  let {
    projectId,
    onClose
  }: { projectId: number | null; onClose?: () => void } = $props();

  type Tab =
    | 'overview'
    | 'architecture'
    | 'mer'
    | 'process'
    | 'nfr'
    | 'adrs'
    | 'subprojects';
  const TABS: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Visión general' },
    { id: 'architecture', label: 'Arquitectura' },
    { id: 'mer', label: 'MER' },
    { id: 'process', label: 'Procesos' },
    { id: 'nfr', label: 'NFR' },
    { id: 'adrs', label: 'ADRs' },
    { id: 'subprojects', label: 'Sub-proyectos' }
  ];

  let versions = $state<AnalysisVersion[]>([]);
  let active = $state<AnalysisVersion | null>(null);

  async function saveDiagram(field: string, value: unknown) {
    if (!active) return;
    const updated = await patchAnalysisDiagrams(
      active.project_id,
      active.version,
      { [field]: value } as never,
    );
    active = updated;
  }

  const isEditable = $derived(active?.status === 'candidate');
  let selVersion = $state<number | null>(null);
  let tab = $state<Tab>('overview');
  let loading = $state(false);
  let error = $state<string | null>(null);

  // Datos lazy-load por pestaña (null = no cargado todavía).
  let entities = $state<DomainEntity[] | null>(null);
  let relationships = $state<DomainRelationship[] | null>(null);
  let adrs = $state<Adr[] | null>(null);
  let subProjects = $state<SubProject[] | null>(null);
  let contracts = $state<SubProjectContract[] | null>(null);
  let merLoading = $state(false);
  let adrsLoading = $state(false);
  let subProjectsLoading = $state(false);

  // Diagramas derivados del detalle activo (vienen en el AnalysisVersion).
  let processDiagrams = $derived<ProcessDiagram[]>(
    active?.process_diagrams ?? []
  );
  let nfr = $derived<NfrAnalysis | null>(active?.nfr_analysis ?? null);

  async function loadVersions(): Promise<void> {
    if (projectId === null) return;
    loading = true;
    error = null;
    try {
      versions = await listAnalysisVersions(projectId);
      if (versions.length > 0) {
        selVersion = versions[0].version; // la más reciente primero
        await loadDetail();
      } else {
        active = null;
        resetLazy();
      }
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  function resetLazy(): void {
    entities = null;
    relationships = null;
    adrs = null;
    subProjects = null;
    contracts = null;
  }

  async function loadDetail(): Promise<void> {
    if (projectId === null || selVersion === null) return;
    loading = true;
    error = null;
    resetLazy();
    try {
      active = await getAnalysisVersion(projectId, selVersion);
    } catch (e) {
      error = (e as Error).message;
      active = null;
    } finally {
      loading = false;
    }
  }

  async function loadMer(): Promise<void> {
    if (projectId === null || selVersion === null || entities !== null) return;
    merLoading = true;
    try {
      // Fetch paralelo: entidades y relaciones son independientes.
      const [ents, rels] = await Promise.all([
        getAnalysisEntities(projectId, selVersion),
        getAnalysisRelationships(projectId, selVersion)
      ]);
      entities = ents;
      relationships = rels;
    } catch {
      entities = [];
      relationships = [];
    } finally {
      merLoading = false;
    }
  }

  async function loadAdrs(): Promise<void> {
    if (projectId === null || selVersion === null || adrs !== null) return;
    adrsLoading = true;
    try {
      adrs = await getAnalysisAdrs(projectId, selVersion);
    } catch {
      adrs = [];
    } finally {
      adrsLoading = false;
    }
  }

  async function loadSubProjects(): Promise<void> {
    if (projectId === null || selVersion === null || subProjects !== null)
      return;
    subProjectsLoading = true;
    try {
      const data = await getAnalysisSubProjects(projectId, selVersion);
      subProjects = data.subprojects;
      contracts = data.contracts;
    } catch {
      subProjects = [];
      contracts = [];
    } finally {
      subProjectsLoading = false;
    }
  }

  function selectTab(t: Tab): void {
    tab = t;
    if (t === 'mer') void loadMer();
    else if (t === 'adrs') void loadAdrs();
    else if (t === 'subprojects') void loadSubProjects();
  }

  function fmtDate(iso: string | null): string {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  function downloadJson(): void {
    if (!active) return;
    const payload = {
      version: active.version,
      status: active.status,
      srs_version: active.srs_version,
      requirement_count: active.requirement_count,
      mer_diagram: active.mer_diagram,
      component_diagram: active.component_diagram,
      system_architecture_diagram: active.system_architecture_diagram,
      infrastructure_diagram: active.infrastructure_diagram,
      process_diagrams: active.process_diagrams,
      nfr_analysis: active.nfr_analysis,
      traceability: active.traceability,
      entities: entities ?? null,
      relationships: relationships ?? null,
      adrs: adrs ?? null,
      subprojects: subProjects ?? null,
      contracts: contracts ?? null,
      generated_at: active.generated_at
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json'
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `Analysis_v${active.version}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const STATUS_CLASS: Record<string, string> = {
    candidate: 'text-accent border-accent/40 bg-accent/5',
    in_review: 'text-warning border-warning/40 bg-warning/5',
    locked: 'text-success border-success/40 bg-success/5'
  };

  function statusClass(status: string): string {
    return STATUS_CLASS[status] ?? 'text-text-dim border-border bg-surface-2';
  }

  const STATUS_LABEL: Record<string, string> = {
    candidate: 'candidato',
    in_review: 'en revisión',
    locked: 'bloqueado'
  };

  function statusLabel(status: string): string {
    return STATUS_LABEL[status] ?? status;
  }

  onMount(() => {
    void loadVersions();
  });

  // Re-fetch al cambiar de proyecto.
  $effect(() => {
    if (projectId !== null) void loadVersions();
  });

  // Cuando el subagente termina un /analysis (analysis.ready), recargar para
  // mostrar la nueva versión CANDIDATE de forma reactiva.
  // Variable NO reactiva (plain let): deduplica sin disparar re-runs del effect.
  let lastReadyVersion: number | null = null;
  $effect(() => {
    const r = $analysisReady;
    if (r && r.version !== lastReadyVersion) {
      lastReadyVersion = r.version;
      void loadVersions();
    }
  });
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <!-- Header -->
  <header
    class="h-9 flex items-center justify-between px-3 border-b border-border bg-surface-2 text-xs gap-2"
  >
    <div class="flex items-baseline gap-2 min-w-0">
      <span class="font-mono uppercase tracking-wider text-text">ANÁLISIS</span>
      {#if versions.length > 0}
        <select
          class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
          value={selVersion}
          onchange={(e) => {
            selVersion = Number((e.target as HTMLSelectElement).value);
            void loadDetail();
          }}
          title="Seleccionar versión del análisis"
        >
          {#each versions as v (v.id)}
            <option value={v.version}>
              v{v.version} · {statusLabel(v.status)} · {v.requirement_count} reqs
            </option>
          {/each}
        </select>
      {/if}
      {#if active}
        <span
          class="px-1.5 py-0.5 border {statusClass(active.status)} font-mono uppercase text-[10px]"
        >
          {statusLabel(active.status)}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      {#if $analysisRunning}
        <span
          class="text-accent font-mono animate-pulse"
          title={$analysisStage?.message ?? ''}
        >
          ⟳ {$analysisStage?.stage ?? 'generando'}…
        </span>
      {/if}
      <button
        type="button"
        class="px-2 py-0.5 text-text-dim hover:text-text font-mono disabled:opacity-50"
        onclick={() => void loadVersions()}
        disabled={loading || projectId === null}
        title="Refrescar versiones"
        >↻</button
      >
      {#if active}
        <button
          type="button"
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          onclick={downloadJson}
          title="Descargar JSON"
          aria-label="Descargar JSON"
          >↓</button
        >
      {/if}
      {#if onClose}
        <button
          type="button"
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          onclick={onClose}
          title="Cerrar visor de análisis"
          aria-label="Cerrar visor de análisis">×</button
        >
      {/if}
    </div>
  </header>

  <!-- Sub-tab bar -->
  {#if active}
    <nav class="flex items-stretch border-b border-border bg-bg text-xs overflow-x-auto">
      {#each TABS as t (t.id)}
        <button
          type="button"
          class="px-3 py-1 font-mono uppercase tracking-wide transition-colors border-b-2 whitespace-nowrap {tab === t.id
            ? 'text-text border-b-accent bg-surface'
            : 'text-text-dim border-b-transparent hover:text-text hover:bg-surface-2'}"
          onclick={() => selectTab(t.id)}>{t.label}</button
        >
      {/each}
    </nav>
  {/if}

  <!-- Body -->
  <div class="flex-1 min-h-0 overflow-y-auto">
    {#if projectId === null}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Selecciona un proyecto
      </div>
    {:else if loading && !active}
      <div
        class="h-full flex items-center justify-center text-text-dim text-sm font-mono"
      >
        Cargando análisis…
      </div>
    {:else if error}
      <div
        class="m-3 p-3 border border-danger/40 bg-danger/5 text-danger text-xs font-mono"
      >
        ! {error}
      </div>
    {:else if !active}
      <div class="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
        <span class="text-text-dim text-sm font-mono"
          >Aún no hay un análisis generado.</span
        >
        <span class="text-text-faint text-xs font-mono">
          Ejecuta <span class="text-accent">/analysis</span> en el chat para
          generar la primera versión (CANDIDATE) a partir del SRS y los
          requerimientos capturados.
        </span>
      </div>
    {:else if tab === 'overview'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        <!-- Resumen rápido -->
        <div class="flex flex-wrap gap-2 text-xs font-mono">
          <span class="px-2 py-1 border border-border bg-surface-2 text-text"
            >{active.requirement_count} requerimientos</span
          >
          <span
            class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{$analysisRunning
              ? $liveEntityCount
              : active.mer_diagram
                ? '✓'
                : 0} entidades (MER)</span
          >
          <span
            class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{processDiagrams.length} diagramas de proceso</span
          >
          <span
            class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{$analysisRunning
              ? $liveAdrCount
              : '—'} ADRs</span
          >
          <span
            class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{$analysisRunning
              ? $liveSubProjectCount
              : '—'} sub-proyectos</span
          >
        </div>

        <!-- Metadata -->
        <div class="text-[10px] font-mono text-text-faint border-t border-border pt-2">
          v{active.version} · SRS v{active.srs_version ?? '—'} · generado:
          {fmtDate(active.generated_at)}
          {#if active.locked_at}· bloqueado: {fmtDate(active.locked_at)}{/if}
        </div>
      </div>
    {:else if tab === 'architecture'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        <!-- System Architecture -->
        <div>
          <h3
            class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
            >Arquitectura del sistema</h3
          >
          {#if active.system_architecture_diagram}
            <div class="border border-border bg-bg">
              <MermaidRenderer
                code={active.system_architecture_diagram}
                id="sys-arch-diagram"
                editable={isEditable}
                onsave={(c) => saveDiagram('system_architecture_diagram', c)}
              />
            </div>
            {#if active.system_architecture_description}
              <p class="mt-1 text-text-dim italic">
                ↳ {active.system_architecture_description}
              </p>
            {/if}
          {:else}
            <p class="text-text-faint text-xs font-mono">
              Sin diagrama de arquitectura para esta versión.
            </p>
          {/if}
        </div>

        <!-- Infrastructure -->
        <div>
          <h3
            class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
            >Infraestructura sugerida</h3
          >
          {#if active.infrastructure_diagram}
            <div class="border border-border bg-bg">
              <MermaidRenderer
                code={active.infrastructure_diagram}
                id="infra-diagram"
                editable={isEditable}
                onsave={(c) => saveDiagram('infrastructure_diagram', c)}
              />
            </div>
            {#if active.infrastructure_description}
              <p class="mt-1 text-text-dim italic">
                ↳ {active.infrastructure_description}
              </p>
            {/if}
          {:else}
            <p class="text-text-faint text-xs font-mono">
              Sin diagrama de infraestructura para esta versión.
            </p>
          {/if}
        </div>
      </div>
    {:else if tab === 'mer'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if merLoading}
          <div class="text-text-dim text-xs font-mono">Cargando MER…</div>
        {:else}
          <!-- erDiagram -->
          {#if active.mer_diagram}
            <div>
              <h3
                class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Modelo Entidad-Relación</h3
              >
              <div class="border border-border bg-bg">
                <MermaidRenderer code={active.mer_diagram} editable={isEditable} onsave={(c) => saveDiagram('mer_diagram', c)} />
              </div>
              {#if active.mer_diagram_description}
                <p class="mt-1 text-text-dim italic">↳ {active.mer_diagram_description}</p>
              {/if}
            </div>
          {/if}

          <!-- Entidades -->
          {#if entities && entities.length > 0}
            <div>
              <h3
                class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Entidades ({entities.length})</h3
              >
              <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                {#each entities as ent (ent.id)}
                  <div class="border border-border bg-surface-2 p-2 text-xs">
                    <div class="flex items-baseline gap-2 flex-wrap font-mono">
                      <span class="text-text font-semibold">{ent.name}</span>
                      <span class="text-text-faint text-[10px]">{ent.code}</span>
                      {#if ent.aggregate_root}
                        <span
                          class="px-1 py-0 border border-accent/40 text-accent text-[9px] uppercase"
                          >aggregate root</span
                        >
                      {/if}
                      {#if ent.bounded_context}
                        <span class="text-text-dim text-[10px]"
                          >· {ent.bounded_context}</span
                        >
                      {/if}
                    </div>
                    {#if ent.description}
                      <p class="mt-1 text-text-dim">{ent.description}</p>
                    {/if}
                    {#if ent.attributes.length > 0}
                      <table class="mt-2 w-full text-[11px] font-mono">
                        <tbody>
                          {#each ent.attributes as attr (attr.name)}
                            <tr class="border-b border-border/30">
                              <td class="py-0.5 pr-2 {attr.is_key
                                ? 'text-accent font-semibold'
                                : 'text-text'}"
                                >{attr.is_key ? '🔑 ' : ''}{attr.name}</td
                              >
                              <td class="py-0.5 px-2 text-text-dim"
                                >{attr.type}{attr.required ? ' *' : ''}</td
                              >
                              <td class="py-0.5 pl-2 text-text-faint"
                                >{attr.description}</td
                              >
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                    {/if}
                    {#if ent.traced_req_codes.length > 0}
                      <div class="mt-1 flex flex-wrap gap-1 text-[9px] font-mono">
                        <span class="text-text-faint">traza:</span>
                        {#each ent.traced_req_codes as code (code)}
                          <span class="text-accent">{code}</span>
                        {/each}
                      </div>
                    {/if}
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Relaciones -->
          {#if relationships && relationships.length > 0}
            <div>
              <h3
                class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Relaciones ({relationships.length})</h3
              >
              <table class="w-full text-xs font-mono border-collapse">
                <tbody>
                  {#each relationships as rel (rel.id)}
                    <tr class="border-b border-border/50">
                      <td class="py-1 pr-2 text-text">{rel.from_entity_code}</td>
                      <td class="py-1 px-2 text-accent whitespace-nowrap"
                        >{rel.cardinality}</td
                      >
                      <td class="py-1 px-2 text-text">{rel.to_entity_code}</td>
                      <td class="py-1 pl-2 text-text-dim"
                        >{rel.label ?? rel.description ?? ''}</td
                      >
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}

          {#if (!entities || entities.length === 0) && !active.mer_diagram}
            <div class="text-text-faint text-xs font-mono">
              Sin MER para esta versión.
            </div>
          {/if}
        {/if}
      </div>
    {:else if tab === 'process'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if processDiagrams.length === 0}
          <div class="text-text-faint text-xs font-mono">
            Sin diagramas de procesos para esta versión.
          </div>
        {:else}
          {#each processDiagrams as diag, i (diag.name + '-' + i)}
            <div>
              <div class="flex items-baseline gap-2 mb-2">
                <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim">
                  {diag.entity_name ? `${diag.entity_name} · ${diag.name}` : diag.name}
                </h3>
                <span class="text-[10px] font-mono text-text-faint uppercase"
                  >{diag.type}</span
                >
              </div>
              <div class="border border-border bg-bg">
                <MermaidRenderer code={diag.mermaid} id={`proc-${i}-${diag.name}`} editable={isEditable} onsave={async (c) => {
                  const updated = (active?.process_diagrams ?? []).map((d, j) =>
                    j === i ? { ...d, mermaid: c } : d
                  );
                  await saveDiagram('process_diagrams', updated);
                }} />
              </div>
              {#if diag.description}
                <p class="mt-1 text-text-dim italic">↳ {diag.description}</p>
              {/if}
              {#if diag.traced_req_codes.length > 0}
                <div class="mt-1 flex flex-wrap gap-1 text-[9px] font-mono">
                  <span class="text-text-faint">traza:</span>
                  {#each diag.traced_req_codes as code (code)}
                    <span class="text-accent">{code}</span>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    {:else if tab === 'nfr'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if !nfr}
          <div class="text-text-faint text-xs font-mono">
            Sin análisis NFR para esta versión.
          </div>
        {:else}
          <!-- Decisiones NFR -->
          {#if nfr.decisions.length > 0}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Decisiones ({nfr.decisions.length})</h3
              >
              <ul class="space-y-2">
                {#each nfr.decisions as d, i (i)}
                  <li class="border border-border bg-surface-2 p-2 text-xs">
                    <div class="font-mono text-text-faint text-[10px]">
                      {(d.code as string | undefined) ?? `#${i + 1}`}
                    </div>
                    <div class="text-text">
                      {(d.statement as string | undefined) ??
                        (d.description as string | undefined) ??
                        JSON.stringify(d)}
                    </div>
                    {#if d.rationale as string | undefined}
                      <div class="text-text-dim italic mt-1">
                        ↳ {d.rationale}
                      </div>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/if}

          <!-- Stack recomendado -->
          {#if nfr.stack.length > 0}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Stack recomendado</h3
              >
              <ul class="space-y-1 text-xs">
                {#each nfr.stack as s, i (i)}
                  <li class="font-mono">
                    <span class="text-accent"
                      >{(s.layer as string | undefined) ??
                        (s.component as string | undefined) ??
                        `#${i + 1}`}</span
                    >
                    <span class="text-text-dim"> · </span>
                    <span class="text-text"
                      >{(s.choice as string | undefined) ??
                        (s.technology as string | undefined) ??
                        JSON.stringify(s)}</span
                    >
                  </li>
                {/each}
              </ul>
            </div>
          {/if}

          <!-- Consistencia de datos -->
          {#if nfr.data_consistency}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Consistencia de datos</h3
              >
              <p class="text-xs text-text">{nfr.data_consistency}</p>
            </div>
          {/if}

          <!-- Patrones -->
          {#if nfr.patterns}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Patrones</h3
              >
              <p class="text-xs text-text whitespace-pre-line">{nfr.patterns}</p>
            </div>
          {/if}
        {/if}
      </div>
    {:else if tab === 'adrs'}
      <div class="p-4 max-w-4xl mx-auto space-y-3">
        {#if adrsLoading}
          <div class="text-text-dim text-xs font-mono">Cargando ADRs…</div>
        {:else if adrs && adrs.length === 0}
          <div class="text-text-faint text-xs font-mono">
            Sin ADRs para esta versión. Los ADRs se generan a partir de las
            decisiones NFR y de arquitectura.
          </div>
        {:else if adrs}
          {#each adrs as adr (adr.id)}
            <article class="border border-border bg-surface-2 p-3 text-xs">
              <header class="flex items-baseline gap-2 flex-wrap font-mono">
                <span class="text-text font-semibold">{adr.title}</span>
                <span class="text-text-faint text-[10px]">{adr.code}</span>
                <span class="text-[10px] uppercase text-text-dim border border-border px-1"
                  >{adr.status}</span
                >
              </header>

              <section class="mt-2">
                <h4 class="text-[10px] font-mono uppercase text-text-faint"
                  >Contexto</h4
                >
                <p class="text-text-dim">{adr.context}</p>
              </section>

              <section class="mt-2">
                <h4 class="text-[10px] font-mono uppercase text-text-faint"
                  >Decisión</h4
                >
                <p class="text-text">{adr.decision}</p>
              </section>

              {#if adr.alternatives && adr.alternatives.length > 0}
                <section class="mt-2">
                  <h4 class="text-[10px] font-mono uppercase text-text-faint"
                    >Alternativas</h4
                  >
                  <ul class="list-disc list-inside text-text-dim">
                    {#each adr.alternatives as alt, i (i)}
                      <li>
                        {(alt.name as string | undefined) ??
                          (alt.title as string | undefined) ??
                          JSON.stringify(alt)}
                        {#if (alt.tradeoff as string | undefined) ??
                           (alt.pros as string | undefined)}
                          <span class="text-text-faint">
                            — {(alt.tradeoff as string | undefined) ??
                              (alt.pros as string | undefined)}
                          </span>
                        {/if}
                      </li>
                    {/each}
                  </ul>
                </section>
              {/if}

              {#if adr.rationale}
                <section class="mt-2">
                  <h4 class="text-[10px] font-mono uppercase text-text-faint"
                    >Justificación</h4
                  >
                  <p class="text-text-dim italic">{adr.rationale}</p>
                </section>
              {/if}

              {#if adr.nfr_codes.length > 0}
                <footer class="mt-2 flex flex-wrap gap-1 text-[9px] font-mono">
                  <span class="text-text-faint">NFRs:</span>
                  {#each adr.nfr_codes as code (code)}
                    <span class="text-accent">{code}</span>
                  {/each}
                </footer>
              {/if}
            </article>
          {/each}
        {/if}
      </div>
    {:else if tab === 'subprojects'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if subProjectsLoading}
          <div class="text-text-dim text-xs font-mono">
            Cargando sub-proyectos…
          </div>
        {:else if subProjects && subProjects.length === 0}
          <div class="text-text-faint text-xs font-mono">
            Sin sub-proyectos para esta versión. La descomposición arquitectónica
            se genera a partir del MER y los bounded contexts.
          </div>
        {:else if subProjects}
          <!-- Component diagram -->
          {#if active.component_diagram}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Diagrama de componentes</h3
              >
              <div class="border border-border bg-bg">
                <MermaidRenderer code={active.component_diagram} id="subcomp-diagram" editable={isEditable} onsave={(c) => saveDiagram('component_diagram', c)} />
              </div>
              {#if active.component_diagram_description}
                <p class="mt-1 text-text-dim italic">↳ {active.component_diagram_description}</p>
              {/if}
            </div>
          {/if}

          <!-- Sub-proyectos -->
          <div>
            <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
              >Sub-proyectos ({subProjects.length})</h3
            >
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
              {#each subProjects as sp (sp.id)}
                <div class="border border-border bg-surface-2 p-2 text-xs">
                  <div class="flex items-baseline gap-2 flex-wrap font-mono">
                    <span class="text-text font-semibold">{sp.name}</span>
                    <span class="text-text-faint text-[10px]">{sp.code}</span>
                  </div>
                  <p class="mt-1 text-text-dim">{sp.responsibility}</p>

                  {#if Object.keys(sp.stack).length > 0}
                    <div class="mt-2">
                      <div class="text-[10px] uppercase text-text-faint font-mono"
                        >Stack</div
                      >
                      <div class="flex flex-wrap gap-1 mt-0.5">
                        {#each Object.entries(sp.stack) as [layer, choice] (layer)}
                          <span class="font-mono text-[10px] px-1 border border-border text-text-dim"
                            >{layer}: {choice}</span
                          >
                        {/each}
                      </div>
                    </div>
                  {/if}

                  {#if sp.bounded_contexts.length > 0}
                    <div class="mt-1 flex flex-wrap gap-1 text-[9px] font-mono">
                      <span class="text-text-faint">BC:</span>
                      {#each sp.bounded_contexts as bc (bc)}
                        <span class="text-text-dim">{bc}</span>
                      {/each}
                    </div>
                  {/if}

                  {#if sp.entity_codes.length > 0}
                    <div class="mt-1 flex flex-wrap gap-1 text-[9px] font-mono">
                      <span class="text-text-faint">entidades:</span>
                      {#each sp.entity_codes as ec (ec)}
                        <span class="text-accent">{ec}</span>
                      {/each}
                    </div>
                  {/if}

                  {#if sp.nfr_codes.length > 0}
                    <div class="mt-1 flex flex-wrap gap-1 text-[9px] font-mono">
                      <span class="text-text-faint">NFRs:</span>
                      {#each sp.nfr_codes as code (code)}
                        <span class="text-accent">{code}</span>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          </div>

          <!-- Contratos -->
          {#if contracts && contracts.length > 0}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Contratos ({contracts.length})</h3
              >
              <ul class="space-y-2">
                {#each contracts as c (c.id)}
                  <li class="border border-border bg-surface-2 p-2 text-xs">
                    <div class="flex items-baseline gap-2 flex-wrap font-mono">
                      <span class="text-accent">{c.from_subproject_code}</span>
                      <span class="text-text-faint">→</span>
                      <span class="text-accent">{c.to_subproject_code}</span>
                      <span class="text-[10px] uppercase border border-border px-1 text-text-dim"
                        >{c.contract_type}</span
                      >
                    </div>
                    <div class="text-text font-semibold mt-1">{c.name}</div>
                    {#if c.description}
                      <p class="text-text-dim mt-0.5">{c.description}</p>
                    {/if}
                    {#if c.spec}
                      <details class="mt-1">
                        <summary class="text-[10px] font-mono text-text-faint cursor-pointer"
                          >Spec</summary
                        >
                        <pre class="text-[10px] font-mono text-text-dim mt-1 whitespace-pre-wrap bg-bg p-1 border border-border">{c.spec}</pre>
                      </details>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
</section>
