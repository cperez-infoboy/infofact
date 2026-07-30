// Estado vivo del pipeline de captura de requerimientos.
// Archivo .ts PLANO → writable/derived (NO runes; ver CLAUDE.md decision 6).
//
// Alimenta al banner de progreso y al panel de hallazgos en ChatPanel.
// Los eventos los inyecta el chat store sendMessage via los handlers
// onProgress / onConflict / onValidationReport / onRequirementAdded, que
// a su vez vienen del canal SSE custom del backend (ver chat.ts api).

import { writable, derived } from 'svelte/store';
import type {
  ProgressEvent,
  ConflictEvent,
  ValidationReport,
  RequirementAdded
} from '$lib/api/chat';

// --- Stores base ------------------------------------------------------------

/** Etapa coarse actual ({stage, message}). Null si el pipeline no está activo. */
export const captureStage = writable<ProgressEvent | null>(null);

/** Lista de conflictos (duplicados + contradicciones) detectados en vivo. */
export const conflicts = writable<ConflictEvent[]>([]);

/** Reporte de validación post-crítica. Null hasta que termine la etapa 4. */
export const validationReport = writable<ValidationReport | null>(null);

/** Requerimientos persistidos, en orden de llegada. */
export const addedRequirements = writable<RequirementAdded[]>([]);

/** True mientras corre la tool run_requirements_capture. */
export const captureRunning = writable<boolean>(false);

/** Wall-clock (ms) por etapa. Se llena con los eventos phase:"end" y se
 *  reemplaza con el dict completo al llegar stage:"done". */
export const captureTimings = writable<Record<string, number>>({});

/** Suma de captureTimings (ms); llega en el evento stage:"done". */
export const captureTotalMs = writable<number>(0);

// --- Derived (sólo lectura) -------------------------------------------------

export const addedCount = derived(addedRequirements, ($a) => $a.length);

export const conflictCount = derived(conflicts, ($c) => $c.length);

export const duplicateCount = derived(
  conflicts,
  ($c) => $c.filter((x) => x.kind === 'duplicate').length
);

export const contradictionCount = derived(
  conflicts,
  ($c) => $c.filter((x) => x.kind === 'contradiction').length
);

// --- Mutadores --------------------------------------------------------------

/** Reset completo. Llamar antes de iniciar una captura nueva. */
export function resetCapture(): void {
  captureStage.set(null);
  conflicts.set([]);
  validationReport.set(null);
  addedRequirements.set([]);
  captureRunning.set(false);
  captureTimings.set({});
  captureTotalMs.set(0);
}

/** Inicia una captura: reset + marca running. */
export function startCapture(): void {
  resetCapture();
  captureRunning.set(true);
}

/** Finaliza la captura: deja running=false pero conserva el estado para UI. */
export function endCapture(): void {
  captureRunning.set(false);
  captureStage.set(null);
}

/** Devuelve los stores a su estado inicial (tests / logout). */
export function _resetCaptureForTests(): void {
  resetCapture();
}
