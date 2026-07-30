<script lang="ts">
  // Visor del SRS versionado (SrsDocument). Reemplaza la proyección on-the-fly:
  // cada /srs genera una versión CANDIDATE persistida, con análisis de calidad,
  // cobertura ISO 25010, goals (GORE) y matriz de trazabilidad. Se renderiza
  // como view tab del FileViewer (botón SRS en +page.svelte -> openView('srs')).
  //
  // Pestañas: Documento (markdown snapshot) · Calidad (hallazgos) · Cobertura
  // (ISO 25010 + 29148) · Trazabilidad (goal <-> req <-> fuente). El progreso
  // vivo del subagente srs-agent (stores/srs) se refleja en el header mientras
  // corre un /srs. Runes OK (.svelte).

  import { onMount } from 'svelte';
  import {
    listSrsVersions,
    getSrsVersion,
    getQuality,
    type SrsVersion,
    type Finding
  } from '$lib/api/srs';
  import { renderMarkdown } from '$lib/utils/markdown';
  import { srsRunning, srsStage, srsReady } from '$lib/stores/srs';
  import RequirementForm from '$lib/components/RequirementForm.svelte';
  import { getRequirement, type RequirementDetail } from '$lib/api/requirements';

  let {
    projectId,
    onClose
  }: { projectId: number | null; onClose?: () => void } = $props();

  type Tab = 'document' | 'quality' | 'coverage' | 'traceability';
  const TABS: { id: Tab; label: string }[] = [
    { id: 'document', label: 'Documento' },
    { id: 'quality', label: 'Calidad' },
    { id: 'coverage', label: 'Cobertura' },
    { id: 'traceability', label: 'Trazabilidad' }
  ];

  let versions = $state<SrsVersion[]>([]);
  let active = $state<SrsVersion | null>(null);
  let selVersion = $state<number | null>(null);
  let tab = $state<Tab>('document');
  let loading = $state(false);
  let error = $state<string | null>(null);
  // Hallazgos detallados (lazy: solo al abrir la pestaña Calidad). La versión
  // trae el resumen; la lista completa vive en GET /quality (por proyecto).
  let findings = $state<Finding[] | null>(null);
  let findingsLoading = $state(false);
  // Peek de un requerimiento desde un hallazgo de la pestana Calidad:
  // fetchea el detalle localmente (sin tocar el store compartido
  // selectedId) y lo muestra en un panel dentro del propio tab, sin
  // salir de Calidad. Editar/guardar usa detail.id (no el store).
  let peekedReq = $state<RequirementDetail | null>(null);
  let peekLoading = $state(false);
  let peekError = $state<string | null>(null);

  async function peekRequirement(reqId: number): Promise<void> {
    peekLoading = true;
    peekError = null;
    try {
      peekedReq = await getRequirement(reqId);
    } catch (e) {
      peekError = (e as Error).message;
    } finally {
      peekLoading = false;
    }
  }

  let html = $derived(active?.markdown ? renderMarkdown(active.markdown) : '');
  let coverage = $derived((active?.coverage ?? {}) as Record<string, any>);
  let qualitySummary = $derived(
    (active?.quality_summary ?? {}) as Record<string, any>
  );
  let traceability = $derived((active?.traceability ?? {}) as Record<string, any>);

  let iso25010 = $derived(
    (coverage.iso_25010 ?? {}) as Record<string, { count: number; label: string }>
  );
  let gaps25010 = $derived((coverage.gaps_25010 ?? []) as string[]);
  let totals = $derived((coverage.totals ?? {}) as Record<string, number>);
  let sections29148 = $derived(
    (coverage.iso_29148_sections ?? {}) as Record<string, boolean>
  );
  let bySeverity = $derived(
    (qualitySummary.by_severity ?? {}) as Record<string, number>
  );
  let matrix = $derived((traceability.matrix ?? []) as Record<string, any>[]);
  let traceGoals = $derived((traceability.goals ?? []) as Record<string, any>[]);
  // Mapa goal_id -> code para legibilizar la matriz.
  let goalCodeMap = $derived(
    Object.fromEntries(traceGoals.map((g) => [g.id, g.code])) as Record<
      number,
      string
    >
  );

  async function loadVersions(): Promise<void> {
    if (projectId === null) return;
    loading = true;
    error = null;
    try {
      versions = await listSrsVersions(projectId);
      if (versions.length > 0) {
        selVersion = versions[0].version; // la más reciente primero
        await loadDetail();
      } else {
        active = null;
        findings = null;
      }
    } catch (e) {
      error = (e as Error).message;
    } finally {
      loading = false;
    }
  }

  async function loadDetail(): Promise<void> {
    if (projectId === null || selVersion === null) return;
    loading = true;
    error = null;
    findings = null;
    try {
      active = await getSrsVersion(projectId, selVersion);
    } catch (e) {
      error = (e as Error).message;
      active = null;
    } finally {
      loading = false;
    }
  }

  async function loadFindings(): Promise<void> {
    if (projectId === null || findings !== null) return;
    findingsLoading = true;
    try {
      const q = await getQuality(projectId);
      findings = q.findings;
    } catch {
      findings = [];
    } finally {
      findingsLoading = false;
    }
  }

  function selectTab(t: Tab): void {
    tab = t;
    if (t === 'quality') void loadFindings();
  }

  function fmtDate(iso: string | null): string {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  const SEV_CLASS: Record<string, string> = {
    blocker: 'text-danger border-danger/40 bg-danger/5',
    major: 'text-warning border-warning/40 bg-warning/5',
    minor: 'text-text-dim border-border bg-surface-2',
    info: 'text-text-faint border-border bg-surface-2'
  };

  function sevClass(sev: string): string {
    return SEV_CLASS[sev] ?? SEV_CLASS.info;
  }

  const RELATION_LABEL: Record<string, string> = {
    realizes: 'realiza',
    contributes: 'contribuye',
    conflicts: 'conflicto'
  };

  onMount(() => {
    void loadVersions();
  });

  // Re-fetch al cambiar de proyecto.
  $effect(() => {
    if (projectId !== null) void loadVersions();
  });

  // Cuando el subagente termina un /srs (srs.ready), recargar para mostrar la
  // nueva versión CANDIDATE de forma reactiva.
  // Variable NO reactiva (plain let): deduplica sin disparar re-runs del effect.
  let lastReadyVersion: number | null = null;
  $effect(() => {
    const r = $srsReady;
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
      <span class="font-mono uppercase tracking-wider text-text">SRS</span>
      {#if versions.length > 0}
        <select
          class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
          value={selVersion}
          onchange={(e) => {
            selVersion = Number((e.target as HTMLSelectElement).value);
            void loadDetail();
          }}
          title="Seleccionar versión del SRS"
        >
          {#each versions as v (v.id)}
            <option value={v.version}>
              v{v.version} · {v.status} · {v.requirement_count} reqs
            </option>
          {/each}
        </select>
      {/if}
      {#if active}
        <span class="text-text-dim font-mono truncate">
          {active.requirement_count} requerimientos
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      {#if $srsRunning}
        <span class="text-accent font-mono animate-pulse" title={$srsStage?.message ?? ''}>
          ⟳ {$srsStage?.stage ?? 'generando'}…
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
      {#if onClose}
        <button
          type="button"
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          onclick={onClose}
          title="Cerrar visor SRS"
          aria-label="Cerrar visor SRS">×</button
        >
      {/if}
    </div>
  </header>

  <!-- Sub-tab bar -->
  {#if active}
    <nav class="flex items-stretch border-b border-border bg-bg text-xs">
      {#each TABS as t (t.id)}
        <button
          type="button"
          class="px-3 py-1 font-mono uppercase tracking-wide transition-colors border-b-2 {tab === t.id
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
        Cargando SRS…
      </div>
    {:else if error}
      <div
        class="m-3 p-3 border border-danger/40 bg-danger/5 text-danger text-xs font-mono"
      >
        ! {error}
      </div>
    {:else if !active}
      <div class="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
        <span class="text-text-dim text-sm font-mono">Aún no hay un SRS generado.</span>
        <span class="text-text-faint text-xs font-mono">
          Ejecuta <span class="text-accent">/srs</span> en el chat para generar la
          primera versión (CANDIDATE) a partir de los requerimientos capturados.
        </span>
      </div>
    {:else if tab === 'document'}
      <article class="md-body p-6 max-w-4xl mx-auto">
        {@html html}
      </article>
      <footer
        class="px-6 py-2 border-t border-border text-[10px] font-mono text-text-faint"
      >
        v{active.version} · {active.status} · generado: {fmtDate(active.generated_at)}
        {#if active.generated_by} · por {active.generated_by}{/if}
      </footer>
    {:else if tab === 'quality'}
      {#if peekedReq}
        <!-- Peek del requerimiento desde un hallazgo: detalle en el propio
             tab de Calidad, sin navegar al explorer ni tocar el store. -->
        <div
          class="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-surface-2 px-3 py-1.5 text-xs"
        >
          <button
            type="button"
            class="font-mono text-text-dim hover:text-text"
            onclick={() => (peekedReq = null)}
            title="Volver a la lista de hallazgos"
          >← hallazgos</button>
          <span class="font-mono text-text">{peekedReq.code}</span>
          {#if peekLoading}<span class="text-text-faint">cargando…</span>{/if}
        </div>
        {#if peekError}
          <div class="m-3 p-2 border border-danger/40 bg-danger/5 text-danger text-xs font-mono">
            ! {peekError}
          </div>
        {:else}
          {#key peekedReq.id}
            <div class="h-full"><RequirementForm detail={peekedReq} /></div>
          {/key}
        {/if}
      {:else}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        <!-- Resumen -->
        <div class="flex flex-wrap gap-2 text-xs font-mono">
          <span class="px-2 py-1 border border-border bg-surface-2 text-text"
            >{qualitySummary.total_findings ?? 0} hallazgos</span
          >
          {#each Object.entries(bySeverity) as [sev, n] (sev)}
            <span class="px-2 py-1 border {sevClass(sev)}">{sev}: {n}</span>
          {/each}
        </div>

        <!-- Hallazgos detallados -->
        {#if findingsLoading}
          <div class="text-text-dim text-xs font-mono">Cargando hallazgos…</div>
        {:else if findings && findings.length === 0}
          <div class="text-text-faint text-xs font-mono">
            Sin hallazgos de calidad. Los requerimientos vivos pasan los
            pre-checks (INCOSE / smells / EARS).
          </div>
        {:else if findings}
          <ul class="space-y-2">
            {#each findings as f (f.id)}
              <li class="border border-border bg-surface-2 p-2 text-xs">
                <div class="flex items-center gap-2 flex-wrap font-mono">
                  <span class="px-1.5 py-0.5 border {sevClass(f.severity)}">{f.severity}</span>
                  <span class="text-text-faint">{f.rule_id}</span>
                  <span class="text-text-dim">· {f.dimension}</span>
                  {#if f.req_id !== null}
                    <button
                      type="button"
                      class="text-text-dim hover:text-accent underline-offset-2 hover:underline"
                      onclick={() => {
                        if (f.req_id !== null) peekRequirement(f.req_id);
                      }}
                      title="Ver detalle del requerimiento"
                    >· {f.req_code ?? '#' + f.req_id}</button>
                  {/if}
                  <span class="ml-auto text-text-faint uppercase">{f.status}</span>
                </div>
                <p class="mt-1 text-text">{f.message}</p>
                {#if f.suggestion}
                  <p class="mt-1 text-text-dim italic">↳ {f.suggestion}</p>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </div>
      {/if}
    {:else if tab === 'coverage'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        <!-- Totales -->
        <div class="flex flex-wrap gap-2 text-xs font-mono">
          <span class="px-2 py-1 border border-border bg-surface-2 text-text"
            >{totals.live ?? 0} vivos</span
          >
          <span class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{totals.functional ?? 0} funcionales</span
          >
          <span class="px-2 py-1 border border-border bg-surface-2 text-text-dim"
            >{totals.nfr ?? 0} no funcionales</span
          >
        </div>

        <!-- Matriz ISO 25010 -->
        <div>
          <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-1"
            >ISO/IEC 25010</h3
          >
          <table class="w-full text-xs font-mono border-collapse">
            <tbody>
              {#each Object.entries(iso25010) as [key, info] (key)}
                <tr class="border-b border-border/50">
                  <td class="py-1 pr-2 text-text-dim">{info.label ?? key}</td>
                  <td class="py-1 px-2 text-right {gaps25010.includes(key) ? 'text-warning' : 'text-text'}"
                    >{info.count ?? 0}</td
                  >
                  <td class="py-1 pl-2 text-[10px] uppercase {gaps25010.includes(key)
                    ? 'text-warning'
                    : 'text-text-faint'}"
                    >{gaps25010.includes(key) ? 'gap' : 'ok'}</td
                  >
                </tr>
              {/each}
            </tbody>
          </table>
        </div>

        <!-- Secciones 29148 -->
        {#if Object.keys(sections29148).length > 0}
          <div>
            <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-1"
              >Secciones 29148</h3
            >
            <div class="flex flex-wrap gap-1.5 text-[10px] font-mono">
              {#each Object.entries(sections29148) as [sec, present] (sec)}
                <span
                  class="px-1.5 py-0.5 border {present
                    ? 'border-accent/40 text-accent'
                    : 'border-border text-text-faint'}">{sec}</span
                >
              {/each}
            </div>
          </div>
        {/if}
      </div>
    {:else if tab === 'traceability'}
      <div class="p-4 max-w-4xl mx-auto space-y-4">
        {#if traceGoals.length === 0 && matrix.length === 0}
          <div class="text-text-faint text-xs font-mono">
            Sin goals ni vínculos de trazabilidad. Ejecuta /srs para inferir el
            modelo de goals (GORE) a partir de los requerimientos.
          </div>
        {/if}

        {#if traceGoals.length > 0}
          <div>
            <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-1"
              >Goals ({traceGoals.length})</h3
            >
            <ul class="space-y-1">
              {#each traceGoals as g (g.id)}
                <li class="border-l-2 border-accent/40 pl-2 text-xs">
                  <div class="font-mono text-text-faint">
                    {g.code} · <span class="text-text-dim">{g.kind}</span>
                    · {g.status}
                  </div>
                  <div class="text-text">{g.statement}</div>
                  {#if g.rationale}
                    <div class="text-text-dim italic">↳ {g.rationale}</div>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>
        {/if}

        {#if matrix.length > 0}
          <div>
            <h3 class="text-xs font-mono uppercase tracking-wider text-text-dim mb-1"
              >Matriz goal ↔ requerimiento</h3
            >
            <table class="w-full text-xs font-mono border-collapse">
              <tbody>
                {#each matrix as row (row.req_id + '-' + row.goal_id)}
                  <tr class="border-b border-border/50 align-top">
                    <td class="py-1 pr-2 text-accent whitespace-nowrap"
                      >{goalCodeMap[row.goal_id] ?? '#' + row.goal_id}</td
                    >
                    <td class="py-1 px-2 text-text-dim uppercase text-[10px]"
                      >{RELATION_LABEL[row.relation] ?? row.relation}</td
                    >
                    <td class="py-1 px-2 text-text-faint whitespace-nowrap"
                      >{row.req_code ?? '—'}</td
                    >
                    <td class="py-1 pl-2 text-text">{row.req_statement ?? ''}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>
    {/if}
  </div>
</section>
