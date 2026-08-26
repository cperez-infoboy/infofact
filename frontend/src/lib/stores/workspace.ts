// Store del workspace: árbol de archivos con carga lazy (lazy loading)
// + operaciones del explorador (crear/renombrar/mover/copiar/borrar).
//
// El árbol se carga con poca profundidad (max_depth=2) y las carpetas
// cuyos hijos no vinieron se cargan al expandirlas (loadChildren). El
// polling y el refresh manual mergean el árbol fresco con el existente
// para no perder los subárboles ya cargados ni el estado expandido.
//
// Archivo .ts PLANO → writable de svelte/store.
import { writable, get } from 'svelte/store';
import {
  getTree,
  createFolder,
  createFile,
  moveEntry as apiMoveEntry,
  copyEntry as apiCopyEntry,
  deleteEntry as apiDeleteEntry,
  type TreeNode
} from '$lib/api/workspaces';
import { currentProjectId } from '$lib/stores/project';
import { closeTabsUnderPath, retargetTabsUnderPath } from '$lib/stores/tabs';

export const tree = writable<TreeNode | null>(null);
export const workspaceError = writable<string | null>(null);
export const workspaceLoading = writable<boolean>(false);

// Conjunto de paths cuyo contenido se está cargando (lazy). El nodo
// muestra un spinner mientras su path está aquí.
export const loadingPaths = writable<Set<string>>(new Set());

let pollTimer: ReturnType<typeof setInterval> | null = null;

// --- Estado de UI del explorador (selección, menú contextual, clipboard) ---

/** MIME custom del drag&drop interno del explorador (mover entre carpetas). */
export const DND_MIME = 'application/x-infofact-node';

/** Nodo seleccionado en el explorador (archivos Y carpetas). */
export const selectedPath = writable<string | null>(null);
export const selectedIsDir = writable<boolean>(false);

/** Path que se está renombrando inline (input en el nodo). */
export const renamingPath = writable<string | null>(null);

/** Path copiado (portapapeles del explorador, para Pegar en una carpeta). */
export const clipboardPath = writable<string | null>(null);

/** Creación pendiente: input inline como primer hijo de parentPath. */
export interface PendingCreate {
  parentPath: string;
  kind: 'file' | 'dir';
}
export const pendingCreate = writable<PendingCreate | null>(null);

/** True mientras corre una operación de filesystem (deshabilita acciones). */
export const opsBusy = writable<boolean>(false);

export function selectNode(path: string, isDir: boolean): void {
  selectedPath.set(path);
  selectedIsDir.set(isDir);
}

/** Directorio padre de un path relativo ('docs/a.md' → 'docs'; 'a.md' → '.'). */
export function parentOf(path: string): string {
  const idx = path.lastIndexOf('/');
  if (idx <= 0) return '.';
  return path.slice(0, idx);
}

/** Traduce códigos de error del backend a mensajes en español para la UI. */
export function humanizeWorkspaceError(code: string): string {
  const map: Record<string, string> = {
    invalid_path: 'Ruta inválida',
    invalid_name: 'Nombre inválido',
    is_root: 'La raíz del workspace no se puede modificar',
    not_found: 'No existe la ruta indicada',
    already_exists: 'Ya existe un elemento con ese nombre',
    target_exists: 'Ya existe un elemento con ese nombre en el destino',
    target_inside_source: 'No se puede mover una carpeta dentro de sí misma',
    file_not_found: 'Archivo no encontrado',
    no_project_selected: 'No hay proyecto seleccionado',
    request_failed: 'Error de conexión'
  };
  return map[code] ?? code;
}

/** Valida un nombre de archivo/carpeta (sin separadores, sin . / ..). */
function sanitizeName(name: string): string | null {
  const v = name.trim();
  if (!v || v.includes('/') || v === '.' || v === '..' || v.length > 255) return null;
  return v;
}

/** Envuelve una operación: setea opsBusy y captura errores en workspaceError. */
async function runOp(fn: () => Promise<unknown>): Promise<boolean> {
  opsBusy.set(true);
  workspaceError.set(null);
  try {
    await fn();
    return true;
  } catch (e) {
    workspaceError.set((e as Error).message);
    return false;
  } finally {
    opsBusy.set(false);
  }
}

