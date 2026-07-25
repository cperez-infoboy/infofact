// Store de proyectos y sesiones. Archivo .ts PLANO → writable de svelte/store.
// Las runes ($state/$derived/$effect) están PROHIBIDAS aquí.
import { writable, derived } from 'svelte/store';
import * as projectsApi from '$lib/api/projects';
import type { ProjectOut, ProjectDetail, SessionOut, SessionDetail } from '$lib/api/projects';

export const projects = writable<ProjectOut[]>([]);
export const currentProject = writable<ProjectDetail | null>(null);
export const currentProjectId = derived(currentProject, ($p) => $p?.id ?? null);
export const sessions = writable<SessionOut[]>([]);
export const currentSession = writable<SessionDetail | null>(null);
export const projectError = writable<string | null>(null);
export const projectLoading = writable<boolean>(false);

/** Carga la lista de proyectos del usuario. */
export async function loadProjects(): Promise<void> {
  projectLoading.set(true);
  projectError.set(null);
  try {
    projects.set(await projectsApi.listProjects());
  } catch (e) {
    projectError.set((e as Error).message);
  } finally {
    projectLoading.set(false);
  }
}

/** Selecciona un proyecto: carga detalle + sesiones. */
export async function selectProject(id: number): Promise<void> {
  projectLoading.set(true);
  projectError.set(null);
  try {
    const detail = await projectsApi.getProject(id);
    currentProject.set(detail);
    sessions.set(detail.sessions);
    // Reset sesión activa al cambiar de proyecto.
    currentSession.set(null);
  } catch (e) {
    projectError.set((e as Error).message);
  } finally {
    projectLoading.set(false);
  }
}

/** Crea un proyecto nuevo y lo deja seleccionado. */
export async function createProject(name: string, phase: string = 'requirements'): Promise<ProjectOut> {
  const created = await projectsApi.createProject({ name, phase });
  await loadProjects();
  await selectProject(created.id);
  // Si el backend creó sesión inicial, la cargamos como activa.
  if (created.initial_session_id) {
    await selectSession(created.initial_session_id);
  }
  return created;
}

/** Crea una sesión nueva bajo el proyecto actual. */
export async function createSession(title?: string): Promise<SessionOut | null> {
  let projId: number | null = null;
  currentProject.subscribe((v) => (projId = v?.id ?? null))();
  if (projId === null) return null;
  const created = await projectsApi.createSession(projId as number, title ? { title } : {});
  sessions.update((list) => [...list, created]);
  await selectSession(created.id);
  return created;
}

/** Selecciona una sesión: carga detalle con mensajes. */
export async function selectSession(id: number): Promise<void> {
  try {
    const detail = await projectsApi.getSessionDetail(id);
    currentSession.set(detail);
  } catch (e) {
    projectError.set((e as Error).message);
  }
}
