<script lang="ts">
  // Formulario editable de un requerimiento. El $state local se inicializa
  // desde la prop `detail`; el padre lo remonta con {#key selectedId} al
  // cambiar la selección, así el buffer se resincroniza sin $effect.
  import { saveRequirement } from '$lib/stores/requirements';
  import type {
    RequirementDetail,
    RequirementSourceEntry,
  } from '$lib/api/requirements';

  let { detail }: { detail: RequirementDetail } = $props();

  const TYPES = [
    'functional', 'performance', 'security', 'usability', 'reliability',
    'maintainability', 'compliance', 'constraint', 'process', 'data'
  ];
  const PRIOS = ['must', 'should', 'could', 'wont'];

  // svelte-ignore state_referenced_locally
  let statement = $state(detail.statement);
  // svelte-ignore state_referenced_locally
  let type = $state(detail.type ?? '');
  // svelte-ignore state_referenced_locally
  let priority = $state(detail.priority ?? '');
  let saving = $state(false);
  let localError = $state<string | null>(null);

  // Normaliza detail.source (dict | lista | null) a lista. Espeja
  // `_source_list` del backend: tras un merge hay varios spans unidos.
  function toSources(
    s: RequirementDetail['source'],
  ): RequirementSourceEntry[] {
    if (s == null) return [];
    return Array.isArray(s) ? s : [s];
  }

  let sources = $derived(toSources(detail.source));

  let dirty = $derived(
    statement !== detail.statement ||
      type !== (detail.type ?? '') ||
      priority !== (detail.priority ?? '')
  );

  async function save(): Promise<void> {
    saving = true;
    localError = null;
    try {
      const body: { statement?: string; type?: string; priority?: string } = {};
      if (statement !== detail.statement) body.statement = statement;
      if (type !== (detail.type ?? '')) body.type = type || undefined;
      if (priority !== (detail.priority ?? '')) body.priority = priority || undefined;
      await saveRequirement(detail.id, body);
    } catch (e) {
      localError = (e as Error).message;
    } finally {
      saving = false;
    }
  }

  function statusClass(s: string | null | undefined): string {
    switch (s) {
      case 'approved':
      case 'validated':
        return 'text-accent';
      case 'rejected':
        return 'text-danger';
      case 'unverified':
        return 'text-warning';
      case 'merged':
      case 'superseded':
        return 'text-text-faint line-through';
      default:
        return 'text-text-dim';
    }
  }
</script>

<div class="h-full flex flex-col min-w-0">
  <header
    class="h-9 flex items-center gap-2 px-3 border-b border-border bg-surface-2 text-xs"
  >
    <span class="font-mono uppercase tracking-wider text-text">{detail.code}</span>
    <span class="font-mono uppercase text-[10px] {statusClass(detail.status)}"
      >{detail.status}</span
    >
    {#if detail.merged_into}
      <span class="font-mono text-[10px] text-text-faint"
        >↺ fusionado en #{detail.merged_into}</span
      >
    {/if}
    {#if detail.derived && detail.parent_code}
      <span class="font-mono text-[10px] text-text-faint"
        >↳ derivado de {detail.parent_code}</span
      >
    {/if}
    <div class="flex-1"></div>
    <button
      type="button"
      class="px-2 py-0.5 text-[10px] font-mono uppercase rounded-sm border border-border bg-surface-3 text-text disabled:opacity-40 hover:bg-accent hover:text-bg transition-colors"
      onclick={save}
      disabled={!dirty || saving}
    >
      {saving ? '…' : 'guardar'}
    </button>
  </header>

  <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-3 space-y-3 text-sm">
    {#if localError}
      <div class="p-2 border border-danger/40 bg-danger/5 text-danger text-xs font-mono">
        ! {localError}
      </div>
    {/if}

    <label class="block">
      <span class="font-mono text-[10px] uppercase tracking-wider text-text-dim"
        >Enunciado</span
      >
      <textarea
        bind:value={statement}
        rows="6"
        class="mt-1 w-full bg-bg border border-border rounded-sm p-2 text-text font-mono text-xs resize-y focus:outline-none focus:border-accent"
      ></textarea>
    </label>

    <div class="grid grid-cols-2 gap-2">
      <label class="block">
        <span class="font-mono text-[10px] uppercase tracking-wider text-text-dim"
          >Tipo</span
        >
        <select
          bind:value={type}
          class="mt-1 w-full bg-bg border border-border rounded-sm p-1.5 text-text text-xs focus:outline-none focus:border-accent"
        >
          <option value="">—</option>
          {#each TYPES as t (t)}<option value={t}>{t}</option>{/each}
        </select>
      </label>
      <label class="block">
        <span class="font-mono text-[10px] uppercase tracking-wider text-text-dim"
          >Prioridad</span
        >
        <select
          bind:value={priority}
          class="mt-1 w-full bg-bg border border-border rounded-sm p-1.5 text-text text-xs focus:outline-none focus:border-accent"
        >
          <option value="">—</option>
          {#each PRIOS as p (p)}<option value={p}>{p}</option>{/each}
        </select>
      </label>
    </div>

    {#if sources.length}
      <section class="space-y-1.5">
        <span class="font-mono text-[10px] uppercase tracking-wider text-text-dim"
          >Origen del documento{sources.length > 1
            ? ` (${sources.length})`
            : ''}</span
        >
        {#each sources as src, i (i)}
          <figure
            class="border border-border bg-bg rounded-sm p-2 space-y-1"
          >
            <blockquote
              class="border-l-2 border-accent/50 pl-2 text-text text-xs leading-relaxed italic"
            >
              {src.quote ?? '(cita vacía)'}
            </blockquote>
            <figcaption
              class="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] font-mono text-text-faint"
            >
              {#if src.section}<span>§ {src.section}</span>{/if}
              {#if src.page != null}<span>p. {src.page}</span>{/if}
              {#if src.document_id}<span>{src.document_id}</span>{/if}
            </figcaption>
          </figure>
        {/each}
      </section>
    {:else}
      <p class="text-[10px] font-mono text-text-faint italic">
        Sin origen (entrada manual).
      </p>
    {/if}

    <dl
      class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs font-mono pt-2 border-t border-border"
    >
      <dt class="text-text-dim">Confianza</dt>
      <dd class="text-text">{detail.confidence ?? '—'}</dd>
      <dt class="text-text-dim">Span verif.</dt>
      <dd class={detail.span_verified ? 'text-accent' : 'text-warning'}
        >{detail.span_verified ? 'sí' : 'no'}</dd
      >
      <dt class="text-text-dim">Derivado</dt>
      <dd class="text-text">{detail.derived ? 'sí' : 'no'}</dd>
    </dl>
  </div>
</div>
