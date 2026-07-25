<script lang="ts">
  // Layout raíz: carga el usuario actual antes de renderizar rutas.
  // Svelte 5 runes — OK porque es .svelte.
  import { onMount } from 'svelte';
  import { loadCurrentUser } from '$lib/stores/auth';
  import '../app.css';

  let { children } = $props();

  let loading = $state(true);

  onMount(async () => {
    await loadCurrentUser();
    loading = false;
  });
</script>

<div class="min-h-screen flex flex-col bg-bg text-text">
  {#if loading}
    <div class="flex-1 flex items-center justify-center">
      <div
        class="text-text-dim text-sm"
        aria-label="Cargando"
      >
        Cargando…
      </div>
    </div>
  {:else}
    {@render children()}
  {/if}
</div>
