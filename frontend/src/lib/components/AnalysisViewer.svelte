<script lang="ts">
  // Visor del Análisis versiónado (Phase 2: Analysis & Design). Muestra el
  // MER (Modelo Entidad-Relación), diagramas de procesos, análisis NFR, ADRs
  // y descomposición arquitectónica unificada. Cada /analysis genera una
  // versión CANDIDATE persistida. Renderizado como view tab del FileViewer.
  //
  // Pestañas: Visión general · Arquitectura · MER · Procesos · NFR · ADRs.
  // El tab "Arquitectura" integra sistema → proyectos → sub-proyectos →
  // componentes → contratos → infraestructura en una sola narrativa.
  // Runes OK (.svelte).

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
    type DomainAttribute,
    type DomainEntity,
    type DomainRelationship,
    type Adr,
    type SubProject,
    type SubProjectContract,
    type AnalysisProject,
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
  import GraphExplorer from '$lib/components/GraphExplorer.svelte';
  import {
    getTraceabilityGraph,
    type TraceabilityGraph,
    type GraphNode,
    type GraphEdge
  } from '$lib/api/packages';
  import {
    buildAreaDiagram,
    buildAreaGroups,
    buildCompactOverview,
    type MerAreaGroup
  } from '$lib/utils/merSubjects';

  let {
    projectId,
    onClose
  }: { projectId: number | null; onClose?: () => void } = $props();

  type Tab =
    | 'overview'
    | 'architecture'
    | 'map'
    | 'trace'
    | 'mer'
    | 'process'
    | 'nfr'
    | 'adrs';
  const TABS: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Visión general' },
    { id: 'architecture', label: 'Arquitectura' },
    { id: 'map', label: 'Mapa' },
    { id: 'trace', label: 'Trazabilidad' },
    { id: 'mer', label: 'MER' },
    { id: 'process', label: 'Procesos' },
    { id: 'nfr', label: 'NFR' },
    { id: 'adrs', label: 'ADRs' }
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
  let analysisProjects = $state<AnalysisProject[] | null>(null);
  let merLoading = $state(false);
  let adrsLoading = $state(false);
  let subProjectsLoading = $state(false);

  // --- Mapa + explorador de trazabilidad (grafo Cytoscape) ---
  let graph = $state<TraceabilityGraph | null>(null);
  let graphLoading = $state(false);
  let graphFocus = $state<string>(''); // '' = mapa completo
  let selectedNode = $state<GraphNode | null>(null);
  let selectedEdge = $state<GraphEdge | null>(null);
  let subprojectDetail = $state<SubProject | null>(null);

  // --- MER: modos de visualización del diagrama ---
  // 'diagrama': erDiagram completo persistido · 'compacto': mapa sin
  // atributos coloreado por área · 'area': subject area (sub-proyecto) con
  // atributos · 'grafo': explorador interactivo (Cytoscape).
  type MerView = 'diagrama' | 'compacto' | 'area' | 'grafo';
  let merView = $state<MerView>('diagrama');
  let merAreaKey = $state('');
  let merExtRefs = $state(false);
  let merGraphQuery = $state('');
  let merGraphArea = $state('');
  let merSelEnt = $state<DomainEntity | null>(null);
  let copyOk = $state(false);

  // Nivel de vista del Mapa: 'overview' = descomposición legible (default);
  // 'full' = grafo completo histórico (pesado, con advertencia).
  let mapView = $state<'overview' | 'full'>('overview');
  // Entidad seleccionada en el Mapa (panel de detalle del nivel elegido).
  let mapEntityDetail = $state<DomainEntity | null>(null);

  async function loadGraph(focus = '', levels = 1): Promise<void> {
    if (projectId === null || selVersion === null) return;
    graphLoading = true;
    try {
      graph = await getTraceabilityGraph(
        projectId,
        selVersion,
        focus,
        levels,
        mapView
      );
      graphFocus = focus;
      if (!focus) {
        selectedNode = null;
        mapEntityDetail = null;
      }
    } catch {
      graph = null;
    } finally {
      graphLoading = false;
    }
  }

  function setMapView(view: 'overview' | 'full'): void {
    if (mapView === view) return;
    mapView = view;
    selectedNode = null;
    subprojectDetail = null;
    mapEntityDetail = null;
    void loadGraph('', 1);
  }

  function onGraphNodeSelect(id: string, expand: boolean): void {
    const node = graph?.nodes.find((n) => n.id === id) ?? null;
    selectedEdge = null;
    mapEntityDetail = null;
    if (node?.kind === 'subproject') {
      subprojectDetail =
        subProjects?.find((sp) => sp.code === id) ?? null;
    } else {
      subprojectDetail = null;
      if (node?.kind === 'entity') void ensureEntityDetail(id);
    }
    selectedNode = node;
    if (expand) void loadGraph(id, 2);
  }

  /** Snapshot liviano de una entidad cuando el diccionario no está cargado. */
  function entitySnapshot(code: string): DomainEntity {
    return {
      id: 0,
      code,
      name: graph?.nodes.find((x) => x.id === code)?.label ?? code,
      description: '',
      attributes: [],
      aggregate_root: false,
      bounded_context: null,
      traced_req_codes: []
    };
  }

  /** Garantiza el diccionario completo para el panel de la entidad. */
  async function ensureEntityDetail(code: string): Promise<void> {
    if (entities === null) await loadMer();
    mapEntityDetail =
      entities?.find((e) => e.code === code) ?? entitySnapshot(code);
  }

  /** Clic en una entidad del sub-proyecto: vecindario de esa entidad. */
  function drillToEntity(code: string): void {
    subprojectDetail = null;
    selectedNode = null;
    mapEntityDetail = null;
    // El vecindario de una entidad solo existe en el grafo completo
    // (en overview no hay nodos de entidad y el foco daría 404).
    mapView = 'full';
    void loadGraph(code, 1);
    void ensureEntityDetail(code);
  }

  function onGraphEdgeSelect(edge: GraphEdge): void {
    selectedNode = null;
    subprojectDetail = null;
    selectedEdge = edge;
  }

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
    analysisProjects = null;
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
      analysisProjects = data.projects ?? [];
    } catch {
      subProjects = [];
      contracts = [];
    } finally {
      subProjectsLoading = false;
    }
  }

  function selectTab(t: Tab): void {
    tab = t;
    if (t === 'mer') {
      void loadMer();
      // El selector de áreas temáticas y el grafo usan los sub-proyectos.
      void loadSubProjects();
    }
    else if (t === 'adrs') void loadAdrs();
    else if (t === 'architecture') void loadSubProjects();
    else if (
      (t === 'map' || t === 'trace') &&
      graph === null &&
      projectId !== null &&
      selVersion !== null
    ) {
      void loadSubProjects(); // el panel usa los sub-proyectos lazy
      void loadGraph();
    }
  }

  // Diccionario de datos: los campos se ordenan PK → FK → resto. Un atributo
  // cuyo tipo nombra otra entidad del modelo se documenta como FK a esa
  // entidad (mismo criterio que el diagrama y el render backend).
  function attrRank(attr: DomainAttribute, all: DomainEntity[]): number {
    if (attr.is_key) return 0;
    const t = (attr.type || '').trim().toLowerCase();
    if (t && all.some((e) => e.name.toLowerCase() === t)) return 1;
    return 2;
  }

  function sortedAttrs(
    ent: DomainEntity,
    all: DomainEntity[]
  ): DomainAttribute[] {
    return [...ent.attributes].sort(
      (a, b) => attrRank(a, all) - attrRank(b, all)
    );
  }

  function attrKeyLabel(attr: DomainAttribute, all: DomainEntity[]): string {
    if (attr.is_key) return 'PK';
    const t = (attr.type || '').trim().toLowerCase();
    const target = all.find((e) => e.name.toLowerCase() === t);
    return target ? `FK → ${target.name}` : '';
  }

  // --- MER: vistas derivadas (deterministas, 0 LLM) ---
  const merAreas = $derived(buildAreaGroups(entities ?? [], subProjects));

  const merSelectedArea = $derived<MerAreaGroup | null>(
    merAreas.find((g) => g.key === merAreaKey) ?? merAreas[0] ?? null
  );

  const merDiagramCode = $derived.by(() => {
    if (!entities || !relationships) return active?.mer_diagram ?? '';
    if (merView === 'compacto') {
      return buildCompactOverview(entities, relationships, merAreas);
    }
    if (merView === 'area') {
      return merSelectedArea
        ? buildAreaDiagram(merSelectedArea, entities, relationships, {
            includeExternal: merExtRefs
          })
        : '';
    }
    return active?.mer_diagram ?? '';
  });

  const merGraphNodes = $derived.by(() => {
    if (!entities) return [];
    const area = merGraphArea
      ? merAreas.find((g) => g.key === merGraphArea)
      : null;
    const q = merGraphQuery.trim().toLowerCase();
    return entities
      .filter((e) => !area || area.entityCodes.includes(e.code))
      .filter(
        (e) =>
          !q ||
          e.name.toLowerCase().includes(q) ||
          e.code.toLowerCase().includes(q)
      )
      .map((e) => ({ id: e.code, label: e.name, kind: 'entity' as const }));
  });

  const merGraphEdges = $derived.by(() => {
    if (!relationships) return [];
    const ids = new Set(merGraphNodes.map((n) => n.id));
    return relationships
      .filter(
        (r) => ids.has(r.from_entity_code) && ids.has(r.to_entity_code)
      )
      .map((r) => ({
        from: r.from_entity_code,
        to: r.to_entity_code,
        kind: 'rel' as const,
        label: r.label ?? '',
        cardinality: r.cardinality
      }));
  });

  function onMerNodeSelect(id: string, _expand: boolean): void {
    merSelEnt = entities?.find((e) => e.code === id) ?? null;
  }

  async function copyMermaidCode(): Promise<void> {
    try {
      await navigator.clipboard.writeText(merDiagramCode);
      copyOk = true;
      setTimeout(() => (copyOk = false), 1500);
    } catch {
      /* portapapeles no disponible: se ignora */
    }
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
      projects: analysisProjects ?? null,
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

  // --- Arquitectura: resolución de códigos SUB-NNN a nombres legibles ---
  function spName(code: string): string {
    const sp = subProjects?.find((s) => s.code === code);
    return sp ? `${code} · ${sp.name}` : code;
  }

  function spContractsOf(sp: SubProject): SubProjectContract[] {
    return (contracts ?? []).filter(
      (c) =>
        c.from_subproject_code === sp.code || c.to_subproject_code === sp.code
    );
  }

  // --- NFR: categorías y capas de stack etiquetadas en español ---
  const CATEGORY_LABEL: Record<string, string> = {
    performance: 'rendimiento',
    security: 'seguridad',
    usability: 'usabilidad',
    reliability: 'confiabilidad',
    maintainability: 'mantenibilidad',
    compliance: 'cumplimiento',
    constraint: 'restricción'
  };

  function categoryLabel(category: string): string {
    return CATEGORY_LABEL[category] ?? category;
  }

  const LAYER_LABEL: Record<string, string> = {
    frontend: 'interfaz',
    backend: 'backend',
    database: 'base de datos',
    messaging: 'mensajería',
    cache: 'caché',
    deployment: 'despliegue',
    monitoring: 'monitoreo'
  };

  function layerLabel(layer: string): string {
    return LAYER_LABEL[layer] ?? layer;
  }

  // --- ADRs: estados etiquetados en español con color ---
  const ADR_STATUS_LABEL: Record<string, string> = {
    proposed: 'propuesto',
    accepted: 'aceptado',
    superseded: 'reemplazado'
  };

  const ADR_STATUS_CLASS: Record<string, string> = {
    proposed: 'text-accent border-accent/40 bg-accent/5',
    accepted: 'text-success border-success/40 bg-success/5',
    superseded: 'text-text-dim border-border bg-surface-2'
  };

  function adrStatusLabel(status: string): string {
    return ADR_STATUS_LABEL[status] ?? status;
  }

  function adrStatusClass(status: string): string {
    return ADR_STATUS_CLASS[status] ?? 'text-text-dim border-border bg-surface-2';
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
        <!-- El message viaja con el lote en curso («entidades identificadas
        (12/41 lotes)»); el stage a secas («mer») no dice nada del avance. -->
        <span
          class="text-accent font-mono animate-pulse max-w-md truncate"
          title={$analysisStage?.message ?? $analysisStage?.stage ?? ''}
        >
          ⟳ {$analysisStage?.message || $analysisStage?.stage || 'generando'}…
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
        <!-- §1 — Arquitectura del sistema -->
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

        {#if subProjectsLoading}
          <div class="border-t border-border pt-4 text-text-dim text-xs font-mono">
            Cargando descomposición…
          </div>
        {:else}
          <!-- §2 — Árbol de descomposición: proyectos → sub-proyectos -->
          {#snippet subProjectCard(sp: SubProject)}
            <div class="border border-border bg-bg p-2 text-xs">
              <div class="flex items-baseline gap-2 flex-wrap font-mono">
                <span class="text-text-faint text-[10px]">{sp.code}</span>
                <span class="text-text font-semibold">{sp.name}</span>
              </div>
              {#if sp.responsibility}
                <p class="mt-1 text-text-dim">{sp.responsibility}</p>
              {/if}
              {#if Object.keys(sp.stack).length > 0}
                <div class="mt-2">
                  <div class="text-[10px] uppercase text-text-faint font-mono">Stack</div>
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
              {#if spContractsOf(sp).length > 0}
                <div class="mt-1 text-[9px] font-mono">
                  <span class="text-text-faint uppercase">contratos:</span>
                  <ul class="mt-0.5 space-y-0.5">
                    {#each spContractsOf(sp) as c (c.id)}
                      <li>
                        <span class="text-accent"
                          >{c.from_subproject_code === sp.code ? '→' : '←'}</span
                        >
                        <span class="text-text-dim"
                          >{spName(
                            c.from_subproject_code === sp.code
                              ? c.to_subproject_code
                              : c.from_subproject_code
                          )}</span
                        >
                        <span class="text-text-faint"
                          >· {c.name} ({c.contract_type})</span
                        >
                      </li>
                    {/each}
                  </ul>
                </div>
              {/if}
            </div>
          {/snippet}
          {#if (analysisProjects && analysisProjects.length > 0) || (subProjects && subProjects.length > 0)}
            {@const orphanSubs = (subProjects ?? []).filter(
              (sp) => !(analysisProjects ?? []).some((p) => p.code === sp.project_code)
            )}
            <div class="border-t border-border pt-4">
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-1"
                >Árbol de descomposición</h3
              >
              <p class="text-text-faint text-xs mb-3">
                El sistema se divide en proyectos (subdominios del dominio); cada
                proyecto agrupa sus sub-proyectos y los contratos fijan los
                acuerdos entre sub-proyectos.
              </p>
              <div class="space-y-3">
                {#each analysisProjects ?? [] as proj (proj.id)}
                  {@const projSubs = subProjects?.filter((sp) => sp.project_code === proj.code) ?? []}
                  <div class="border border-border bg-surface-2 p-2">
                    <div class="flex items-baseline gap-2 flex-wrap">
                      <span class="text-text-faint text-[10px] font-mono">{proj.code}</span>
                      <span class="text-sm font-semibold text-text">{proj.name}</span>
                      <span class="text-[9px] uppercase px-1.5 py-0.5 border font-mono
                        {proj.domain_type === 'core'
                          ? 'border-accent/40 text-accent bg-accent/5'
                          : proj.domain_type === 'supporting'
                            ? 'border-border text-text-dim bg-surface-2'
                            : 'border-success/40 text-success bg-success/5'}"
                        >{proj.domain_type}</span
                      >
                      <span class="ml-auto text-[9px] font-mono text-text-faint">
                        {projSubs.length} sub-proyectos · {proj.entity_codes.length} entidades ·
                        {proj.traced_req_codes.length} requisitos
                      </span>
                    </div>
                    {#if proj.description}
                      <p class="text-text-dim text-xs mt-0.5">{proj.description}</p>
                    {/if}
                    {#if proj.bounded_contexts.length > 0}
                      <div class="mt-0.5 flex flex-wrap gap-1 text-[9px] font-mono">
                        <span class="text-text-faint">BCs:</span>
                        {#each proj.bounded_contexts as bc (bc)}
                          <span class="text-text-dim">{bc}</span>
                        {/each}
                      </div>
                    {/if}
                    {#if projSubs.length > 0}
                      <div class="mt-2 space-y-2 border-l-2 border-accent/40 pl-3">
                        {#each projSubs as sp (sp.id)}
                          {@render subProjectCard(sp)}
                        {/each}
                      </div>
                    {:else}
                      <p class="text-text-faint text-xs font-mono mt-2">
                        Sin sub-proyectos asignados.
                      </p>
                    {/if}
                  </div>
                {/each}
                {#if orphanSubs.length > 0}
                  <div class="border border-dashed border-warning/40 p-2">
                    <div class="flex items-baseline gap-2 flex-wrap">
                      <span class="text-sm font-semibold text-warning"
                        >Sin proyecto asignado</span
                      >
                      <span class="text-[9px] font-mono text-text-faint"
                        >{orphanSubs.length} sub-proyectos</span
                      >
                    </div>
                    <p class="text-text-faint text-xs mt-0.5">
                      No están asociados a ningún proyecto de esta versión
                      (típico de versiones refinadas antes de un fix de
                      asociación). Re-generar el análisis los re-asocia.
                    </p>
                    <div class="mt-2 space-y-2 border-l-2 border-warning/40 pl-3">
                      {#each orphanSubs as sp (sp.id)}
                        {@render subProjectCard(sp)}
                      {/each}
                    </div>
                  </div>
                {/if}
              </div>
            </div>
          {/if}

          <!-- §4 — Diagrama de Componentes -->
          {#if active.component_diagram}
            <div class="border-t border-border pt-4">
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Diagrama de Componentes</h3>
              <div class="border border-border bg-bg">
                <MermaidRenderer code={active.component_diagram} id="subcomp-diagram" editable={isEditable} onsave={(c) => saveDiagram('component_diagram', c)} />
              </div>
              {#if active.component_diagram_description}
                <p class="mt-1 text-text-dim italic">↳ {active.component_diagram_description}</p>
              {/if}
            </div>
          {/if}

          <!-- §5 — Contratos entre Sub-proyectos -->
          {#if contracts && contracts.length > 0}
            <div class="border-t border-border pt-4">
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Contratos ({contracts.length})</h3>
              <ul class="space-y-2">
                {#each contracts as c (c.id)}
                  <li class="border border-border bg-surface-2 p-2 text-xs">
                    <div class="flex items-baseline gap-2 flex-wrap font-mono">
                      <span class="text-accent">{spName(c.from_subproject_code)}</span>
                      <span class="text-text-faint">→</span>
                      <span class="text-accent">{spName(c.to_subproject_code)}</span>
                      <span class="text-[10px] uppercase border border-border px-1 text-text-dim"
                        >{c.contract_type}</span>
                    </div>
                    <div class="text-text font-semibold mt-1">{c.name}</div>
                    {#if c.description}
                      <p class="text-text-dim mt-0.5">{c.description}</p>
                    {/if}
                    {#if c.spec}
                      <details class="mt-1">
                        <summary class="text-[10px] font-mono text-text-faint cursor-pointer">Spec</summary>
                        <pre class="text-[10px] font-mono text-text-dim mt-1 whitespace-pre-wrap bg-bg p-1 border border-border">{c.spec}</pre>
                      </details>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/if}
        {/if}

        <!-- §6 — Infraestructura -->
        <div class="border-t border-border pt-4">
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
    {:else if tab === 'map' || tab === 'trace'}
      <div class="p-4 max-w-6xl mx-auto">
        {#if graphLoading && !graph}
          <div class="text-text-dim text-xs font-mono">Cargando grafo…</div>
        {:else if graph}
          <div class="grid grid-cols-[1fr_320px] gap-3">
            <div>
              {#if tab === 'map'}
                <!-- Selector de nivel: overview legible vs grafo completo -->
                <div class="flex items-center gap-2 mb-2 flex-wrap">
                  <span class="text-[10px] uppercase font-mono text-text-faint"
                    >Vista:</span
                  >
                  <button
                    type="button"
                    class="px-2 py-0.5 border text-xs font-mono {mapView === 'overview'
                      ? 'border-accent/60 text-accent bg-accent/5'
                      : 'border-border text-text-dim hover:text-text'}"
                    onclick={() => setMapView('overview')}
                    title="Solo proyectos, sub-proyectos y contratos"
                  >
                    Descomposición
                  </button>
                  <button
                    type="button"
                    class="px-2 py-0.5 border text-xs font-mono {mapView === 'full'
                      ? 'border-accent/60 text-accent bg-accent/5'
                      : 'border-border text-text-dim hover:text-text'}"
                    onclick={() => setMapView('full')}
                    title="Incluye entidades, requerimientos y tareas (pesado: puede demorar y verse denso)"
                  >
                    Grafo completo
                  </button>
                  {#if mapView === 'full'}
                    <span class="text-[10px] text-warning font-mono">
                      ⚠ pesado con muchos requerimientos
                    </span>
                  {/if}
                </div>
              {/if}
              {#if tab === 'trace'}
                <div class="flex items-center gap-2 mb-2">
                  <input
                    class="bg-bg border border-border text-text text-xs px-2 py-1 font-mono flex-1"
                    placeholder="Foco (REQ-XXXX, ENT-XXXX, SUB-001, TASK-001@WP-001…)"
                    bind:value={graphFocus}
                    onkeydown={(e) => {
                      if ((e as KeyboardEvent).key === 'Enter') {
                        void loadGraph(graphFocus.trim(), 1);
                      }
                    }}
                  />
                  <button
                    type="button"
                    class="px-2 py-1 border border-border text-text-dim hover:text-accent text-xs font-mono"
                    onclick={() => void loadGraph(graphFocus.trim(), 1)}
                  >Ir</button>
                  {#if graphFocus}
                    <button
                      type="button"
                      class="px-2 py-1 border border-border text-text-dim hover:text-text text-xs font-mono"
                      onclick={() => {
                        graphFocus = '';
                        void loadGraph('', 1);
                      }}
                    >Mapa completo</button>
                  {/if}
                </div>
              {:else if graphFocus}
                <button
                  type="button"
                  class="mb-2 px-2 py-1 border border-border text-text-dim hover:text-text text-xs font-mono"
                  onclick={() => {
                    graphFocus = '';
                    void loadGraph('', 1);
                  }}
                >← Mapa completo</button>
              {/if}
              <GraphExplorer
                nodes={graph.nodes}
                edges={graph.edges}
                height="560px"
                onNodeSelect={onGraphNodeSelect}
                onEdgeSelect={onGraphEdgeSelect}
              />
            </div>
            <aside class="border border-border bg-surface-2 p-3 text-xs overflow-y-auto max-h-[600px]">
              {#if selectedNode?.kind === 'subproject' && subprojectDetail}
                <h4 class="text-text font-semibold font-mono">{subprojectDetail.name}</h4>
                <p class="text-text-faint text-[10px] font-mono mb-2">{subprojectDetail.code}
                  {#if subprojectDetail.project_code}· {subprojectDetail.project_code}{/if}</p>
                <p class="text-text-dim">{subprojectDetail.responsibility}</p>
                {#if Object.keys(subprojectDetail.stack).length}
                  <div class="mt-2 font-mono text-[10px]">
                    {#each Object.entries(subprojectDetail.stack) as [k, v] (k)}
                      <span class="px-1 border border-border mr-1">{k}: {v}</span>
                    {/each}
                  </div>
                {/if}
                <div class="mt-2">
                  <p class="text-text-faint font-mono text-[10px] uppercase">Entidades ({subprojectDetail.entity_codes.length})</p>
                  <div class="flex flex-wrap gap-1 mt-1">
                    {#each subprojectDetail.entity_codes as ec (ec)}
                      <button
                        type="button"
                        class="text-accent text-[10px] font-mono hover:underline"
                        onclick={() => drillToEntity(ec)}
                        title="Ver el vecindario de esta entidad"
                      >{ec}</button>
                    {/each}
                  </div>
                </div>
                {#if subprojectDetail.nfr_codes.length}
                  <div class="mt-2">
                    <p class="text-text-faint font-mono text-[10px] uppercase">NFRs ({subprojectDetail.nfr_codes.length})</p>
                    <div class="flex flex-wrap gap-1 mt-1">
                      {#each subprojectDetail.nfr_codes as nc (nc)}
                        <span class="text-accent text-[10px] font-mono">{nc}</span>
                      {/each}
                    </div>
                  </div>
                {/if}
                {#if subprojectDetail}
                  {@const spDetail = subprojectDetail}
                  {@const myContracts = (contracts ?? []).filter(
                    (c) => c.from_subproject_code === spDetail.code ||
                      c.to_subproject_code === spDetail.code
                  )}
                  {#if myContracts.length}
                    <div class="mt-2">
                      <p class="text-text-faint font-mono text-[10px] uppercase">Contratos ({myContracts.length})</p>
                      <ul class="mt-1 space-y-0.5">
                        {#each myContracts as c (c.id)}
                          <li class="font-mono text-[10px]">
                            <span class:text-accent={c.from_subproject_code === spDetail.code}>
                              {c.from_subproject_code}</span> →
                            <span class:text-accent={c.to_subproject_code === spDetail.code}>
                              {c.to_subproject_code}</span>
                            <span class="text-text-dim">{c.name} ({c.contract_type})</span>
                          </li>
                        {/each}
                      </ul>
                    </div>
                  {/if}
                {/if}
              {:else if selectedNode?.kind === 'entity' && mapEntityDetail}
                <!-- Panel de entidad: detalle completo sin salir del Mapa -->
                <div class="flex items-baseline gap-2 flex-wrap font-mono">
                  <h4 class="text-text font-semibold">{mapEntityDetail.name}</h4>
                  <span class="text-text-faint text-[10px]">{mapEntityDetail.code}</span>
                  {#if mapEntityDetail.aggregate_root}
                    <span class="px-1 py-0 border border-accent/40 text-accent text-[9px] uppercase"
                      >aggregate root</span>
                  {/if}
                </div>
                {#if mapEntityDetail.bounded_context}
                  <p class="text-text-faint text-[10px] font-mono mt-0.5">{mapEntityDetail.bounded_context}</p>
                {/if}
                {#if mapEntityDetail.description}
                  <p class="text-text-dim mt-1">{mapEntityDetail.description}</p>
                {/if}
                {#if mapEntityDetail.attributes.length > 0}
                  <h5 class="mt-2 text-[10px] uppercase font-mono text-text-faint">
                    Campos ({mapEntityDetail.attributes.length})
                  </h5>
                  <table class="mt-1 w-full text-[10px] font-mono border-collapse">
                    <tbody>
                      {#each sortedAttrs(mapEntityDetail, entities ?? []) as attr (attr.name)}
                        <tr class="border-b border-border/30">
                          <td class="py-0.5 pr-1 {attr.is_key ? 'text-accent font-semibold' : 'text-text'}">
                            {attr.is_key ? '🔑 ' : ''}{attr.name}</td>
                          <td class="py-0.5 px-1 text-text-dim">
                            {attr.type}{attr.length ? `(${attr.length})` : ''}</td>
                          <td class="py-0.5 pl-1 text-text-faint text-right">
                            {attrKeyLabel(attr, entities ?? [])}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
                <h5 class="mt-2 text-[10px] uppercase font-mono text-text-faint">Relaciones</h5>
                <ul class="mt-1 space-y-0.5">
                  {#each (relationships ?? []).filter(
                    (r) =>
                      r.from_entity_code === mapEntityDetail!.code ||
                      r.to_entity_code === mapEntityDetail!.code
                  ) as r (r.id)}
                    {@const otherCode =
                      r.from_entity_code === mapEntityDetail!.code
                        ? r.to_entity_code
                        : r.from_entity_code}
                    {@const other = (entities ?? []).find((e) => e.code === otherCode)}
                    <li>
                      <button
                        type="button"
                        class="font-mono text-[10px] text-text-dim hover:text-accent text-left"
                        onclick={() => drillToEntity(otherCode)}
                      >
                        {r.from_entity_code === mapEntityDetail!.code ? '→' : '←'}
                        {other?.name ?? otherCode}
                        <span class="text-text-faint">· {r.label ?? ''} ({r.cardinality})</span>
                      </button>
                    </li>
                  {/each}
                </ul>
                <p class="text-text-faint text-[9px] font-mono mt-2">
                  {graphFocus}
                </p>
              {:else if selectedNode}
                <h4 class="text-text font-semibold font-mono">{selectedNode.label}</h4>
                <p class="text-text-faint text-[10px] font-mono mt-1">tipo: {selectedNode.kind}</p>
                {#if selectedNode.id.startsWith('REQ-')}
                  {@const req = entities?.find((e) =>
                    (e.traced_req_codes ?? []).includes(selectedNode!.id))}
                  <p class="text-text-dim mt-2 text-[11px]">
                    Aparece trazado en entidades del MER. Usá el explorador
                    para seguir su cadena (clic en vecinos, doble clic para
                    expandir).
                  </p>
                {/if}
              {:else if selectedEdge?.kind === 'contract'}
                <h4 class="text-text font-semibold font-mono">{selectedEdge.label}</h4>
                <p class="text-text-faint text-[10px] font-mono mt-1">
                  {selectedEdge.from} → {selectedEdge.to} · {selectedEdge.contract_type}</p>
                {@const c = contracts?.find(
                  (x) => x.name === selectedEdge!.label &&
                    ((x.from_subproject_code === selectedEdge!.from &&
                      x.to_subproject_code === selectedEdge!.to) ||
                      (x.from_subproject_code === selectedEdge!.to &&
                        x.to_subproject_code === selectedEdge!.from)))}
                {#if c}
                  {#if c.description}<p class="text-text-dim mt-2">{c.description}</p>{/if}
                  {#if c.spec}
                    <pre class="mt-2 p-2 bg-bg border border-border text-[10px] font-mono whitespace-pre-wrap overflow-x-auto">{c.spec}</pre>
                  {/if}
                {/if}
              {:else}
                <p class="text-text-faint font-mono text-[11px]">
                  {tab === 'trace'
                    ? 'Buscá un código o hacé clic en un nodo del mapa para ver su detalle.'
                    : 'Clic en un nodo (sub-proyecto) para ver su detalle; clic en una arista (contrato) para ver su spec.'}
                </p>
              {/if}
            </aside>
          </div>
        {/if}
      </div>
    {:else if tab === 'mer'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if merLoading}
          <div class="text-text-dim text-xs font-mono">Cargando MER…</div>
        {:else}
          <!-- erDiagram: modos diagrama completo / mapa compacto / área / grafo -->
          <div>
            <div class="flex items-center gap-2 flex-wrap mb-2">
              <h3
                class="text-xs font-mono uppercase tracking-wider text-text-dim"
                >Modelo Entidad-Relación</h3
              >
              <select
                class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
                value={merView}
                onchange={(e) =>
                  (merView = (e.target as HTMLSelectElement)
                    .value as MerView)}
                title="Modo de visualización del MER"
              >
                <option value="diagrama">Diagrama completo</option>
                <option value="compacto">Mapa compacto</option>
                <option value="area">Por área…</option>
                <option value="grafo">Grafo interactivo</option>
              </select>
              {#if merView === 'area' && merAreas.length > 0}
                <select
                  class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
                  bind:value={merAreaKey}
                  title="Área temática (sub-proyecto)"
                >
                  {#each merAreas as area (area.key)}
                    <option value={area.key}>
                      {area.label} ({area.entityCodes.length})
                    </option>
                  {/each}
                </select>
                <label
                  class="flex items-center gap-1 text-[10px] font-mono text-text-dim cursor-pointer"
                >
                  <input
                    type="checkbox"
                    bind:checked={merExtRefs}
                    class="accent-accent"
                  />
                  referencias externas
                </label>
                <button
                  type="button"
                  class="px-2 py-0.5 border border-border text-text-dim hover:text-accent text-[10px] font-mono"
                  onclick={() => void copyMermaidCode()}
                  title="Copiar el código mermaid de esta vista"
                >
                  {copyOk ? '✓ copiado' : 'copiar mermaid'}
                </button>
              {/if}
              {#if merView === 'compacto'}
                <button
                  type="button"
                  class="px-2 py-0.5 border border-border text-text-dim hover:text-accent text-[10px] font-mono"
                  onclick={() => void copyMermaidCode()}
                  title="Copiar el código mermaid de esta vista"
                >
                  {copyOk ? '✓ copiado' : 'copiar mermaid'}
                </button>
              {/if}
            </div>

            {#if merView === 'grafo'}
              <div class="flex items-center gap-2 flex-wrap mb-2">
                <input
                  class="bg-bg border border-border text-text text-xs px-2 py-1 font-mono flex-1 min-w-40"
                  placeholder="Buscar entidad por nombre o código…"
                  bind:value={merGraphQuery}
                />
                <select
                  class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
                  bind:value={merGraphArea}
                  title="Filtrar por área"
                >
                  <option value="">
                    Todas las áreas ({(entities ?? []).length})
                  </option>
                  {#each merAreas as area (area.key)}
                    <option value={area.key}>
                      {area.label} ({area.entityCodes.length})
                    </option>
                  {/each}
                </select>
              </div>
              <div class="grid grid-cols-[1fr_320px] gap-3">
                <GraphExplorer
                  nodes={merGraphNodes}
                  edges={merGraphEdges}
                  height="560px"
                  onNodeSelect={onMerNodeSelect}
                />
                <aside
                  class="border border-border bg-surface-2 p-3 text-xs overflow-y-auto max-h-[600px]"
                >
                  {#if merSelEnt}
                    <div class="flex items-baseline gap-2 flex-wrap font-mono">
                      <span class="text-text font-semibold"
                        >{merSelEnt.name}</span
                      >
                      <span class="text-text-faint text-[10px]"
                        >{merSelEnt.code}</span
                      >
                      {#if merSelEnt.aggregate_root}
                        <span
                          class="px-1 py-0 border border-accent/40 text-accent text-[9px] uppercase"
                          >aggregate root</span
                        >
                      {/if}
                    </div>
                    {#if merSelEnt.bounded_context}
                      <p class="text-text-faint text-[10px] font-mono mt-0.5">
                        {merSelEnt.bounded_context}
                      </p>
                    {/if}
                    {#if merSelEnt.description}
                      <p class="text-text-dim mt-1">{merSelEnt.description}</p>
                    {/if}
                    <h5
                      class="mt-2 text-[10px] uppercase font-mono text-text-faint"
                      >Campos ({merSelEnt.attributes.length})</h5
                    >
                    <table class="mt-1 w-full text-[10px] font-mono border-collapse">
                      <tbody>
                        {#each sortedAttrs(merSelEnt, entities ?? []) as attr (attr.name)}
                          <tr class="border-b border-border/30">
                            <td
                              class="py-0.5 pr-1 {attr.is_key
                                ? 'text-accent font-semibold'
                                : 'text-text'}"
                              >{attr.is_key ? '🔑 ' : ''}{attr.name}</td
                            >
                            <td class="py-0.5 px-1 text-text-dim"
                              >{attr.type}{attr.length
                                ? `(${attr.length})`
                                : ''}</td
                            >
                            <td
                              class="py-0.5 pl-1 text-text-faint text-right"
                              >{attrKeyLabel(attr, entities ?? [])}</td
                            >
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                    <h5
                      class="mt-2 text-[10px] uppercase font-mono text-text-faint"
                      >Relaciones</h5
                    >
                    <ul class="mt-1 space-y-0.5">
                      {#each (relationships ?? []).filter(
                        (r) =>
                          r.from_entity_code === merSelEnt!.code ||
                          r.to_entity_code === merSelEnt!.code
                      ) as r (r.id)}
                        {@const otherCode =
                          r.from_entity_code === merSelEnt!.code
                            ? r.to_entity_code
                            : r.from_entity_code}
                        {@const other = (entities ?? []).find(
                          (e) => e.code === otherCode
                        )}
                        <li>
                          <button
                            type="button"
                            class="font-mono text-[10px] text-text-dim hover:text-accent text-left"
                            onclick={() => (merSelEnt = other ?? null)}
                          >
                            {r.from_entity_code === merSelEnt!.code ? '→' : '←'}
                            {other?.name ?? otherCode}
                            <span class="text-text-faint">
                              · {r.label ?? ''} ({r.cardinality})</span
                            >
                          </button>
                        </li>
                      {/each}
                    </ul>
                  {:else}
                    <p class="text-text-faint font-mono text-[11px]">
                      Clic en una entidad para ver su detalle: descripción,
                      campos del diccionario y relaciones (clic en una
                      relación para navegar a la entidad vecina).
                    </p>
                  {/if}
                </aside>
              </div>
            {:else if merDiagramCode}
              <div class="border border-border bg-bg">
                {#if merView === 'diagrama'}
                  <MermaidRenderer
                    code={merDiagramCode}
                    id="mer-diagram"
                    editable={isEditable}
                    onsave={(c) => saveDiagram('mer_diagram', c)}
                  />
                {:else}
                  <MermaidRenderer
                    code={merDiagramCode}
                    id={`mer-view-${merView}`}
                  />
                {/if}
              </div>
              {#if merView === 'diagrama' && active.mer_diagram_description}
                <p class="mt-1 text-text-dim italic">
                  ↳ {active.mer_diagram_description}
                </p>
              {/if}
            {:else if merView === 'area'}
              <p class="text-text-faint text-xs font-mono">
                Sin entidades para esta área.
              </p>
            {:else if active.mer_diagram}
              <p class="text-text-faint text-xs font-mono">
                Generando vista…
              </p>
            {/if}
          </div>

          <!-- Diccionario de datos: índice de entidades + una sección por
               entidad (explicación + tabla de campos del modelo). -->
          {#if entities && entities.length > 0}
            <div>
              <h3
                class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Diccionario de datos ({entities.length} entidades)</h3
              >

              <!-- Índice de entidades -->
              <table class="w-full text-[11px] font-mono border-collapse mb-3">
                <thead>
                  <tr class="border-b border-border text-text-dim">
                    <th class="py-1 pr-2 text-left font-normal">Entidad</th>
                    <th class="py-1 px-2 text-left font-normal">Descripción</th>
                    <th class="py-1 px-2 text-right font-normal">Campos</th>
                  </tr>
                </thead>
                <tbody>
                  {#each entities as ent (ent.id)}
                    <tr class="border-b border-border/30">
                      <td class="py-1 pr-2 text-text">{ent.name}</td>
                      <td class="py-1 px-2 text-text-dim truncate max-w-0"
                        >{ent.description}</td
                      >
                      <td class="py-1 px-2 text-right text-text-faint"
                        >{ent.attributes.length}</td
                      >
                    </tr>
                  {/each}
                </tbody>
              </table>

              <!-- Una sección por entidad -->
              {#each entities as ent (ent.id)}
                <div class="mb-4 border-l-2 border-accent/40 pl-3">
                  <div class="flex items-baseline gap-2 flex-wrap font-mono">
                    <span class="text-text font-semibold text-sm">{ent.name}</span>
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
                    <p class="mt-1 text-xs text-text-dim">{ent.description}</p>
                  {/if}
                  {#if ent.attributes.length > 0}
                    <table class="mt-2 w-full text-[11px] font-mono border-collapse">
                      <thead>
                        <tr class="border-b border-border text-text-dim">
                          <th class="py-1 pr-2 text-left font-normal">Campo</th>
                          <th class="py-1 px-2 text-left font-normal">Tipo</th>
                          <th class="py-1 px-2 text-left font-normal">Largo</th>
                          <th class="py-1 px-2 text-left font-normal">Oblig.</th>
                          <th class="py-1 px-2 text-left font-normal">Clave</th>
                          <th class="py-1 pl-2 text-left font-normal">Descripción</th>
                        </tr>
                      </thead>
                      <tbody>
                        {#each sortedAttrs(ent, entities) as attr (attr.name)}
                          <tr class="border-b border-border/30">
                            <td class="py-0.5 pr-2 {attr.is_key
                              ? 'text-accent font-semibold'
                              : 'text-text'}"
                              >{attr.is_key ? '🔑 ' : ''}{attr.name}</td
                            >
                            <td class="py-0.5 px-2 text-text">{attr.type}</td>
                            <td class="py-0.5 px-2 text-text-dim"
                              >{attr.length ?? ''}</td
                            >
                            <td class="py-0.5 px-2 text-text-dim"
                              >{attr.required ? 'sí' : 'no'}</td
                            >
                            <td class="py-0.5 px-2 {attr.is_key
                              ? 'text-accent'
                              : 'text-text-dim'}"
                              >{attrKeyLabel(attr, entities)}</td
                            >
                            <td class="py-0.5 pl-2 text-text-faint"
                              >{attr.description}</td
                            >
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  {:else}
                    <p class="mt-2 text-[11px] font-mono text-text-faint">
                      Sin atributos modelados.
                    </p>
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
          <!-- Objetivo del tab -->
          <p class="text-text-faint text-xs">
            Cómo responde la arquitectura a los requerimientos no funcionales
            (rendimiento, seguridad, usabilidad…) capturados en la fase 1.
            Cada decisión cita el REQ-XXXX que la motiva y alimenta los ADRs
            del siguiente tab.
          </p>

          <!-- Decisiones NFR -->
          {#if nfr.decisions.length > 0}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Decisiones ({nfr.decisions.length})</h3
              >
              <ul class="space-y-2">
                {#each nfr.decisions as d, i (i)}
                  <li class="border border-border bg-surface-2 p-2 text-xs">
                    <div class="flex items-center gap-2 flex-wrap font-mono">
                      <span class="text-accent text-[10px]">{d.req_code}</span>
                      {#if d.category}
                        <span class="text-[9px] uppercase px-1.5 py-0.5 border border-border text-text-dim"
                          >{categoryLabel(d.category)}</span
                        >
                      {/if}
                      {#if d.stack_component}
                        <span class="text-[9px] px-1 border border-border text-text-faint"
                          >componente: {d.stack_component}</span
                        >
                      {/if}
                    </div>
                    <div class="text-text mt-1">{d.decision}</div>
                    {#if d.rationale}
                      <div class="text-text-dim italic mt-1">↳ {d.rationale}</div>
                    {/if}
                    {#if d.impact}
                      <div class="text-text-faint mt-1">
                        <span class="font-mono">impacto:</span> {d.impact}
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
                  <li class="border border-border bg-surface-2 p-2 font-mono">
                    <div>
                      <span class="text-accent">{layerLabel(s.layer)}</span>
                      <span class="text-text-dim"> · </span>
                      <span class="text-text">{s.technology}</span>
                    </div>
                    {#if s.rationale}
                      <div class="text-text-faint mt-0.5">↳ {s.rationale}</div>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/if}

          <!-- Estrategias transversales -->
          {#if nfr.data_consistency || nfr.patterns}
            <div>
              <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-2"
                >Estrategias transversales</h3
              >
              <div class="space-y-2 text-xs">
                {#if nfr.data_consistency}
                  <div class="border border-border bg-surface-2 p-2">
                    <div class="text-[10px] font-mono uppercase text-text-faint"
                      >Consistencia de datos</div
                    >
                    <p class="text-text mt-0.5">{nfr.data_consistency}</p>
                  </div>
                {/if}
                {#if nfr.patterns}
                  <div class="border border-border bg-surface-2 p-2">
                    <div class="text-[10px] font-mono uppercase text-text-faint"
                      >Patrones</div
                    >
                    <p class="text-text mt-0.5 whitespace-pre-line">{nfr.patterns}</p>
                  </div>
                {/if}
              </div>
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
          <!-- Objetivo del tab -->
          <p class="text-text-faint text-xs">
            Cada ADR documenta una decisión arquitectónica: el contexto que la
            motiva, qué se decidió, qué alternativas se descartaron y qué
            requerimientos no funcionales (REQ-XXXX) la justifican. Es la
            memoria de diseño del sistema.
          </p>
          {#each adrs as adr (adr.id)}
            <article class="border border-border bg-surface-2 p-3 text-xs">
              <header class="flex items-baseline gap-2 flex-wrap font-mono">
                <span class="text-text-faint text-[10px]">{adr.code}</span>
                <span class="text-text font-semibold">{adr.title}</span>
                <span class="text-[10px] uppercase border px-1 {adrStatusClass(adr.status)}"
                  >{adrStatusLabel(adr.status)}</span
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
                    >Alternativas consideradas</h4
                  >
                  <ul class="space-y-1">
                    {#each adr.alternatives as alt, i (i)}
                      <li class="border-l-2 border-border pl-2">
                        <div class="text-text-dim">{alt.name}</div>
                        {#if alt.pros}
                          <div class="text-success/90 text-[11px]">✓ {alt.pros}</div>
                        {/if}
                        {#if alt.cons}
                          <div class="text-danger/90 text-[11px]">✗ {alt.cons}</div>
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
                <footer class="mt-2 flex flex-wrap gap-1 text-[9px] font-mono items-baseline">
                  <span class="text-text-faint">Motivado por:</span>
                  {#each adr.nfr_codes as code (code)}
                    <span class="text-accent"
                      title="Requerimiento no funcional que motiva esta decisión">{code}</span
                    >
                  {/each}
                </footer>
              {/if}
            </article>
          {/each}
        {/if}
      </div>
    {/if}
  </div>
</section>
