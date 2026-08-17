// Estado vivo del /agrupar (ruta directa del comando /agrupar).
// Archivo .ts PLANO → writable (NO runes; ver CLAUDE.md decisión 6).
//
// Alimenta al banner GroupingStatus.svelte. Los eventos los inyecta el chat
// store via onGroupingProgress / onGroupingDone, que llegan del canal SSE
// grouping.progress / grouping.ready del backend.
//
// Patrón de los stores de srs/analysis: el run AUTO-INICIA con el primer
// evento de progreso (no hay tool name que lo dispare) y cierra con el
// stage:"done" del progreso o con grouping.ready.
import { get, writable } from 'svelte/store';
import type { GroupingReadyEvent, ProgressEvent } from '$lib/api/chat';

// --- Stores base ------------------------------------------------------------

/** Etapa actual ({stage, message, current?, total?}). Null si no hay run. */
export const groupingStage = writable<ProgressEvent | null>(null);

/** True mientras corre la revisión de duplicados. */
export const groupingRunning = writable<boolean>(false);

/** Wall-clock (ms) por etapa; se llena con los eventos phase:"end" y se
 *  reemplaza con el dict completo al llegar stage:"done". */
export const groupingTimings = writable<Record<string, number>>({});

/** Suma de groupingTimings (ms); llega en el evento stage:"done". */
export const groupingTotalMs = writable<number>(0);

/** Resultado del último run (grouping.ready). Persiste como banner residual. */
export const groupingResult = writable<GroupingReadyEvent | null>(null);

// --- Mutadores --------------------------------------------------------------

/** Reset completo. Llamar antes de iniciar una revisión nueva. */
export function resetGrouping(): void {
  groupingStage.set(null);
  groupingRunning.set(false);
  groupingTimings.set({});
  groupingTotalMs.set(0);
  groupingResult.set(null);
}

/** Evento grouping.progress: etapa/lote. Auto-inicia el run en el primero. */
export function onGroupingProgress(p: ProgressEvent): void {
  if (!get(groupingRunning)) {
    // Run nuevo: limpiar el residual del anterior.
    groupingTimings.set({});
    groupingTotalMs.set(0);
    groupingResult.set(null);
    groupingRunning.set(true);
  }
  groupingStage.set(p);
  if (p.phase === 'end' && typeof p.elapsed_ms === 'number') {
    // Local const: mantener el narrowing de p.elapsed_ms en el spread.
    const elapsed = p.elapsed_ms;
    groupingTimings.update((t) => ({ ...t, [p.stage]: elapsed }));
  }
  if (p.stage === 'done' && p.timings) {
    groupingTimings.set(p.timings);
    if (typeof p.total_ms === 'number') groupingTotalMs.set(p.total_ms);
    groupingRunning.set(false);
  }
}

/** grouping.ready: plan persistido; cierra el run y deja el residual. */
export function onGroupingDone(e: GroupingReadyEvent): void {
  groupingResult.set(e);
  groupingRunning.set(false);
  groupingStage.set(null);
}

/** Fin del stream sin plan (fallo o cancel): bajar la bandera sin perder
 *  lo acumulado (los timings ya recogidos siguen visibles). */
export function endGrouping(): void {
  groupingRunning.set(false);
}

/** Devuelve los stores a su estado inicial (tests / logout). */
export function _resetGroupingForTests(): void {
  resetGrouping();
}
