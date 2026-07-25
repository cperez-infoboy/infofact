<script lang="ts">
  // Handle vertical arrastrable. Emite delta via onResize(deltaPx).
  // La persistencia en localStorage la hace el parent (conoce el ancho total).
  //
  // TRAMPA: capturamos mousemove/mouseup en window para que el arrastre
  // continúe fuera del handle. Removemos listeners en mouseup.
  let {
    onResize
  }: {
    onResize: (deltaPx: number) => void;
  } = $props();

  let startX = 0;

  function onMouseDown(e: MouseEvent) {
    e.preventDefault();
    startX = e.clientX;
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    document.body.classList.add('select-none', 'cursor-col-resize');
  }

  function onMouseMove(e: MouseEvent) {
    const delta = e.clientX - startX;
    startX = e.clientX;
    onResize(delta);
  }

  function onMouseUp() {
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('mouseup', onMouseUp);
    document.body.classList.remove('select-none', 'cursor-col-resize');
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') onResize(-16);
    else if (e.key === 'ArrowRight') onResize(16);
  }
</script>

<button
  type="button"
  aria-label="Redimensionar panel"
  class="w-1 shrink-0 cursor-col-resize bg-border hover:bg-border-strong active:bg-text-faint transition-colors border-0 p-0"
  onmousedown={onMouseDown}
  onkeydown={handleKeydown}
></button>
