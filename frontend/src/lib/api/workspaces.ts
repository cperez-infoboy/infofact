// Cliente del workspace: árbol de archivos + lectura/escritura + gestión
// (crear/mover/copiar/borrar) + descarga. Espeja backend/routers/workspaces.py.
import { apiFetch, ApiError } from './client';

export interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children?: TreeNode[];
  // true si los hijos ya fueron cargados (lazy loading). Si es false,
  // la carpeta puede tener hijos aún no explorados por el backend.
  loaded?: boolean;
}

export interface FileContent {
  path: string;
  content: string;
}

export interface FileWriteResult {
  path: string;
  ok: boolean;
}

/** Árbol del workspace desde `path` (default: raíz). max_depth opcional. */
export function getTree(
  projectId: number,
  path: string = '.',
  maxDepth: number = 3
): Promise<TreeNode> {
  const qs = new URLSearchParams({
    project_id: String(projectId),
    path,
    max_depth: String(maxDepth)
  });
  return apiFetch<TreeNode>(`/api/workspaces/tree?${qs.toString()}`);
}

/** Lee un archivo del workspace como texto UTF-8. */
export function getFile(projectId: number, path: string): Promise<FileContent> {
  const qs = new URLSearchParams({
    project_id: String(projectId),
    path
  });
  return apiFetch<FileContent>(`/api/workspaces/file?${qs.toString()}`);
}

/** Escribe `content` en `path` dentro del workspace. */
export function putFile(
  projectId: number,
  path: string,
  content: string
): Promise<FileWriteResult> {
  const qs = new URLSearchParams({
    project_id: String(projectId),
    path
  });
  return apiFetch<FileWriteResult>(`/api/workspaces/file?${qs.toString()}`, {
    method: 'PUT',
    body: JSON.stringify({ content })
  });
}

export interface FsOpResult {
  path: string | null;
  ok: boolean;
}

/** Query string estándar de los endpoints /fs/* (project_id + extras). */
function fsQs(projectId: number, params: Record<string, string> = {}): string {
  return new URLSearchParams({ project_id: String(projectId), ...params }).toString();
}

/** Crea una carpeta (falla con already_exists si ya existe). */
export function createFolder(projectId: number, path: string): Promise<FsOpResult> {
  return apiFetch<FsOpResult>(`/api/workspaces/fs/mkdir?${fsQs(projectId)}`, {
    method: 'POST',
    body: JSON.stringify({ path })
  });
}

/** Crea un archivo vacío (falla con already_exists si ya existe). */
export function createFile(projectId: number, path: string): Promise<FsOpResult> {
  return apiFetch<FsOpResult>(`/api/workspaces/fs/file?${fsQs(projectId)}`, {
    method: 'POST',
    body: JSON.stringify({ path })
  });
}

/** Mueve/renombra `src` a `dst` (path destino completo; no pisa destino). */
export function moveEntry(
  projectId: number,
  src: string,
  dst: string
): Promise<FsOpResult> {
  return apiFetch<FsOpResult>(`/api/workspaces/fs/move?${fsQs(projectId)}`, {
    method: 'POST',
    body: JSON.stringify({ src, dst })
  });
}

/** Copia `src` dentro de `dstDir` (sufijo ` copia` ante colisión). */
export function copyEntry(
  projectId: number,
  src: string,
  dstDir: string
): Promise<FsOpResult> {
  return apiFetch<FsOpResult>(`/api/workspaces/fs/copy?${fsQs(projectId)}`, {
    method: 'POST',
    body: JSON.stringify({ src, dst_dir: dstDir })
  });
}

/** Borra un archivo o carpeta (recursivo). */
export function deleteEntry(projectId: number, path: string): Promise<FsOpResult> {
  return apiFetch<FsOpResult>(`/api/workspaces/fs/entry?${fsQs(projectId, { path })}`, {
    method: 'DELETE'
  });
}

/**
 * Descarga un archivo como adjunto. No usa apiFetch (respuesta binaria):
 * fetch → blob → objectURL + click de <a download>. Si falla, parsea
 * {detail} y lanza ApiError como el resto de la API.
 */
export async function downloadFile(projectId: number, path: string): Promise<void> {
  const qs = new URLSearchParams({ project_id: String(projectId), path });
  const res = await fetch(`/api/workspaces/download?${qs.toString()}`, {
    credentials: 'include'
  });
  if (!res.ok) {
    let code = 'request_failed';
    try {
      const body = await res.json();
      code = body.detail ?? code;
    } catch {
      // Respuesta no-JSON; nos quedamos con el código por defecto.
    }
    throw new ApiError(res.status, code);
  }
  const blob = await res.blob();
  const filename = path.split('/').pop() || 'descarga';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
