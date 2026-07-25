<script lang="ts">
  // Registro: email + password + profile (slug ^[a-z0-9_-]{2,48}$).
  // 409 → "Email o perfil ya en uso". 400 → "Perfil inválido".
  import { goto } from '$app/navigation';
  import { register } from '$lib/api/auth';
  import { ApiError } from '$lib/api/client';
  import { authStore } from '$lib/stores/auth';

  let email = $state('');
  let password = $state('');
  let profile = $state('');
  let error = $state<string | null>(null);
  let submitting = $state(false);

  // Svelte interpreta `{...}` en atributos como expresiones JS; escapamos el
  // cuantificador del regex pasándolo como variable.
  // Guión al PRINCIPIO del character class: en flag /v (Chrome 112+) el orden
  // `_-` se interpreta como rango inválido. `[-a-z0-9_]` es portable en /v y
  // en Python `re`.
  const profilePattern = '^[-a-z0-9_]{2,48}$';

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    submitting = true;
    try {
      const user = await register(email, password, profile);
      authStore.set(user);
      await goto('/');
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 409) error = 'Email o perfil ya en uso';
        else if (e.status === 400) error = 'Perfil inválido';
        else error = e.code;
      } else {
        error = 'Error inesperado';
      }
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head><title>InfoFact — Registrarse</title></svelte:head>

<main class="flex-1 flex items-center justify-center p-6">
  <form
    onsubmit={handleSubmit}
    class="w-full max-w-sm bg-surface border border-border p-8 flex flex-col gap-4 rounded-xl"
  >
    <h1 class="text-xl text-center text-text font-medium tracking-tight">
      Crear cuenta
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
        autocomplete="new-password"
        minlength="8"
        class="bg-bg border border-border rounded-md px-3 py-2 text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
      />
    </label>

    <label class="flex flex-col gap-1 text-sm">
      <span class="text-text-dim">Perfil (slug)</span>
      <input
        type="text"
        bind:value={profile}
        required
        pattern={profilePattern}
        title="Solo minúsculas, números, guion o guion bajo. 2-48 caracteres."
        placeholder="ej: mi_perfil-01"
        class="bg-bg border border-border rounded-md px-3 py-2 text-text focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
      />
      <span class="text-xs text-text-dim">
        Solo minúsculas, números, guion o guion bajo. 2-48 caracteres.
      </span>
    </label>

    <button
      type="submit"
      disabled={submitting}
      class="mt-2 px-4 py-2 bg-accent text-bg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition rounded-md"
    >
      {submitting ? 'Creando…' : 'Crear cuenta'}
    </button>

    <p class="text-center text-sm text-text-dim">
      ¿Ya tienes cuenta?
      <a href="/login" class="text-text hover:underline">Iniciar sesión</a>
    </p>
  </form>
</main>
