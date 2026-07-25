<script lang="ts">
  // Login: email + password. 401 → "Credenciales inválidas".
  import { goto } from '$app/navigation';
  import { login } from '$lib/api/auth';
  import { ApiError } from '$lib/api/client';
  import { authStore } from '$lib/stores/auth';

  let email = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let submitting = $state(false);

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    submitting = true;
    try {
      const user = await login(email, password);
      authStore.set(user);
      await goto('/');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        error = 'Credenciales inválidas';
      } else if (e instanceof ApiError) {
        error = e.code;
      } else {
        error = 'Error inesperado';
      }
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head><title>InfoFact — Iniciar sesión</title></svelte:head>

<main class="flex-1 flex items-center justify-center p-6">
  <form
    onsubmit={handleSubmit}
    class="w-full max-w-sm bg-surface border border-border p-8 flex flex-col gap-4 rounded-xl"
  >
    <h1 class="text-xl text-center text-text font-medium tracking-tight">
      Iniciar sesión
    </h1>

    {#if error}
      <div
        class="border border-danger/50 bg-danger/5 text-danger px-3 py-2 text-sm rounded-md"
        role="alert"
      >
        ! {error}
      </div>
    {/if}

    <label class="flex flex-col gap-1 text-sm">
      <span class="text-text-dim">Email</span>
      <input
        type="email"
        bind:value={email}
        required
        autocomplete="email"
        class="bg-bg border border-border rounded-md px-3 py-2 text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
      />
    </label>

    <label class="flex flex-col gap-1 text-sm">
      <span class="text-text-dim">Contraseña</span>
      <input
        type="password"
        bind:value={password}
        required
        autocomplete="current-password"
        class="bg-bg border border-border rounded-md px-3 py-2 text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
      />
    </label>

    <button
      type="submit"
      disabled={submitting}
      class="mt-2 px-4 py-2 bg-accent text-bg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition rounded-md"
    >
      {submitting ? 'Ingresando…' : 'Ingresar'}
    </button>

    <p class="text-center text-sm text-text-dim">
      ¿No tienes cuenta?
      <a href="/register" class="text-text hover:underline">Registrarse</a>
    </p>
  </form>
</main>
