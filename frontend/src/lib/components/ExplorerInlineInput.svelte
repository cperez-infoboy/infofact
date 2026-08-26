<script lang="ts">
  // Input inline del explorador: creación de archivo/carpeta y renombrado
  // (estilo VSCode). Enter confirma, Escape y blur cancelan. En modo rename
  // selecciona el nombre sin la extensión.
  let {
    initial = '',
    selectBaseOnly = false,
    onconfirm,
    oncancel
  }: {
    initial?: string;
    selectBaseOnly?: boolean;
    onconfirm: (value: string) => void;
    oncancel: () => void;
  } = $props();

  let inputEl = $state<HTMLInputElement | null>(null);
  // svelte-ignore state_referenced_locally — semilla inicial (prop inmutable).
  let value = $state(initial);
  // Evita que el blur posterior a una confirmación dispare también cancel.
  let done = false;

  $effect(() => {
    const el = inputEl;
    if (!el) return;
    el.focus();
    if (selectBaseOnly) {
      // svelte-ignore state_referenced_locally — initial es prop inmutable.
      const dot = initial.lastIndexOf('.');
      el.setSelectionRange(0, dot > 0 ? dot : initial.length);
    } else {
      el.select();
    }
  });

  function confirm() {
    done = true;
    const v = value.trim();
    if (!v) {
      oncancel();
      return;
    }
    onconfirm(v);
  }

  function cancel() {
    done = true;
    oncancel();
  }
</script>

<input
  bind:this={inputEl}
  bind:value
  type="text"
  class="w-full min-w-0 bg-bg border border-border-strong rounded-sm px-1 py-0.5
         text-sm font-mono text-text outline-none focus:border-accent"
  placeholder="nombre…"
  spellcheck="false"
  onkeydown={(e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      confirm();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      cancel();
    }
  }}
  onblur={() => {
    if (!done) cancel();
  }}
/>
