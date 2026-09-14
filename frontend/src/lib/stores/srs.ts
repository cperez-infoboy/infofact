// Estado del SRS del proyecto: progreso vivo (SSE) + datos persistidos.
// Archivo .ts PLANO -> writable/derived (NO runes; ver CLAUDE.md decision 6).
//
// El progreso vivo lo alimentan los fine events del subagente srs-agent
// (srs.progress / quality.found / goal.inferred / coverage.report / srs.ready)
// que el chat store inyecta via onSrsProgress / onQualityFound / onGoalInferred
// / onCoverageReport / onSrsReady (definidos abajo).
//
// A diferencia de la captura, las stage tools del subagente quedan ocultas
// tras el tool `task` de DeepAgents -> no podemos detectar inicio/fin por
// nombre de tool. El estado vivo se resetea solo: el PRIMER srs.progress
// inicia el run, srs.ready lo cierra (endSrs baja el flag pero conserva los
// hallazgos/goals para la UI).

import { get, writable, derived } from 'svelte/store';
import type { ProgressEvent } from '$lib/api/chat';
import type {
  QualityFoundEvent,
  GoalInferredEvent,
  CoverageReportEvent,
  SrsReadyEvent,
  SrsDraftEvent
} from '$lib/api/chat';
import type { SrsVersion } from '$lib/api/srs';

// --- Fine events (SSE vivo) -------------------------------------------------

/** Etapa SRS actual ({stage, message}). Null si no hay run activo. */
export const srsStage = writable<ProgressEvent | null>(null);

/** Hallazgos accionables (blocker/major) detectados en vivo. */
export const liveFindings = writable<QualityFoundEvent[]>([]);

/** Goals inferidos en vivo. */
export const liveGoals = writable<GoalInferredEvent[]>([]);

/** Reporte de cobertura en vivo (ISO 25010 + goals). */
export const liveCoverage = writable<CoverageReportEvent | null>(null);

/** Notificacion de SRS listo (version CANDIDATE). Null hasta srs.ready. */
export const srsReady = writable<SrsReadyEvent | null>(null);

/** Notificacion de version DRAFT materializada temprano (srs.draft). */
export const srsDraft = writable<SrsDraftEvent | null>(null);

/** True mientras el subagente srs-agent corre una generacion. */
export const srsRunning = writable<boolean>(false);

// --- Datos persistidos (cargados bajo demanda por la UI) -------------------

export const srsVersions = writable<SrsVersion[]>([]);
export const activeSrsVersion = writable<SrsVersion | null>(null);

// --- Derived (solo lectura) ------------------------------------------------

export const liveFindingCount = derived(liveFindings, ($f) => $f.length);

export const liveBlockerCount = derived(
  liveFindings,
  ($f) => $f.filter((x) => x.severity === 'blocker').length
);

export const liveGoalCount = derived(liveGoals, ($g) => $g.length);

// --- Mutadores --------------------------------------------------------------

/** Resetea el estado vivo (conserva los datos persistidos). */
export function resetSrsLive(): void {
  srsStage.set(null);
  liveFindings.set([]);
  liveGoals.set([]);
  liveCoverage.set(null);
  srsReady.set(null);
  srsDraft.set(null);
  srsRunning.set(false);
}

/** Inicia un run SRS: reset vivo + marca running. Idempotente. */
export function startSrs(): void {
  if (!get(srsRunning)) {
    srsStage.set(null);
    liveFindings.set([]);
    liveGoals.set([]);
    liveCoverage.set(null);
    srsReady.set(null);
    srsDraft.set(null);
  }
  srsRunning.set(true);
}

/** Cierra el run: baja el flag running pero conserva hallazgos/goals para UI. */
export function endSrs(): void {
  srsRunning.set(false);
  srsStage.set(null);
}

// --- Inyeccion de fine events (llamados por el chat store) -----------------

/** Handler de srs.progress: auto-inicia el run en el primer evento. */
export function onSrsProgress(p: ProgressEvent): void {
  if (!get(srsRunning)) startSrs();
  srsStage.set(p);
}

/** Handler de quality.found: acumula un hallazgo accionable. */
export function onQualityFound(f: QualityFoundEvent): void {
  liveFindings.update((list) => [...list, f]);
}

/** Handler de goal.inferred: acumula un goal inferido. */
export function onGoalInferred(g: GoalInferredEvent): void {
  liveGoals.update((list) => [...list, g]);
}

/** Handler de coverage.report: reemplaza el reporte de cobertura vivo. */
export function onCoverageReport(c: CoverageReportEvent): void {
  liveCoverage.set(c);
}

/** Handler de srs.ready: fija la version generada y cierra el run. */
export function onSrsReady(r: SrsReadyEvent): void {
  srsReady.set(r);
  endSrs();
}

/** Handler de srs.draft: version DRAFT visible antes del commit final. */
export function onSrsDraft(d: SrsDraftEvent): void {
  srsDraft.set(d);
}

/** Devuelve los stores a su estado inicial (tests / logout). */
export function _resetSrsForTests(): void {
  resetSrsLive();
  srsVersions.set([]);
  activeSrsVersion.set(null);
}
