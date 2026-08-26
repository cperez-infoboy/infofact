// Cliente de proyectos y sesiones. Tipos espejan routers/projects.py.
import { apiFetch } from './client';

export interface ProjectOut {
  id: number;
  name: string;
  slug: string;
  phase: string;
  created_at: string;
  initial_session_id?: number | null;
}

export interface SessionOut {
  id: number;
  project_id: number;
  title: string;
  phase: string;
  created_at: string;
}

export interface MessageOut {
  id: number | string;
  role: string;
  content: string;
  created_at: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_args?: Record<string, unknown> | null;
  status?: string | null;
  /** Foto de sesión: flag persistido por el relay (null = ruta legacy). */
  is_intermediate?: boolean | null;
}

export interface ProjectDetail extends ProjectOut {
  description?: string | null;
  sessions: SessionOut[];
}

export interface SessionDetail extends SessionOut {
  messages: MessageOut[];
}

export interface ProjectCreateBody {
  name: string;
  phase?: string;
}

export interface SessionCreateBody {
  title?: string;
}

export interface SrsCounts {
  live: number;
  soft_deleted: number;
  open_relations: number;
  by_type: Record<string, number>;
  by_priority: Record<string, number>;
}

export interface SrsOut {
  project_id: number;
  markdown: string;
  generated_at: string;
  counts: SrsCounts;
}

/** Lista los proyectos del usuario (sin initial_session_id). */
export function listProjects(): Promise<ProjectOut[]> {
  return apiFetch<ProjectOut[]>('/api/projects');
}

/** Crea un proyecto; el backend crea también la sesión inicial. */
export function createProject(body: ProjectCreateBody): Promise<ProjectOut> {
  return apiFetch<ProjectOut>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(body)
  });
}

/** Detalle del proyecto con sus sesiones. */
export function getProject(id: number): Promise<ProjectDetail> {
  return apiFetch<ProjectDetail>(`/api/projects/${id}`);
}

/** Crea una nueva sesión bajo un proyecto. */
export function createSession(
  projectId: number,
  body: SessionCreateBody = {}
): Promise<SessionOut> {
  return apiFetch<SessionOut>(`/api/projects/${projectId}/sessions`, {
    method: 'POST',
    body: JSON.stringify(body)
  });
}

/** Lista sesiones de un proyecto. */
export function listSessions(projectId: number): Promise<SessionOut[]> {
  return apiFetch<SessionOut[]>(`/api/projects/${projectId}/sessions`);
}

/** Detalle de una sesión con sus mensajes. */
export function getSessionDetail(sessionId: number): Promise<SessionDetail> {
  return apiFetch<SessionDetail>(`/api/chat/sessions/${sessionId}`);
}

/** Obtiene el SRS Markdown generado desde los RequirementItem del proyecto. */
export function getProjectSrs(projectId: number): Promise<SrsOut> {
  return apiFetch<SrsOut>(`/api/projects/${projectId}/srs`);
}
