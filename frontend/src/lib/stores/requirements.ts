// Store de requerimientos y planes de agrupamiento. Archivo .ts PLANO →
// writable/derived de svelte/store. Runes PROHIBIDAS aquí (CLAUDE.md #6).
import { writable, derived, get } from 'svelte/store';
import * as reqApi from '$lib/api/requirements';
import type {
  RequirementItem,
  RequirementDetail,
  GroupingPlanSummary,
  GroupingPlanDetail,
  ApplyResult
} from '$lib/api/requirements';
import type { GroupingReadyEvent } from '$lib/api/chat';

export const items = writable<RequirementItem[]>([]);
export const selectedId = writable<number | null>(null);
export const selectedDetail = writable<RequirementDetail | null>(null);
export const filters = writable<{
  status?: string;
  type?: string;
  priority?: string;
}>({});
export const reqLoading = writable(false);
export const reqError = writable<string | null>(null);

export const plans = writable<GroupingPlanSummary[]>([]);
export const activePlanId = writable<number | null>(null);
export const activePlan = writable<GroupingPlanDetail | null>(null);
export const planLoading = writable(false);
export const planError = writable<string | null>(null);

// El project activo se setea desde el overlay (no es reactivo: lo pasa +page).
let _projectId: number | null = null;
export function setProject(pid: number | null): void {
  _projectId = pid;
}

/** Carga la lista de requerimientos según los filtros activos. */
export async function loadRequirements(): Promise<void> {
  if (_projectId === null) return;
  reqLoading.set(true);
  reqError.set(null);
  try {
    const f = get(filters);
    const params: Record<string, string> = {};
    if (f.status) params.status = f.status;
    if (f.type) params.type = f.type;
    if (f.priority) params.priority = f.priority;
    const out = await reqApi.listRequirements(_projectId, params);
    items.set(out.items);
  } catch (e) {
    reqError.set((e as Error).message);
  } finally {
    reqLoading.set(false);
  }
}

/** Selecciona un requerimiento y carga su detalle enriquecido. */
export async function selectRequirement(id: number | null): Promise<void> {
  selectedId.set(id);
  selectedDetail.set(null);
  if (id === null) return;
  try {
    selectedDetail.set(await reqApi.getRequirement(id));
  } catch (e) {
    reqError.set((e as Error).message);
  }
}

/** Guarda statement/type/priority; actualiza lista y detalle en el store. */
export async function saveRequirement(
  id: number,
  body: reqApi.RequirementPatch
): Promise<void> {
  const updated = await reqApi.patchRequirement(id, body);
  items.update((list) =>
    list.map((it) => (it.id === id ? { ...it, ...updated } : it))
  );
  selectedDetail.update((d) => (d && d.id === id ? { ...d, ...updated } : d));
}

/** Carga los planes del proyecto; deja activo el más reciente si ninguno lo está. */
export async function loadPlans(): Promise<void> {
  if (_projectId === null) return;
  planLoading.set(true);
  planError.set(null);
  try {
    const out = await reqApi.listGroupingPlans(_projectId);
    plans.set(out.plans);
    if (get(activePlanId) === null && out.plans.length > 0) {
      activePlanId.set(out.plans[0].id);
    }
    if (get(activePlanId) !== null) {
      await selectPlan(get(activePlanId));
    }
  } catch (e) {
    planError.set((e as Error).message);
  } finally {
    planLoading.set(false);
  }
}

/** Selecciona un plan y carga su detalle con grupos. */
export async function selectPlan(id: number | null): Promise<void> {
  activePlanId.set(id);
  activePlan.set(null);
  if (id === null || _projectId === null) return;
  try {
    activePlan.set(await reqApi.getGroupingPlan(_projectId, id));
  } catch (e) {
    planError.set((e as Error).message);
  }
}

/** Cambia la decision de un grupo y recarga el plan activo. */
export async function setDecision(
  groupId: number,
  decision: string
): Promise<void> {
  if (_projectId === null || get(activePlanId) === null) return;
  await reqApi.setGroupDecision(_projectId, get(activePlanId)!, groupId, decision);
  await selectPlan(get(activePlanId));
}

/** Aplica el plan activo; refresca plan, lista de planes y requerimientos. */
export async function applyPlan(): Promise<ApplyResult | null> {
  if (_projectId === null || get(activePlanId) === null) return null;
  const res = await reqApi.applyGroupingPlan(_projectId, get(activePlanId)!);
  await selectPlan(get(activePlanId));
  await loadPlans();
  await loadRequirements();
  return res;
}

/** Handler de grouping.ready (SSE): recarga los planes y selecciona el nuevo.
 *
 *  Viene tanto de la ruta directa de /agrupar como de la tool review_grouping
 *  (ruta agéntica); cierra el gap de refresco manual del panel. NO abre la
 *  pestaña de agrupamiento por sí sola (paridad con onSrsReady).
 */
export async function onGroupingReady(e: GroupingReadyEvent): Promise<void> {
  if (_projectId === null) return;
  await loadPlans();
  if (e.plan_id) {
    await selectPlan(e.plan_id);
  }
}