/** Crea un archivo o carpeta `name` dentro de `parentPath`. */
export async function createEntry(
  parentPath: string,
  kind: 'file' | 'dir',
  rawName: string
): Promise<boolean> {
  const name = sanitizeName(rawName);
  if (name === null) {
    workspaceError.set('invalid_name');
    return false;
  }
  const projectId = get(currentProjectId);
  if (projectId === null) {
    workspaceError.set('no_project_selected');
    return false;
  }
  const path = parentPath === '.' ? name : `${parentPath}/${name}`;
  const okOp = await runOp(() =>
    kind === 'dir' ? createFolder(projectId, path) : createFile(projectId, path)
  );
  if (okOp) {
    pendingCreate.set(null);
    await loadChildren(parentPath);
    selectNode(path, kind === 'dir');
  }
  return okOp;
}

/** Renombra `node` a `newName` (mismo directorio). Re-apunta tabs abiertas. */
export async function renameEntry(node: TreeNode, rawName: string): Promise<boolean> {
  const name = sanitizeName(rawName);
  if (name === null) {
    workspaceError.set('invalid_name');
    return false;
  }
  if (node.path === '.') {
    workspaceError.set('is_root');
    return false;
  }
  renamingPath.set(null);
  const parent = parentOf(node.path);
  const dst = parent === '.' ? name : `${parent}/${name}`;
  if (dst === node.path) return true;
  const projectId = get(currentProjectId);
  if (projectId === null) {
    workspaceError.set('no_project_selected');
    return false;
  }
  const okOp = await runOp(() => apiMoveEntry(projectId, node.path, dst));
  if (okOp) {
    retargetTabsUnderPath(node.path, dst);
    await loadChildren(parent);
    selectNode(dst, node.type === 'dir');
  }
  return okOp;
}

/** Borra `node` (archivo o carpeta). Cierra tabs bajo ese path. */
export async function deleteEntry(node: TreeNode): Promise<boolean> {
  if (node.path === '.') {
    workspaceError.set('is_root');
    return false;
  }
  const projectId = get(currentProjectId);
  if (projectId === null) {
    workspaceError.set('no_project_selected');
    return false;
  }
  const parent = parentOf(node.path);
  const okOp = await runOp(() => apiDeleteEntry(projectId, node.path));
  if (okOp) {
    closeTabsUnderPath(node.path);
    selectedPath.update((p) =>
      p === node.path || (p !== null && p.startsWith(node.path + '/')) ? null : p
    );
    await loadChildren(parent);
  }
  return okOp;
}

/** Mueve `src` (drag&drop o menú) dentro de `dstDir`. Re-apunta tabs. */
export async function moveEntryInto(src: string, dstDir: string): Promise<boolean> {
  const projectId = get(currentProjectId);
  if (projectId === null) {
    workspaceError.set('no_project_selected');
    return false;
  }
  const name = src.split('/').pop() ?? src;
  const dst = dstDir === '.' ? name : `${dstDir}/${name}`;
  if (dst === src) return true;
  const okOp = await runOp(() => apiMoveEntry(projectId, src, dst));
  if (okOp) {
    retargetTabsUnderPath(src, dst);
    const oldParent = parentOf(src);
    await loadChildren(oldParent);
    if (dstDir !== oldParent) await loadChildren(dstDir);
  }
  return okOp;
}

/** Copia `src` (duplicar/pegar) dentro de `dstDir`. */
export async function copyEntryInto(src: string, dstDir: string): Promise<boolean> {
  const projectId = get(currentProjectId);
  if (projectId === null) {
    workspaceError.set('no_project_selected');
    return false;
  }
  const okOp = await runOp(() => apiCopyEntry(projectId, src, dstDir));
  if (okOp) await loadChildren(dstDir);
  return okOp;
}

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
 * (botón ↻ o cambio de proyecto). Con `silent=true` (polling) no toca
 * workspaceError: un error de una operación previa debe seguir visible
 * hasta la próxima acción explícita del usuario, no borrarse 5s después.
 */
export async function refreshTree(
  path: string = '.',
  force: boolean = false,
  silent: boolean = false
): Promise<void> {
  const projectId = get(currentProjectId);
  if (projectId === null) {
    tree.set(null);
    return;
  }
  workspaceLoading.set(true);
  if (!silent) workspaceError.set(null);
  try {
    const raw = await getTree(projectId, path, 2);
    const fresh = normalizeNode(raw);
    tree.update((existing) =>
      path === '.' && !force ? mergeTree(existing, fresh) : fresh
    );
  } catch (e) {
    if (!silent) workspaceError.set((e as Error).message);
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

/** Arranca polling del árbol (mergea, no pisa lazy, no borra errores). */
export function startPolling(intervalMs: number = 5000): void {
  if (pollTimer !== null) return;
  pollTimer = setInterval(() => {
    refreshTree('.', false, true).catch(() => {
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
