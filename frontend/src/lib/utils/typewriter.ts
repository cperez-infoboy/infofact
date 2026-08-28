// Ritmo del reveal typewriter: util pura de pacing, sin estado.
//
// El reveal progresivo pinta el texto en streaming con retardo controlado:
// `revealStep(backlog)` devuelve cuántos caracteres avanzar por tick en
// función del backlog (caracteres recibidos y todavía no revelados).
//
// - Backlog chico (streaming real, deltas de a pocos tokens): paso mínimo;
//   el reveal sigue de cerca al ritmo de llegada.
// - Backlog grande (ráfaga: respuesta completa en un solo frame, delta
//   grande del relay): paso proporcional para drenar en ~10 ticks en vez
//   de arrastrarse a paso fijo.

/** Intervalo del reveal en ms: corto para verse fluido, sin llegar a un
 *  reveal por frame de render. */
export const REVEAL_TICK_MS = 24;

/** Caracteres a revelar en el próximo tick según el backlog pendiente. */
export function revealStep(backlog: number): number {
  if (backlog <= 0) return 0;
  return Math.max(2, Math.ceil(backlog / 10));
}
