// Store del workspace: árbol de archivos + polling.
// Archivo .ts PLANO → writable de svelte/store.
import { writable, get } from 'svelte/store';
import { getTree, type TreeNode } from '$lib/api/workspaces';
import { currentProjectId } from '$lib/stores/project';

export const tree = writable<TreeNode | null>(null);
export const workspaceError = writable<string | null>(null);
export const workspaceLoading = writable<boolean>(false);

let pollTimer: ReturnType<typeof setInterval> | null = null;

/** Normaliza un nodo crudo del backend:
 *  - type siempre presente (default 'file').
 *  - children siempre es array si type='dir', ausente si file.
 *  - path/name siempre strings.
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
      children: childrenRaw.map(normalizeNode)
    };
  }
  return { name, path, type: 'file' };
}

/** Recarga el árbol desde la raíz.
 *  Sin proyecto seleccionado → tree a null y retorna temprano
 *  (no hay workspace para listar). Usa get() one-shot para evitar
 *  loops reactivos: refreshTree no suscribe a currentProjectId.
 */
export async function refreshTree(path: string = '.'): Promise<void> {
  const projectId = get(currentProjectId);
  if (projectId === null) {
    tree.set(null);
    return;
  }
  workspaceLoading.set(true);
  workspaceError.set(null);
  try {
    const raw = await getTree(projectId, path, 3);
    tree.set(normalizeNode(raw));
  } catch (e) {
    workspaceError.set((e as Error).message);
  } finally {
    workspaceLoading.set(false);
  }
}

/** Arranca polling del árbol. No-stack si ya hay uno activo. */
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
