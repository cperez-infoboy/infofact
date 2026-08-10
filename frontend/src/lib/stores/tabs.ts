// Store de tabs del FileViewer. Archivo .ts PLANO → writable de svelte/store.
// LRU eviction: al superar MAX_TABS, se elimina el archivo limpio menos
// recientemente activo. Los view tabs (requerimientos / agrupamiento) son
// sticky: nunca se evictan (son singleton baratos que el usuario no quiere ver
// desaparecer).
import { get, writable } from 'svelte/store';
import { getFile, putFile } from '$lib/api/workspaces';
import { currentProjectId } from '$lib/stores/project';

export const MAX_TABS = 15;

export type TabKind = 'file' | 'requirements' | 'grouping' | 'srs' | 'analysis';

export interface FileTab {
  id: string; // === path
  kind: 'file';
  path: string;
  name: string;
  content: string;
  dirty: boolean;
  scrollPos: number;
  /** Último timestamp en que fue activado — para LRU. */
  lastActivated: number;
}

export interface ViewTab {
  id: string; // === kind (singleton: un solo tab por kind)
  kind: 'requirements' | 'grouping' | 'srs' | 'analysis';
  name: string;
  lastActivated: number;
}

export type Tab = FileTab | ViewTab;

export const openTabs = writable<Tab[]>([]);
export const activeTabId = writable<string | null>(null);
export const tabsError = writable<string | null>(null);

const VIEW_NAMES: Record<'requirements' | 'grouping' | 'srs' | 'analysis', string> = {
  requirements: 'Requerimientos',
  grouping: 'Agrupamiento',
  srs: 'SRS',
  analysis: 'Análisis'
};

function basename(p: string): string {
  const parts = p.split('/');
  return parts[parts.length - 1] || p;
}

/** Abre un archivo. Si ya está abierto, solo lo activa. Devuelve false si el
 *  fetch falla o no hay proyecto seleccionado. */
export async function openTab(path: string): Promise<boolean> {
  let existing: Tab | undefined;
  openTabs.subscribe((list) => {
    existing = list.find((t) => t.id === path);
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
    const tab: FileTab = {
      id: path,
      kind: 'file',
      path,
      name: basename(path),
      content: file.content,
      dirty: false,
      scrollPos: 0,
      lastActivated: Date.now()
    };
    await evictIfNeeded();
    openTabs.update((list) => [...list, tab]);
    activeTabId.set(path);
    return true;
  } catch (e) {
    tabsError.set((e as Error).message);
    return false;
  }
}

/** Abre una "vista" (no archivo): requerimientos o agrupamiento.
 *  Singleton por kind: si ya existe, solo la activa. No necesita proyecto
 *  para abrirse (el componente carga sus datos con el projectId que recibe). */
export async function openView(
  kind: 'requirements' | 'grouping' | 'srs' | 'analysis'
): Promise<void> {
  let existing: Tab | undefined;
  openTabs.subscribe((list) => {
    existing = list.find((t) => t.id === kind);
  })();

  if (existing) {
    setActive(kind);
    return;
  }

  const tab: ViewTab = {
    id: kind,
    kind,
    name: VIEW_NAMES[kind],
    lastActivated: Date.now()
  };
  await evictIfNeeded();
  openTabs.update((list) => [...list, tab]);
  activeTabId.set(kind);
}

/** Cierra una tab (por id). Si era la activa, activa un vecino. */
export function closeTab(id: string): void {
  openTabs.update((list) => {
    const idx = list.findIndex((t) => t.id === id);
    if (idx === -1) return list;
    const next = list.slice();
    next.splice(idx, 1);
    let currentActive: string | null = null;
    activeTabId.subscribe((v) => (currentActive = v))();
    if (currentActive === id) {
      const fallback = next[idx - 1] ?? next[idx] ?? null;
      activeTabId.set(fallback ? fallback.id : null);
    }
    return next;
  });
}

/** Activa una tab existente (touch LRU). */
export function setActive(id: string): void {
  openTabs.update((list) =>
    list.map((t) => (t.id === id ? { ...t, lastActivated: Date.now() } : t))
  );
  activeTabId.set(id);
}

/** Marca un tab de archivo como dirty + actualiza su contenido.
 *  No-op para view tabs (no son texto editable). */
export function markDirty(id: string, content: string): void {
  openTabs.update((list) =>
    list.map((t) =>
      t.id === id && t.kind === 'file' ? { ...t, content, dirty: true } : t
    )
  );
}

/** Guarda el tab activo al backend (solo archivos). Devuelve false si no hay
 *  tab activa, no es archivo, o el guardado falla. */
export async function saveActive(): Promise<boolean> {
  let id: string | null = null;
  activeTabId.subscribe((v) => (id = v))();
  if (id === null) return false;

  let tab: Tab | undefined;
  openTabs.subscribe((list) => {
    tab = list.find((t) => t.id === id);
  })();

  if (!tab || tab.kind !== 'file') return false;

  const projectId = get(currentProjectId);
  if (projectId === null) {
    tabsError.set('no_project_selected');
    return false;
  }
  tabsError.set(null);
  try {
    await putFile(projectId, tab.path, tab.content);
    openTabs.update((list) =>
      list.map((t) =>
        t.id === id && t.kind === 'file' ? { ...t, dirty: false } : t
      )
    );
    return true;
  } catch (e) {
    tabsError.set((e as Error).message);
    return false;
  }
}

/**
 * LRU eviction: si agregar una tab más excede MAX_TABS, elimina la menos
 * recientemente activa que sea un ARCHIVO limpio. Los view tabs son sticky
 * (no se evictan). Si todos los archivos están dirty, no hace nada (el caller
 * decide). Expuesto para tests.
 */
export async function evictIfNeeded(): Promise<void> {
  let list: Tab[] = [];
  openTabs.subscribe((v) => (list = v))();

  if (list.length < MAX_TABS) return;

  const candidates = list
    .filter((t): t is FileTab => t.kind === 'file' && !t.dirty)
    .sort((a, b) => a.lastActivated - b.lastActivated);

  if (candidates.length === 0) return;

  const victim = candidates[0];
  openTabs.update((l) => l.filter((t) => t.id !== victim.id));

  let currentActive: string | null = null;
  activeTabId.subscribe((v) => (currentActive = v))();
  if (currentActive === victim.id) {
    openTabs.subscribe((l) => {
      const fallback = l[l.length - 1] ?? null;
      activeTabId.set(fallback ? fallback.id : null);
    })();
  }
}

/** Resetea el store (para tests). */
export function _resetTabsForTests(): void {
  openTabs.set([]);
  activeTabId.set(null);
  tabsError.set(null);
}
