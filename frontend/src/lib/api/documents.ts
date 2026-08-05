// Cliente de documentos fuente del proyecto. Tipos espejan routers/documents.ts.
import { apiFetch } from './client';

export interface DocumentOut {
  id: number;
  project_id: number;
  rel_path: string;
  filename: string;
  extension: string;
  mime: string | null;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  parse_status: string;
  parser_used: string | null;
  error: string | null;
  created_at: string | null;
}

export interface ScanResult {
  registered: DocumentOut[];
  skipped: number;
}

/**
 * Sube un documento binario al workspace del proyecto.
 * Usa multipart/form-data; apiFetch omite Content-Type para que el browser
 * fije el boundary automáticamente.
 */
export function uploadDocument(
  projectId: number,
  file: File,
  relPath?: string
): Promise<DocumentOut> {
  const form = new FormData();
  form.append('project_id', String(projectId));
  form.append('file', file);
  if (relPath) form.append('rel_path', relPath);
  return apiFetch<DocumentOut>('/api/documents/upload', {
    method: 'POST',
    body: form
  });
}

/** Registra archivos ya presentes en el workspace (sin re-subir). */
export function scanDocuments(
  projectId: number,
  relPath: string = '.',
  recursive: boolean = true
): Promise<ScanResult> {
  return apiFetch<ScanResult>('/api/documents/scan', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      rel_path: relPath,
      recursive
    })
  });
}

/** Lista los documentos del proyecto con su parse_status. */
export function listDocuments(projectId: number): Promise<DocumentOut[]> {
  return apiFetch<DocumentOut[]>(`/api/documents?project_id=${projectId}`);
}

/** Borra un documento. Si `purge`, elimina también el archivo del workspace. */
export function deleteDocument(
  documentId: number,
  projectId: number,
  purge = false
): Promise<void> {
  const q = new URLSearchParams({ project_id: String(projectId) });
  if (purge) q.set('purge', 'true');
  return apiFetch<void>(`/api/documents/${documentId}?${q.toString()}`, {
    method: 'DELETE'
  });
}
