<script lang="ts">
  // Visor de Paquetes de Trabajo (entrega a desarrolladores). Espejo de
  // SrsViewer: selector de versiones, paquetes con markdown renderizado,
  // panel de coherencia y descargas .md vía endpoints export.
  // Runes OK (.svelte).
  import { onMount } from 'svelte';
  import {
    listPackagesVersions,
    getPackagesVersion,
    packageExportUrl,
    masterExportUrl,
    type PackagesDocument,
    type PackagesVersionDetail,
    type CoherenceGate
  } from '$lib/api/packages';
  import { renderMarkdown } from '$lib/utils/markdown';

  let {
    projectId,
    onClose
  }: { projectId: number | null; onClose?: () => void } = $props();

  let versions = $state<PackagesDocument[]>([]);
  let active = $state<PackagesVersionDetail | null>(null);
  let selVersion = $state<number | null>(null);
  let selPackage = $state<string | null>(null); // WP code o 'master'
  let loading = $state(false);
  let error = $state<string | null>(null);

  let html = $derived.by(() => {
    if (!active) return '';
    if (selPackage === 'master') return renderMarkdown(active.master_markdown);
    const wp = active.packages.find((p) => p.code === selPackage);
    return wp?.markdown ? renderMarkdown(wp.markdown) : '';
  });

  const gates = $derived<CoherenceGate[]>(
    active?.coherence_report?.gates ?? []
  );
  const critique = $derived<unknown[]>(
    active?.coherence_report?.critique_findings ?? []
  );

  const STATUS_CLASS: Record<string, string> = {
    candidate: 'text-accent border-accent/40 bg-accent/5',
    in_review: 'text-warning border-warning/40 bg-warning/5',
    locked: 'text-success border-success/40 bg-success/5'
  };

  async function loadVersions(): Promise<void> {
    if (projectId === null) return;
    loading = true;
    error = null;
    try {
      versions = await listPackagesVersions(projectId);
      if (versions.length > 0) {
        selVersion = versions[0].version;
        await loadDetail();
      } else {
        active = null;
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
    try {
      active = await getPackagesVersion(projectId, selVersion);
      selPackage = active.packages.length > 0 ? active.packages[0].code : 'master';
    } catch (e) {
      error = (e as Error).message;
      active = null;
    } finally {
      loading = false;
    }
  }

  function selectVersion(v: number): void {
    selVersion = v;
    void loadDetail();
  }

  onMount(() => {
    void loadVersions();
  });

  $effect(() => {
    if (projectId !== null) void loadVersions();
  });
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <header
    class="h-9 flex items-center justify-between px-3 border-b border-border bg-surface-2 text-xs gap-2"
  >
    <div class="flex items-baseline gap-2 min-w-0">
      <span class="font-mono uppercase tracking-wider text-text">PAQUETES</span>
      {#if versions.length > 0}
        <select
          class="bg-bg border border-border text-text text-xs px-1 py-0.5 font-mono focus:outline-none focus:border-accent"
          value={selVersion}
          onchange={(e) => selectVersion(Number((e.target as HTMLSelectElement).value))}
          title="Seleccionar versión"
        >
          {#each versions as v (v.id)}
            <option value={v.version}>
              v{v.version} · {v.status} · {v.analysis_version
                ? `análisis v${v.analysis_version}`
                : ''} · {v.requirement_count} reqs
            </option>
          {/each}
        </select>
      {/if}
      {#if active}
        <span
          class="px-1.5 py-0.5 border {STATUS_CLASS[active.status] ??
            'text-text-dim border-border bg-surface-2'} font-mono uppercase text-[10px]"
        >
          {active.status}
        </span>
      {/if}
    </div>
    <div class="flex items-center gap-2">
      {#if active}
        <a
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          href={masterExportUrl(projectId ?? 0, active.version)}
          download
          title="Descargar maestro de ensamblaje (.md)">↓ maestro</a
        >
      {/if}
      {#if onClose}
        <button
          type="button"
          class="px-2 py-0.5 text-text-dim hover:text-text font-mono"
          onclick={onClose}
          title="Cerrar visor de paquetes">×</button
        >
      {/if}
    </div>
  </header>

  {#if active}
    <nav class="flex items-stretch border-b border-border bg-bg text-xs overflow-x-auto">
      <button
        type="button"
        class="px-3 py-1 font-mono uppercase tracking-wide border-b-2 whitespace-nowrap {selPackage === 'master'
          ? 'text-text border-b-accent bg-surface'
          : 'text-text-dim border-b-transparent hover:text-text'}"
        onclick={() => (selPackage = 'master')}
      >
        Maestro
      </button>
      {#each active.packages as p (p.id)}
        <button
          type="button"
          class="px-3 py-1 font-mono uppercase tracking-wide border-b-2 whitespace-nowrap {selPackage === p.code
            ? 'text-text border-b-accent bg-surface'
            : 'text-text-dim border-b-transparent hover:text-text'}"
          onclick={() => (selPackage = p.code)}
          title="{p.sub_project_name} · {p.counts?.tasks ?? 0} tareas"
        >
          {p.code}
          <span class="text-text-faint">{p.counts?.tasks ?? 0}t</span>
        </button>
      {/each}
    </nav>
  {/if}

  <div class="flex-1 min-h-0 overflow-y-auto">
    {#if projectId === null}
      <div class="h-full flex items-center justify-center text-text-dim text-sm font-mono">
        Selecciona un proyecto
      </div>
    {:else if loading && !active}
      <div class="h-full flex items-center justify-center text-text-dim text-sm font-mono">
        Cargando paquetes…
      </div>
    {:else if error}
      <div class="m-3 p-3 border border-danger/40 bg-danger/5 text-danger text-xs font-mono">
        ! {error}
      </div>
    {:else if !active}
      <div class="h-full flex flex-col items-center justify-center gap-2 text-center px-6">
        <span class="text-text-dim text-sm font-mono">
          Aún no hay paquetes de trabajo.</span
        >
        <span class="text-text-faint text-xs font-mono">
          Ejecuta <span class="text-accent">/paquetes</span> en el chat para
          generar los entregables desde el último análisis comprometido.
        </span>
      </div>
    {:else}
      <div class="p-4 max-w-4xl mx-auto">
        {#if selPackage !== 'master'}
          {@const wp = active.packages.find((p) => p.code === selPackage)}
          {#if wp}
            <div class="flex items-center gap-3 mb-3">
              <a
                class="px-2 py-0.5 border border-border text-text-dim hover:text-accent text-xs font-mono"
                href={packageExportUrl(projectId ?? 0, active.version, wp.code)}
                download
              >
                ↓ {wp.code}.md
              </a>
              <span class="text-[10px] font-mono text-text-faint">
                {wp.counts?.entities ?? 0} entidades ·
                {wp.counts?.requirements ?? 0} reqs ·
                {wp.counts?.tasks ?? 0} tareas
              </span>
            </div>
          {/if}
        {/if}
        <article class="md-body">
          {@html html}
        </article>
      </div>
    {/if}
  </div>

  {#if active}
    <footer class="border-t border-border bg-surface-2 px-3 py-2 text-[10px] font-mono text-text-dim">
      <details>
        <summary class="cursor-pointer">
          Coherencia ({gates.filter((g) => g.status === 'pass').length}/{gates.length} gates ok
          {#if critique.length}· {critique.length} hallazgos de crítica{/if})
        </summary>
        <ul class="mt-1 space-y-0.5">
          {#each gates as g (g.gate)}
            <li>
              {g.status === 'pass' ? '✅' : g.status === 'warn' ? '⚠️' : '❌'}
              <span class:text-warning={g.status === 'warn'} class:text-danger={g.status === 'fail'}>
                {g.gate}</span
              >{g.blocking ? ' (bloqueante)' : ''}{#each g.details as d (d)}<br /><span
                  class="text-text-faint pl-4">{d}</span
                >{/each}
            </li>
          {/each}
        </ul>
      </details>
    </footer>
  {/if}
</section>
