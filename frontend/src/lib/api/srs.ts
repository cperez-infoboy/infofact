// Cliente del SRS (Software Requirements Specification) versionado.
// Tipos espejan los response models de backend/routers/srs.py.
// `credentials: 'include'` via apiFetch (cookie JWT HttpOnly).
import { apiFetch } from './client';

// --- Tipos (espejo de los response models de srs.py) -----------------------

export type SrsStatus = 'candidate' | 'in_review' | 'locked';
export type GoalKind = 'functional_goal' | 'softgoal' | 'obstacle';
export type GoalStatus = 'proposed' | 'confirmed' | 'rejected';
export type FindingSeverity = 'blocker' | 'major' | 'minor' | 'info';
export type FindingDimension =
  | 'incose_rule'
  | 'requirement_smell'
  | 'ears_violation'
  | 'ambiguity'
  | 'coverage_gap'
  | 'missing_req';

/** Versión de SRS (el markdown llega solo en el detalle de una versión). */
export interface SrsVersion {
  id: number;
  project_id: number;
  version: number;
  status: string;
  structure: unknown[];
  narrative: Record<string, unknown>;
  markdown: string | null;
  quality_summary: Record<string, unknown>;
  coverage: Record<string, unknown>;
  traceability: Record<string, unknown>;
  review_flags: Record<string, unknown>;
  requirement_codes: unknown[];
  requirement_count: number;
  generated_at: string | null;
  generated_by: string | null;
  reviewed_at: string | null;
  locked_at: string | null;
}

/** Respuesta del commit: crea una versión CANDIDATE. */
export interface SrsCommit {
  project_id: number;
  version: number;
  status: string;
  requirement_count: number;
  quality_summary: Record<string, unknown>;
  coverage: Record<string, unknown>;
  goals: Record<string, unknown>;
}

/** Hallazgo de calidad persistido (RequirementFinding). */
export interface Finding {
  id: number;
  req_id: number | null;
  scope: string;
  dimension: string;
  rule_id: string;
  severity: string;
  message: string;
  suggestion: string | null;
  status: string;
  ears_pattern: string | null;
  detected_by: string | null;
}

/** Resumen de calidad + hallazgos (GET /quality). */
export interface QualityReport {
  summary: Record<string, unknown>;
  findings: Finding[];
}

/** Matriz de cobertura (GET /coverage, del último SRS). */
export interface CoverageReport {
  coverage: Record<string, unknown>;
}

/** Goal del proyecto (jerarquía GORE). */
export interface Goal {
  id: number;
  code: string;
  statement: string;
  kind: string;
  parent_id: number | null;
  rationale: string | null;
  source: Record<string, unknown> | null;
  confidence: number;
  status: string;
}

/** Matriz de trazabilidad goal <-> req <-> fuente. */
export interface TraceabilityReport {
  goals: Goal[];
  matrix: Record<string, unknown>[];
}

// --- Bodies -----------------------------------------------------------------

export interface SrsStatusBody {
  status: 'in_review' | 'locked';
}
export interface SrsNarrativeBody {
  narrative: Record<string, unknown>;
}
export interface SrsReviewFlagsBody {
  review_flags: Record<string, unknown>;
}
export type SrsPatchBody = SrsStatusBody | SrsNarrativeBody | SrsReviewFlagsBody;

export interface GoalPatchBody {
  statement?: string;
  rationale?: string;
  status?: 'proposed' | 'confirmed' | 'rejected';
}

// --- Funciones --------------------------------------------------------------

/** Genera un SRS CANDIDATE (orquesta calidad + goals + cobertura). */
export function commitSrs(projectId: number): Promise<SrsCommit> {
  return apiFetch<SrsCommit>(`/api/projects/${projectId}/srs/commit`, {
    method: 'POST'
  });
}

/** Lista las versiones del proyecto (sin markdown). */
export function listSrsVersions(projectId: number): Promise<SrsVersion[]> {
  return apiFetch<SrsVersion[]>(`/api/projects/${projectId}/srs/versions`);
}

/** Detalle de una versión (con markdown). */
export function getSrsVersion(
  projectId: number,
  version: number
): Promise<SrsVersion> {
  return apiFetch<SrsVersion>(
    `/api/projects/${projectId}/srs/versions/${version}`
  );
}

/** Mutación controlada de una versión (estado / narrativa / pendientes). */
export function patchSrsVersion(
  projectId: number,
  version: number,
  body: SrsPatchBody
): Promise<SrsVersion> {
  return apiFetch<SrsVersion>(
    `/api/projects/${projectId}/srs/versions/${version}`,
    { method: 'PATCH', body: JSON.stringify(body) }
  );
}

/** Refresca solo las secciones proyectadas (markdown + conteo). */
export function reprojectSrs(
  projectId: number,
  version: number
): Promise<SrsVersion> {
  return apiFetch<SrsVersion>(
    `/api/projects/${projectId}/srs/versions/${version}/reproject`,
    { method: 'POST' }
  );
}

/** Resumen de calidad + hallazgos del proyecto. */
export function getQuality(projectId: number): Promise<QualityReport> {
  return apiFetch<QualityReport>(`/api/projects/${projectId}/quality`);
}

/** Matriz de cobertura (ISO 25010 + 29148 + goals) del último SRS. */
export function getCoverage(projectId: number): Promise<CoverageReport> {
  return apiFetch<CoverageReport>(`/api/projects/${projectId}/coverage`);
}

/** Hallazgos de calidad de un requerimiento. */
export function getRequirementFindings(
  projectId: number,
  reqId: number
): Promise<Finding[]> {
  return apiFetch<Finding[]>(
    `/api/projects/${projectId}/requirements/${reqId}/findings`
  );
}

/** Lista los goals del proyecto (jerarquía GORE). */
export function listGoals(projectId: number): Promise<Goal[]> {
  return apiFetch<Goal[]>(`/api/projects/${projectId}/goals`);
}

/** Detalle de un goal. */
export function getGoal(projectId: number, goalId: number): Promise<Goal> {
  return apiFetch<Goal>(`/api/projects/${projectId}/goals/${goalId}`);
}

/** Edita un goal (statement / rationale / status). */
export function patchGoal(
  projectId: number,
  goalId: number,
  body: GoalPatchBody
): Promise<Goal> {
  return apiFetch<Goal>(`/api/projects/${projectId}/goals/${goalId}`, {
    method: 'PATCH',
    body: JSON.stringify(body)
  });
}

/** Matriz de trazabilidad goal <-> requerimiento <-> fuente. */
export function getTraceability(projectId: number): Promise<TraceabilityReport> {
  return apiFetch<TraceabilityReport>(`/api/projects/${projectId}/traceability`);
}
