// Estado del análisis del proyecto: progreso vivo (SSE) + datos persistidos.
// Archivo .ts PLANO -> writable/derived (NO runes; ver CLAUDE.md decision 6).
//
// El progreso vivo lo alimentan los fine events del subagente analysis-agent
// (analysis.progress / mer_ready / nfr_ready / process_ready / adr_ready /
// subproject_ready / analysis.ready) que el chat store inyecta via
// onAnalysisProgress / onAnalysisMerReady / onAnalysisNfrReady /
// onAnalysisProcessReady / onAnalysisAdrReady / onAnalysisSubProjectReady /
// onAnalysisReady (definidos abajo).
//
// Igual que con SRS, las stage tools del subagente quedan ocultas tras la
// tool `task` de DeepAgents -> no podemos detectar inicio/fin por nombre de
// tool. El estado vivo se resetea solo: el PRIMER analysis.progress inicia
// el run, analysis.ready lo cierra (endAnalysis baja el flag pero conserva
// los conteos para la UI).

import { get, writable, derived } from 'svelte/store';
import type { ProgressEvent } from '$lib/api/chat';
import type { AnalysisVersion } from '$lib/api/analysis';

// --- Fine events (SSE vivo) -------------------------------------------------

/** Etapa de análisis actual ({stage, message}). Null si no hay run activo. */
export const analysisStage = writable<ProgressEvent | null>(null);

/** Notificacion de análisis listo (versión CANDIDATE). Null hasta analysis.ready. */
export const analysisReady = writable<
  | {
      version: number;
      status: string;
      requirement_count: number;
      entities: number;
      relationships: number;
      adrs: number;
      sub_projects: number;
    }
  | null
>(null);

/** True mientras el subagente analysis-agent corre una generación. */
export const analysisRunning = writable<boolean>(false);

// --- Conteos en vivo (van llegando por fases) -------------------------------

export const liveEntityCount = writable<number>(0);
export const liveRelationshipCount = writable<number>(0);
export const liveAdrCount = writable<number>(0);
export const liveSubProjectCount = writable<number>(0);
export const liveProcessDiagramCount = writable<number>(0);

// --- Datos persistidos (cargados bajo demanda por la UI) -------------------

export const analysisVersions = writable<AnalysisVersion[]>([]);
export const activeAnalysisVersion = writable<number | null>(null);

// --- Derived (solo lectura) ------------------------------------------------

/** Progreso vivo agregado: null cuando no hay run activo o no hay stage. */
export const liveAnalysisProgress = derived(
  [analysisRunning, analysisStage],
  ([$running, $stage]) => ($running && $stage ? $stage : null)
);

// --- Mutadores --------------------------------------------------------------

/** Resetea el estado vivo (conserva los datos persistidos). */
export function resetAnalysisLive(): void {
  analysisStage.set(null);
  analysisReady.set(null);
  liveEntityCount.set(0);
  liveRelationshipCount.set(0);
  liveAdrCount.set(0);
  liveSubProjectCount.set(0);
  liveProcessDiagramCount.set(0);
}

/** Inicia un run de análisis: reset vivo + marca running. Idempotente. */
export function startAnalysis(): void {
  if (!get(analysisRunning)) {
    analysisStage.set(null);
    analysisReady.set(null);
    liveEntityCount.set(0);
    liveRelationshipCount.set(0);
    liveAdrCount.set(0);
    liveSubProjectCount.set(0);
    liveProcessDiagramCount.set(0);
  }
  analysisRunning.set(true);
}

/** Cierra el run: baja el flag running pero conserva conteos para UI. */
export function endAnalysis(): void {
  analysisRunning.set(false);
  analysisStage.set(null);
}

// --- Inyección de fine events (llamados por el chat store) ------------------

/** Handler de analysis.progress: auto-inicia el run en el primer evento. */
export function onAnalysisProgress(p: ProgressEvent): void {
  if (!get(analysisRunning)) startAnalysis();
  analysisStage.set(p);
}

/** Handler de analysis.mer_ready: fija conteo de entidades del MER. */
export function onAnalysisMerReady(e: {
  entities?: number;
  relationships?: number;
}): void {
  if (typeof e.entities === 'number') liveEntityCount.set(e.entities);
  if (typeof e.relationships === 'number')
    liveRelationshipCount.set(e.relationships);
}

/** Handler de analysis.nfr_ready: por ahora sin estado adicional. */
export function onAnalysisNfrReady(_e: unknown): void {
  // Los datos detallados del NFR se cargan bajo demanda desde la API.
}

/** Handler de analysis.process_ready: fija conteo de diagramas de proceso. */
export function onAnalysisProcessReady(e: {
  diagrams?: number;
}): void {
  if (typeof e.diagrams === 'number') liveProcessDiagramCount.set(e.diagrams);
}

/** Handler de analysis.adr_ready: fija conteo de ADRs. */
export function onAnalysisAdrReady(e: { adrs?: number }): void {
  if (typeof e.adrs === 'number') liveAdrCount.set(e.adrs);
}

/** Handler de analysis.subproject_ready: fija conteo de sub-proyectos. */
export function onAnalysisSubProjectReady(e: {
  sub_projects?: number;
}): void {
  if (typeof e.sub_projects === 'number')
    liveSubProjectCount.set(e.sub_projects);
}

/** Handler de analysis.ready: fija la versión generada y cierra el run. */
export function onAnalysisReady(r: {
  version: number;
  status: string;
  requirement_count: number;
  entities: number;
  relationships: number;
  adrs: number;
  sub_projects: number;
}): void {
  analysisReady.set(r);
  endAnalysis();
}

/** Devuelve los stores a su estado inicial (tests / logout). */
export function _resetAnalysisForTests(): void {
  resetAnalysisLive();
  analysisVersions.set([]);
  activeAnalysisVersion.set(null);
  analysisRunning.set(false);
}
