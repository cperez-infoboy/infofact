// Cliente del workspace: árbol de archivos + lectura/escritura.
// Espeja backend/routers/workspaces.py.
import { apiFetch } from './client';

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
