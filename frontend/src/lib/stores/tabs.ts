// Store de tabs del FileViewer. Archivo .ts PLANO → writable de svelte/store.
// LRU eviction: cuando excede MAX_TABS, se elimina el menos recientemente activo
// (no dirty sin confirmación).
import { get, writable } from 'svelte/store';
import { getFile, putFile } from '$lib/api/workspaces';
import { currentProjectId } from '$lib/stores/project';

export const MAX_TABS = 15;

export interface Tab {
  path: string;
  name: string;
  content: string;
  dirty: boolean;
  scrollPos: number;
  /** Último timestamp en que fue activado — para LRU. */
  lastActivated: number;
}

export const openTabs = writable<Tab[]>([]);
export const activeTabPath = writable<string | null>(null);
export const tabsError = writable<string | null>(null);

function basename(p: string): string {
  const parts = p.split('/');
  return parts[parts.length - 1] || p;
}

/** Abre un archivo. Si ya está abierto, solo lo activa (no recarga contenido
 *  salvo que esté vacío). Devuelve false si el fetch falla. */
export async function openTab(path: string): Promise<boolean> {
  // Si ya existe, solo lo activa.
  let existing: Tab | undefined;
  openTabs.subscribe((list) => {
    existing = list.find((t) => t.path === path);
  })();

  if (existing) {
    setActive(path);
    return true;
  }

  const projectId = get(currentProjectId);
  if (projectId === null) {
    tabsError.set('no_project_selected');
    return false;
  }
  tabsError.set(null);
  try {
    const file = await getFile(projectId, path);
    const tab: Tab = {
      path,
      name: basename(path),
      content: file.content,
      dirty: false,
      scrollPos: 0,
      lastActivated: Date.now()
    };

    // LRU eviction antes de insertar si excederíamos el límite.
    await evictIfNeeded();

    openTabs.update((list) => [...list, tab]);
    activeTabPath.set(path);
    return true;
  } catch (e) {
    tabsError.set((e as Error).message);
    return false;
  }
}

/** Cierra una tab. Si era la activa, activa la última restante (o null). */
export function closeTab(path: string): void {
  openTabs.update((list) => {
    const idx = list.findIndex((t) => t.path === path);
    if (idx === -1) return list;
    const next = list.slice();
    next.splice(idx, 1);
    // Si era la activa, mover el foco al vecino (prefiriendo el anterior).
    let currentActive: string | null = null;
    activeTabPath.subscribe((v) => (currentActive = v))();
    if (currentActive === path) {
      const fallback = next[idx - 1] ?? next[idx] ?? null;
      activeTabPath.set(fallback ? fallback.path : null);
    }
    return next;
  });
}

/** Activa una tab existente (touch LRU). */
export function setActive(path: string): void {
  openTabs.update((list) =>
    list.map((t) =>
      t.path === path ? { ...t, lastActivated: Date.now() } : t
    )
  );
  activeTabPath.set(path);
}

/** Marca la tab como dirty + actualiza contenido editado. */
export function markDirty(path: string, content: string): void {
  openTabs.update((list) =>
    list.map((t) =>
      t.path === path ? { ...t, content, dirty: true } : t
    )
  );
}

/** Guarda la tab activa al backend. Devuelve false si falla. */
export async function saveActive(): Promise<boolean> {
  let path: string | null = null;
  activeTabPath.subscribe((v) => (path = v))();
  if (path === null) return false;

  let tab: Tab | undefined;
  openTabs.subscribe((list) => {
    tab = list.find((t) => t.path === path);
  })();

  if (!tab) return false;

  const projectId = get(currentProjectId);
  if (projectId === null) {
    tabsError.set('no_project_selected');
    return false;
  }
  tabsError.set(null);
  try {
    await putFile(projectId, path as string, tab.content);
    openTabs.update((list) =>
      list.map((t) => (t.path === path ? { ...t, dirty: false } : t))
    );
    return true;
  } catch (e) {
    tabsError.set((e as Error).message);
    return false;
  }
}

/**
 * LRU eviction interno: si agregar una tab más excede MAX_TABS,
 * elimina la menos recientemente activa que NO esté dirty. Si todas están
 * dirty, no evicta (el caller decide qué hacer).
 *
 * Expuesto solo para tests.
 */
export async function evictIfNeeded(): Promise<void> {
  let list: Tab[] = [];
  openTabs.subscribe((v) => (list = v))();

  if (list.length < MAX_TABS) return;

  // Candidatos a eviccionar: no dirty, ordenados por lastActivated asc.
  const candidates = list
    .filter((t) => !t.dirty)
    .sort((a, b) => a.lastActivated - b.lastActivated);

  if (candidates.length === 0) {
    // No hay nada evictable sin perder cambios. No hacemos nada:
    // el caller (UI) debería confirmar con el usuario.
    return;
  }

  const victim = candidates[0];
  openTabs.update((l) => l.filter((t) => t.path !== victim.path));

  // Si la víctima era la activa, mover foco.
  let currentActive: string | null = null;
  activeTabPath.subscribe((v) => (currentActive = v))();
  if (currentActive === victim.path) {
    openTabs.subscribe((l) => {
      const fallback = l[l.length - 1] ?? null;
      activeTabPath.set(fallback ? fallback.path : null);
    })();
  }
}

/** Resetea el store (para tests). */
export function _resetTabsForTests(): void {
  openTabs.set([]);
  activeTabPath.set(null);
  tabsError.set(null);
}
