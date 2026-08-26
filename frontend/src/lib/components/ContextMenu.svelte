<script lang="ts">
  // Menú contextual flotante genérico. Se posiciona en (x, y) con clamp
  // contra los bordes del viewport, y se cierra por click fuera, Escape,
  // resize o scroll (incluye scrolls de contenedores internos, via capture).
  export interface MenuItem {
    label: string;
    danger?: boolean;
    disabled?: boolean;
    action?: () => void;
    separator?: boolean;
  }

  let {
    items,
    x,
    y,
    onclose
  }: { items: MenuItem[]; x: number; y: number; onclose: () => void } = $props();

  let menuEl = $state<HTMLElement | null>(null);
  // svelte-ignore state_referenced_locally — semilla inicial: pos luego se
  // recalcula en el $effect de clamp.
  let pos = $state({ x, y });

  // Clamp post-render: mide el tamaño real y ajusta para no desbordar.
  $effect(() => {
    const el = menuEl;
    if (!el) return;
    const r = el.getBoundingClientRect();
    pos = {
      x: Math.max(8, Math.min(x, window.innerWidth - r.width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - r.height - 8))
    };
    // Close on any scroll (los scrolls de elementos no burbujean; capture sí).
    const onScroll = () => onclose();
    window.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => window.removeEventListener('scroll', onScroll, { capture: true });
  });

  function run(item: MenuItem) {
    if (item.disabled) return;
    item.action?.();
    onclose();
  }
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape') onclose();
  }}
  onpointerdown={(e) => {
    if (menuEl && !menuEl.contains(e.target as Node)) onclose();
  }}
  onresize={() => onclose()}
/>

<div
  bind:this={menuEl}
  class="fixed z-50 min-w-[170px] py-1 rounded-md border border-border
         bg-surface-2 shadow-lg shadow-black/50 select-none"
  style="left: {pos.x}px; top: {pos.y}px"
  role="menu"
  aria-label="Menú del explorador"
>
  {#each items as item, i (i)}
    {#if item.separator}
      <div class="my-1 border-t border-border"></div>
    {:else}
      <button
        type="button"
        role="menuitem"
        class="w-full text-left px-3 py-1 text-xs font-mono transition-colors
               disabled:opacity-40 disabled:cursor-default
               {item.danger
        ? 'text-danger hover:bg-danger/10'
        : 'text-text-dim hover:bg-surface-3 hover:text-text'}"
        disabled={item.disabled}
        onclick={() => run(item)}
      >
        {item.label}
      </button>
    {/if}
  {/each}
</div>
