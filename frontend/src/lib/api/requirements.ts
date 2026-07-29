// Cliente HTTP de requerimientos y planes de agrupamiento. Tipos espejan
// routers/requirements.py. La cookie JWT viaja vía apiFetch (credentials:include).
import { apiFetch } from './client';

/** Una entrada del ancla de origen: cita verbatim + metadata del documento. */
export interface RequirementSourceEntry {
  document_id?: string | null;
  section?: string | null;
  page?: number | null;
  quote?: string | null;
}

/**
 * Origen del requerimiento: una entrada (caso normal), varias (tras un merge)
 * o null (entrada manual). Espeja `_source_list` del backend.
 */
export type RequirementSource =
  | RequirementSourceEntry
  | RequirementSourceEntry[];

export interface RequirementItem {
  id: number;
  project_id: number;
  code: string;
  statement: string;
  type: string | null;
  priority: string | null;
  status: string | null;
  confidence: number | null;
  span_verified: boolean | null;
  parent_id: number | null;
  derived: boolean | null;
  merged_into: number | null;
  source: RequirementSource | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RequirementListOut {
  project_id: number;
  count: number;
  items: RequirementItem[];
}

// get_requirement devuelve un dict enriquecido; tipado laxo (acepta extras).
export interface RequirementDetail extends RequirementItem {
  parent_code?: string | null;
  acceptance_criteria?: unknown;
  relations?: unknown;
  revisions?: unknown;
  [k: string]: unknown;
}

export interface RequirementPatch {
  statement?: string;
  type?: string;
  priority?: string;
}

export interface MergeBody {
  keep_id: number;
  member_ids: number[];
  reason?: string;
}

export interface GroupingPlanSummary {
  id: number;
  status: string;
  generated_at: string | null;
  applied_at: string | null;
  groups: number;
  accepted: number;
}

export interface GroupMember {
  id: number;
  code: string | null;
  statement: string | null;
}

export interface GroupingGroupOut {
  id: number;
  keeper: { id: number; code: string | null; statement: string | null };
  members: GroupMember[];
  reason: string;
  confidence: number;
  decision: string;
}

export interface GroupingPlanDetail {
  id: number;
  project_id: number;
  status: string;
  generated_at: string | null;
  applied_at: string | null;
  groups: GroupingGroupOut[];
}

export interface ApplyResult {
  id: number;
  status: string;
  applied: number;
  already_applied: number;
  invalid: number;
  skipped: number;
}

/** Lista requerimientos del proyecto con filtros opcionales. */
export function listRequirements(
  projectId: number,
  params: Record<string, string> = {}
): Promise<RequirementListOut> {
  const qs = new URLSearchParams(params).toString();
  return apiFetch<RequirementListOut>(
    `/api/projects/${projectId}/requirements${qs ? '?' + qs : ''}`
  );
}

/** Detalle enriquecido (source, relaciones, revisiones). */
export function getRequirement(id: number): Promise<RequirementDetail> {
  return apiFetch<RequirementDetail>(`/api/requirements/${id}`);
}

/** Actualiza statement / type / priority. */
export function patchRequirement(
  id: number,
  body: RequirementPatch
): Promise<RequirementItem> {
  return apiFetch<RequirementItem>(`/api/requirements/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body)
  });
}

/** Fusiona N requerimientos en el keeper. */
export function mergeRequirements(body: MergeBody): Promise<RequirementItem> {
  return apiFetch<RequirementItem>('/api/requirements/merge', {
    method: 'POST',
    body: JSON.stringify(body)
  });
}

/** Lista planes de agrupamiento del proyecto. */
export function listGroupingPlans(
  projectId: number
): Promise<{ project_id: number; plans: GroupingPlanSummary[] }> {
  return apiFetch(`/api/projects/${projectId}/grouping-plans`);
}

/** Detalle de un plan con sus grupos. */
export function getGroupingPlan(
  projectId: number,
  planId: number
): Promise<GroupingPlanDetail> {
  return apiFetch(`/api/projects/${projectId}/grouping-plans/${planId}`);
}

/** Cambia la decision de un grupo (accept/reject/pending). */
export function setGroupDecision(
  projectId: number,
  planId: number,
  groupId: number,
  decision: string
): Promise<{ id: number; decision: string }> {
  return apiFetch(
    `/api/projects/${projectId}/grouping-plans/${planId}/groups/${groupId}`,
    { method: 'PATCH', body: JSON.stringify({ decision }) }
  );
}

/** Aplica el plan: fusiona los grupos decision=accept (idempotente). */
export function applyGroupingPlan(
  projectId: number,
  planId: number
): Promise<ApplyResult> {
  return apiFetch(`/api/projects/${projectId}/grouping-plans/${planId}/apply`, {
    method: 'POST'
  });
}
