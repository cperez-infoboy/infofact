<script lang="ts">
  // Tab "Agrupamiento": selector de plan + grupos con decisión.
  // Acciones: aceptar (✓) / rechazar (✕) por grupo; "aplicar" fusiona los
  // accept. Se carga a sí mismo al montarse (setProject + loadPlans).
  import {
    setProject,
    plans,
    activePlanId,
    activePlan,
    planLoading,
    planError,
    selectPlan,
    setDecision,
    applyPlan,
    loadPlans
  } from '$lib/stores/requirements';
  import type { ApplyResult } from '$lib/api/requirements';

  let { projectId }: { projectId: number | null } = $props();

  let applying = $state(false);
  let applyResult = $state<string | null>(null);

  let acceptedCount = $derived(
    $activePlan ? $activePlan.groups.filter((g) => g.decision === 'accept').length : 0
  );

  async function reload(): Promise<void> {
    setProject(projectId);
    await loadPlans();
  }

  $effect(() => {
    if (projectId !== null) reload();
  });

  function statusClass(s: string): string {
    if (s === 'applied') return 'text-accent';
    if (s === 'partially-applied') return 'text-warning';
    return 'text-text-dim';
  }
  function decisionClass(d: string): string {
    if (d === 'accept') return 'text-accent border-accent';
    if (d === 'reject') return 'text-danger border-danger/50';
    return 'text-text-dim border-border';
  }

  async function apply(): Promise<void> {
    applying = true;
    applyResult = null;
    try {
      const res: ApplyResult | null = await applyPlan();
      if (res) {
        applyResult =
          `aplicado=${res.applied} ya=${res.already_applied} inválido=${res.invalid} (${res.status})`;
      }
    } catch (e) {
      applyResult = '! ' + (e as Error).message;
    } finally {
      applying = false;
    }
  }
</script>

<section class="h-full flex flex-col bg-surface text-text min-w-0">
  <header
    class="h-9 flex items-center gap-2 px-3 border-b border-border bg-surface-2 text-xs"
  >
    <span class="font-mono uppercase tracking-wider text-text">Agrupamiento</span>
    <div class="flex-1"></div>
    <select
      value={$activePlanId ?? ''}
      onchange={(e) =>
        selectPlan(e.currentTarget.value ? Number(e.currentTarget.value) : null)}
      class="bg-bg border border-border rounded-sm px-1 py-0.5 text-text text-[11px] font-mono max-w-[130px]"
    >
      <option value="">—</option>
      {#each $plans as p (p.id)}<option value={p.id}>#{p.id} · {p.status}</option>{/each}
    </select>
    <button
      type="button"
      class="px-2 py-0.5 text-[10px] font-mono uppercase rounded-sm border border-border bg-surface-3 text-text hover:bg-accent hover:text-bg disabled:opacity-40 transition-colors"
      onclick={apply}
      disabled={$activePlan === null || $activePlan.status === 'applied' || applying}
      title="Fusionar los grupos aceptados (idempotente)"
    >
      {applying ? '…' : 'aplicar'}
    </button>
  </header>

  <div class="flex-1 min-h-0 overflow-y-auto p-2 space-y-2 text-xs">
    {#if $planLoading && !$activePlan}
      <div class="text-text-dim font-mono p-2">Cargando…</div>
    {:else if $planError}
      <div class="p-2 border border-danger/40 bg-danger/5 text-danger font-mono">
        ! {$planError}
      </div>
    {:else if !$activePlan}
      <div class="text-text-dim font-mono p-2 leading-relaxed">
        Sin planes todavía.<br />
        <button type="button" class="underline hover:text-text" onclick={reload}
          >refrescar</button
        ><br />
        <span class="text-text-faint"
          >Pídele al agente «/agrupar» para detectar duplicados.</span
        >
      </div>
    {:else}
      {#if applyResult}
        <div
          class="p-2 border border-accent/40 bg-accent/5 text-accent font-mono break-words"
        >
          {applyResult}
        </div>
      {/if}
      <div class="font-mono text-[10px] uppercase text-text-dim px-1">
        {$activePlan.groups.length} grupos · estado
        <span class={statusClass($activePlan.status)}>{$activePlan.status}</span>
        <span class="text-text-faint">· {acceptedCount} aceptados</span>
      </div>
      {#each $activePlan.groups as g (g.id)}
        <div class="border border-border rounded-sm p-2 bg-bg">
          <div class="flex items-center gap-1 mb-1 flex-wrap">
            <span class="font-mono text-text">★ {g.keeper.code ?? '—'}</span>
            <span class="font-mono text-text-faint text-[10px]"
              >← {g.members.map((m) => m.code).join(', ')}</span
            >
          </div>
          <div class="font-mono text-[10px] text-text-dim mb-1.5"
            >{g.reason} · {Math.round((g.confidence ?? 0) * 100)}%</div
          >
          <div class="flex items-center gap-1">
            <span
              class="font-mono text-[9px] uppercase px-1 border {decisionClass(
                g.decision
              )} rounded-sm"
              >{g.decision}</span
            >
            <div class="flex-1"></div>
            <button
              type="button"
              class="px-1.5 py-0.5 text-[10px] font-mono rounded-sm border border-border hover:border-accent hover:text-accent {g.decision ===
              'accept'
                ? 'border-accent text-accent'
                : ''}"
              onclick={() => setDecision(g.id, 'accept')}
              title="Aceptar fusión">✓</button
            >
            <button
              type="button"
              class="px-1.5 py-0.5 text-[10px] font-mono rounded-sm border border-border hover:border-danger hover:text-danger {g.decision ===
              'reject'
                ? 'border-danger text-danger'
                : ''}"
              onclick={() => setDecision(g.id, 'reject')}
              title="Rechazar fusión">✕</button
            >
          </div>
        </div>
      {/each}
    {/if}
  </div>
</section>
