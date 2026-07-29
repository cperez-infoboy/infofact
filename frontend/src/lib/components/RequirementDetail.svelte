<script lang="ts">
  // Wrapper del editor: renderiza RequirementForm con {#key selectedId} para
  // que el buffer de edición se reinicialice al cambiar de requerimiento.
  import { selectedDetail, selectedId } from '$lib/stores/requirements';
  import RequirementForm from '$lib/components/RequirementForm.svelte';
</script>

{#if $selectedDetail && $selectedId !== null}
  {#key $selectedId}
    <div class="h-full">
      <RequirementForm detail={$selectedDetail} />
    </div>
  {/key}
{:else}
  <div class="h-full flex items-center justify-center text-text-dim text-sm font-mono">
    Selecciona un requerimiento
  </div>
{/if}
