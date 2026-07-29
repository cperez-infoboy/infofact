<script lang="ts">
  // Tab "Requerimientos": lista/filtro + detalle editable.
  // La lista es un panel redimensionable (width persistente en localStorage,
  // igual que el explorer y el chat). La fila clipa overflow para que el
  // detalle no se derrame sobre el chat cuando el viewer se estrecha.
  import {
    setProject,
    loadRequirements,
    items,
    selectedId,
    selectRequirement,
    filters,
    reqLoading,
    reqError
  } from '$lib/stores/requirements';
  import RequirementDetail from '$lib/components/RequirementDetail.svelte';
  import ResizeHandle from '$lib/components/ResizeHandle.svelte';

  let { projectId }: { projectId: number | null } = $props();

  let q = $state('');

  // Ancho persistente de la lista de requerimientos.
  const LIST_KEY = 'infofact.layout.reqsListWidth';
  const DEFAULT_LIST = 224;
  const MIN_LIST = 160;
  const MAX_LIST = 480;

  function readListWidth(): number {
    if (typeof localStorage === 'undefined') return DEFAULT_LIST;
    const raw = localStorage.getItem(LIST_KEY);
    if (raw === null) return DEFAULT_LIST;
    const v = Number(raw);
    if (!Number.isFinite(v)) return DEFAULT_LIST;
    return Math.min(MAX_LIST, Math.max(MIN_LIST, v));
  }

  let listWidth = $state(readListWidth());

  function onListResize(delta: number) {
    listWidth = Math.min(MAX_LIST, Math.max(MIN_LIST, listWidth + delta));
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(LIST_KEY, String(listWidth));
    }
  }

  const STATUSES = [
    'draft', 'unverified', 'validated', 'approved', 'rejected', 'merged', 'superseded'
  ];
  const TYPES = [
    'functional', 'performance', 'security', 'usability', 'reliability',
    'maintainability', 'compliance', 'constraint', 'process', 'data'
  ];
  const PRIOS = ['must', 'should', 'could', 'wont'];

  let filtered = $derived(
    q.trim()
      ? $items.filter((it) =>
          (it.code + ' ' + it.statement).toLowerCase().includes(q.trim().toLowerCase())
        )
      : $items
  );

  async function reload(): Promise<void> {
    setProject(projectId);
    await loadRequirements();
  }

  $effect(() => {
    if (projectId !== null) reload();
  });

  async function setFilter(
    key: 'status' | 'type' | 'priority',
    val: string
  ): Promise<void> {
    filters.update((f) => ({ ...f, [key]: val || undefined }));
    await loadRequirements();
  }
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0 overflow-hidden">
  <header
    class="h-9 flex items-center gap-2 px-3 border-b border-border bg-surface-2 text-xs"
  >
    <span class="font-mono uppercase tracking-wider text-text">Requerimientos</span>
    {#if $items.length}
      <span class="font-mono text-text-faint">({$items.length})</span>
    {/if}
  </header>

  <!-- min-w-0 en la fila + overflow-hidden: el detalle puede estrecharse sin
       derramarse sobre el panel del chat. -->
  <div class="flex-1 min-h-0 flex min-w-0">
    <!-- Lista + filtros (panel redimensionable) -->
    <div
      class="shrink-0 border-r border-border flex flex-col min-h-0"
      style="width: {listWidth}px"
    >
      <div class="p-2 space-y-1 border-b border-border">
        <input
          type="text"
          placeholder="buscar…"
          bind:value={q}
          class="w-full bg-bg border border-border rounded-sm px-2 py-1 text-xs font-mono focus:outline-none focus:border-accent"
        />
        <div class="grid grid-cols-3 gap-1">
          <select
            value={$filters.status ?? ''}
            onchange={(e) => setFilter('status', e.currentTarget.value)}
            class="bg-bg border border-border rounded-sm px-1 py-0.5 text-[10px] font-mono"
            title="estado"
          >
            <option value="">st</option>
            {#each STATUSES as s (s)}<option value={s}>{s.slice(0, 4)}</option>{/each}
          </select>
          <select
            value={$filters.type ?? ''}
            onchange={(e) => setFilter('type', e.currentTarget.value)}
            class="bg-bg border border-border rounded-sm px-1 py-0.5 text-[10px] font-mono"
            title="tipo"
          >
            <option value="">ty</option>
            {#each TYPES as t (t)}<option value={t}>{t.slice(0, 4)}</option>{/each}
          </select>
          <select
            value={$filters.priority ?? ''}
            onchange={(e) => setFilter('priority', e.currentTarget.value)}
            class="bg-bg border border-border rounded-sm px-1 py-0.5 text-[10px] font-mono"
            title="prioridad"
          >
            <option value="">pr</option>
            {#each PRIOS as p (p)}<option value={p}>{p.slice(0, 4)}</option>{/each}
          </select>
        </div>
      </div>
      <div class="flex-1 min-h-0 overflow-y-auto">
        {#if $reqLoading && !$items.length}
          <div class="p-2 text-text-dim font-mono text-xs">Cargando…</div>
        {:else if $reqError}
          <div class="p-2 text-danger font-mono text-xs">! {$reqError}</div>
        {:else}
          {#each filtered as it (it.id)}
            <button
              type="button"
              class="w-full text-left py-1 border-b border-border/50 hover:bg-surface-2 {it.derived
                ? 'pl-5'
                : 'px-2'} {$selectedId === it.id
                ? 'bg-surface-3 border-l-2 border-l-accent'
                : ''}"
              onclick={() => selectRequirement(it.id)}
            >
              <div
                class="font-mono text-[11px] {it.derived ? 'text-text-dim' : 'text-text'}"
              >
                {it.derived ? '↳ ' : ''}{it.code}<span
                  class="text-text-faint text-[9px]"> {it.priority ?? ''}</span
                >
              </div>
              <div class="text-[11px] text-text-dim truncate">{it.statement}</div>
            </button>
          {/each}
        {/if}
      </div>
    </div>

    <ResizeHandle onResize={onListResize} />

    <!-- Detalle editable -->
    <div class="flex-1 min-w-0">
      <RequirementDetail />
    </div>
  </div>
</section>
