// Tests del ritmo del reveal typewriter (util pura, sin estado).
import { describe, it, expect } from 'vitest';
import { revealStep, REVEAL_TICK_MS } from './typewriter';

describe('revealStep', () => {
  it('backlog vacío o negativo no revela nada', () => {
    expect(revealStep(0)).toBe(0);
    expect(revealStep(-5)).toBe(0);
  });

  it('backlog chico revela al menos 2 chars por tick (sigue al stream)', () => {
    expect(revealStep(1)).toBe(2);
    expect(revealStep(3)).toBe(2);
    expect(revealStep(20)).toBe(2);
  });

  it('backlog grande drena proporcional (~10 ticks)', () => {
    // Una ráfaga de 5000 chars (respuesta completa en un solo frame,
    // comportamiento pre-streaming) se revela en ~10 ticks en vez de
    // arrastrarse a paso fijo.
    expect(revealStep(5000)).toBe(500);
  });

  it('es monótono: más backlog nunca revela menos', () => {
    expect(revealStep(100)).toBeLessThanOrEqual(revealStep(1000));
    expect(revealStep(1000)).toBeLessThanOrEqual(revealStep(10000));
  });
});

describe('REVEAL_TICK_MS', () => {
  it('tick corto pero no por frame de render', () => {
    expect(REVEAL_TICK_MS).toBeGreaterThanOrEqual(16);
    expect(REVEAL_TICK_MS).toBeLessThanOrEqual(50);
  });
});
