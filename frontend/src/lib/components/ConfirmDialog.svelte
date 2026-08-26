<script lang="ts">
  // Diálogo modal de confirmación genérico. Escape y click en el overlay
  // cancelan; el foco arranca en el botón de confirmación.
  let {
    title,
    message,
    confirmLabel = 'Confirmar',
    cancelLabel = 'Cancelar',
    danger = false,
    onconfirm,
    oncancel
  }: {
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    danger?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
  } = $props();
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape') oncancel();
  }}
/>

<!-- Backdrop: click cierra (Escape es el equivalente de teclado, manejado
     en svelte:window). -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
  onclick={() => oncancel()}
>
  <div
    class="w-full max-w-sm rounded-md border border-border bg-surface-2 p-4 shadow-lg"
    role="alertdialog"
    aria-modal="true"
    aria-label={title}
    tabindex="-1"
    onclick={(e) => e.stopPropagation()}
  >
    <h2 class="text-sm font-semibold text-text mb-2">{title}</h2>
    <p class="text-xs text-text-dim font-mono whitespace-pre-wrap mb-4">{message}</p>
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="px-3 py-1 text-xs rounded-sm border border-border bg-surface-3
               text-text-dim hover:text-text transition-colors"
        onclick={() => oncancel()}
      >
        {cancelLabel}
      </button>
      <button
        type="button"
        class="px-3 py-1 text-xs rounded-sm border transition-colors
               {danger
          ? 'border-danger bg-danger/10 text-danger hover:bg-danger/20'
          : 'border-border-strong bg-surface-3 text-text hover:bg-border'}"
        onclick={() => onconfirm()}
      >
        {confirmLabel}
      </button>
    </div>
  </div>
</div>
