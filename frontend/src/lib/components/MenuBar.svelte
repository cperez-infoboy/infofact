<script lang="ts">
  // Barra superior: selector de project + sesión, info de usuario, logout.
  // Runes OK porque es .svelte.
  import { goto } from '$app/navigation';
  import {
    projects,
    currentProject,
    sessions,
    currentSession,
    loadProjects,
    selectProject,
    selectSession,
    createProject,
    createSession
  } from '$lib/stores/project';
  import { authStore } from '$lib/stores/auth';
  import { logout as logoutApi } from '$lib/api/auth';
  import { setMessages } from '$lib/stores/chat';

  let { } = $props();

  let newProjectName = $state('');
  let creating = $state(false);

  // Refresca listas al montar.
  $effect(() => {
    loadProjects();
  });

  async function handleProjectChange(e: Event) {
    const value = (e.target as HTMLSelectElement).value;
    if (!value) return;
    await selectProject(Number(value));
    setMessages([]); // limpiar chat al cambiar proyecto
  }

  async function handleSessionChange(e: Event) {
    const value = (e.target as HTMLSelectElement).value;
    if (!value) return;
    await selectSession(Number(value));
    // El historial lo carga el chat panel vía $effect al detectar cambio.
  }

  async function handleCreateProject() {
    const name = newProjectName.trim();
    if (!name) return;
    creating = true;
    try {
      await createProject(name);
      newProjectName = '';
      setMessages([]);
    } catch {
      // El store ya capturó el error; el toast va en otra iter.
    } finally {
      creating = false;
    }
  }

  async function handleNewSession() {
    await createSession();
    setMessages([]);
  }

  async function handleLogout() {
    try {
      await logoutApi();
    } catch {
      // Ignorar error de red.
    }
    authStore.set(null);
    goto('/login');
  }
</script>

<header
  class="h-11 shrink-0 flex items-center gap-3 px-3 bg-surface text-text border-b border-border"
>
  <div class="font-semibold tracking-tight select-none text-text">
    InfoFact
  </div>

  <div class="flex items-center gap-2">
    <label class="sr-only" for="project-select">Proyecto</label>
    <select
      id="project-select"
      class="bg-bg text-text text-sm rounded-md px-2 py-1 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
      value={$currentProject?.id ?? ''}
      onchange={handleProjectChange}
      disabled={$projects.length === 0}
    >
      {#if $projects.length === 0}
        <option value="">sin proyectos</option>
      {:else}
        {#each $projects as p (p.id)}
          <option value={p.id}>{p.name}</option>
        {/each}
      {/if}
    </select>

    {#if $currentProject}
      <label class="sr-only" for="session-select">Sesión</label>
      <select
        id="session-select"
        class="bg-bg text-text text-sm rounded-md px-2 py-1 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60"
        value={$currentSession?.id ?? ''}
        onchange={handleSessionChange}
      >
        {#if $sessions.length === 0}
          <option value="">sin sesiones</option>
        {:else}
          {#each $sessions as s, i (s.id)}
            <option value={s.id}>sesión {i + 1}</option>
          {/each}
        {/if}
      </select>
      <button
        type="button"
        class="text-xs px-2 py-1 rounded-md bg-surface-2 border border-border text-text-dim hover:bg-surface-3 hover:text-text transition-colors"
        onclick={handleNewSession}>+ sesión</button
      >
    {/if}
  </div>

  <div class="ml-auto flex items-center gap-2">
    <div class="flex items-center gap-1">
      <input
        type="text"
        bind:value={newProjectName}
        placeholder="nuevo proyecto…"
        class="bg-bg text-text text-sm rounded-md px-2 py-1 border border-border focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/60 w-44"
        onkeydown={(e) => {
          if (e.key === 'Enter' && !creating) handleCreateProject();
        }}
      />
      <button
        type="button"
        class="text-xs px-2 py-1 rounded-md bg-accent text-bg hover:bg-accent-hover disabled:opacity-50 transition-colors"
        onclick={handleCreateProject}
        disabled={creating || !newProjectName.trim()}>crear</button
      >
    </div>

    {#if $authStore}
      <span class="text-xs text-text-dim font-mono">
        {$authStore.email}<span class="text-text-faint">@</span><span class="text-text">{$authStore.profile}</span>
      </span>
    {/if}
    <button
      type="button"
      class="text-xs px-2 py-1 rounded-md bg-surface-2 border border-border text-text-dim hover:bg-surface-3 hover:text-text transition-colors"
      onclick={handleLogout}>↪ salir</button
    >
  </div>
</header>
