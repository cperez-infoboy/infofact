// Store del workspace: árbol de archivos con carga lazy (lazy loading).
//
// El árbol se carga con poca profundidad (max_depth=2) y las carpetas
// cuyos hijos no vinieron se cargan al expandirlas (loadChildren). El
// polling y el refresh manual mergean el árbol fresco con el existente
// para no perder los subárboles ya cargados ni el estado expandido.
//
// Archivo .ts PLANO → writable de svelte/store.
import { writable, get } from 'svelte/store';
import { getTree, type TreeNode } from '$lib/api/workspaces';
import { currentProjectId } from '$lib/stores/project';

export const tree = writable<TreeNode | null>(null);
export const workspaceError = writable<string | null>(null);
export const workspaceLoading = writable<boolean>(false);

// Conjunto de paths cuyo contenido se está cargando (lazy). El nodo
// muestra un spinner mientras su path está aquí.
export const loadingPaths = writable<Set<string>>(new Set());

let pollTimer: ReturnType<typeof setInterval> | null = null;

/**
 * Normaliza un nodo crudo del backend:
 *  - type siempre presente (default 'file').
 *  - children siempre es array si type='dir'.
 *  - loaded: true si el backend ya trajo hijos (children no vacío);
 *    false si vino vacío (puede tener hijos no explorados → lazy).
 *
 *  Defensivo: el backend puede omitir campos en rutas legacy.
 */
export function normalizeNode(raw: unknown): TreeNode {
  const r = (raw ?? {}) as Record<string, unknown>;
  const name = typeof r.name === 'string' ? r.name : '';
  const path = typeof r.path === 'string' ? r.path : name;
  const type = r.type === 'dir' ? 'dir' : 'file';

  if (type === 'dir') {
    const childrenRaw = Array.isArray(r.children) ? r.children : [];
    return {
      name,
      path,
      type: 'dir',
      children: childrenRaw.map(normalizeNode),
      loaded: childrenRaw.length > 0
    };
  }
  return { name, path, type: 'file' };
}

/**
 * Combina el nodo fresco con el existente. Si el existente ya tenía sus
 * hijos cargados (loaded=true), se preservan: el refresh de nivel raíz
 * no debe pisar lo que el usuario expandió vía lazy loading.
 */
function mergeNode(existing: TreeNode | undefined, fresh: TreeNode): TreeNode {
  if (
    existing &&
    existing.type === 'dir' &&
    fresh.type === 'dir' &&
    existing.loaded &&
    existing.children
  ) {
    return { ...fresh, children: existing.children, loaded: true };
  }
  return fresh;
}

function mergeTree(existing: TreeNode | null, fresh: TreeNode): TreeNode {
  if (!existing) return fresh;
  if (
    existing.type === 'dir' &&
    fresh.type === 'dir' &&
    existing.children &&
    fresh.children
  ) {
    const byPath = new Map(existing.children.map((c) => [c.path, c]));
    const merged = fresh.children.map((fc) => mergeNode(byPath.get(fc.path), fc));
    return {
      ...fresh,
      children: merged,
      loaded: fresh.loaded || existing.loaded
    };
  }
  return fresh;
}

/** Reemplaza recursivamente los children del nodo cuyo path === targetPath. */
function setChildren(
  node: TreeNode,
  targetPath: string,
  children: TreeNode[]
): TreeNode {
  if (node.path === targetPath && node.type === 'dir') {
    return { ...node, children, loaded: true };
  }
  if (node.children) {
    return {
      ...node,
      children: node.children.map((c) => setChildren(c, targetPath, children))
    };
  }
  return node;
}

/**
 * Recarga el árbol. Por defecto mergea con lo existente (preserva
 * subárboles lazy cargados). Con `force=true` reemplaza por completo
 * (botón ↻ o cambio de proyecto).
 */
export async function refreshTree(
  path: string = '.',
  force: boolean = false
): Promise<void> {
  const projectId = get(currentProjectId);
  if (projectId === null) {
    tree.set(null);
    return;
  }
  workspaceLoading.set(true);
  workspaceError.set(null);
  try {
    const raw = await getTree(projectId, path, 2);
    const fresh = normalizeNode(raw);
    tree.update((existing) =>
      path === '.' && !force ? mergeTree(existing, fresh) : fresh
    );
  } catch (e) {
    workspaceError.set((e as Error).message);
  } finally {
    workspaceLoading.set(false);
  }
}

/**
 * Carga los hijos de un directorio (lazy). Se invoca al expandir una
 * carpeta cuyo `loaded` es false. Mergea el resultado al árbol.
 */
export async function loadChildren(nodePath: string): Promise<void> {
  const projectId = get(currentProjectId);
  if (projectId === null) return;

  loadingPaths.update((s) => {
    const next = new Set(s);
    next.add(nodePath);
    return next;
  });

  try {
    const raw = await getTree(projectId, nodePath, 2);
    const fetched = normalizeNode(raw);
    const children = fetched.children ?? [];
    tree.update((root) => (root ? setChildren(root, nodePath, children) : root));
  } catch (e) {
    workspaceError.set((e as Error).message);
  } finally {
    loadingPaths.update((s) => {
      const next = new Set(s);
      next.delete(nodePath);
      return next;
    });
  }
}

/** Arranca polling del árbol (mergea, no pisa lazy). No-stack si ya hay uno. */
export function startPolling(intervalMs: number = 5000): void {
  if (pollTimer !== null) return;
  pollTimer = setInterval(() => {
    refreshTree().catch(() => {
      // Error de polling no rompe la UI; la próxima tick reintenta.
    });
  }, intervalMs);
}

/** Detiene el polling. Idempotente. */
export function stopPolling(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}
