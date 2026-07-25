// Tests del store de tabs: LRU eviction al superar MAX_TABS, dirty no se
// evicta sin señal, closeTab borra correctamente. Store puro (TS), sin DOM.
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mockeamos el módulo de API para que openTab no dispare fetch real.
vi.mock('$lib/api/workspaces', () => ({
  getFile: vi.fn(async (path: string) => ({
    path,
    content: `content-for-${path}`
  })),
  putFile: vi.fn(async (path: string, _content: string) => ({
    path,
    ok: true
  }))
}));

import {
  openTabs,
  activeTabPath,
  openTab,
  closeTab,
  setActive,
  markDirty,
  saveActive,
  MAX_TABS,
  _resetTabsForTests
} from './tabs';

describe('tabs store', () => {
  beforeEach(() => {
    _resetTabsForTests();
    // Aislamos el store entre tests: limpiamos suscripciones activas
    // re-creando estado en cada beforeEach.
  });

  it('openTab abre una tab nueva y la activa', async () => {
    const ok = await openTab('docs/a.md');
    expect(ok).toBe(true);
    let list: typeof openTabs extends import('svelte/store').Writable<infer T> ? T : never = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(1);
    expect(list[0].path).toBe('docs/a.md');
    expect(list[0].name).toBe('a.md');
    let active = '';
    activeTabPath.subscribe((v) => (active = v ?? ''))();
    expect(active).toBe('docs/a.md');
  });

  it('openTab en path existente solo activa (no duplica)', async () => {
    await openTab('docs/a.md');
    await openTab('docs/a.md');
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(1);
  });

  it('closeTab remueve y reasigna foco al vecino', async () => {
    await openTab('docs/a.md');
    await openTab('docs/b.md');
    await openTab('docs/c.md'); // activa
    closeTab('docs/c.md');
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(2);
    let active = '';
    activeTabPath.subscribe((v) => (active = v ?? ''))();
    // Al cerrar el último activo, cae al anterior (idx - 1 en el orden original).
    expect(['docs/a.md', 'docs/b.md']).toContain(active);
  });

  it(`LRU eviction: al abrir la tab #${MAX_TABS + 1}, cae la menos recientemente activa`, async () => {
    // Abrir MAX_TABS tabs (todas clean).
    for (let i = 0; i < MAX_TABS; i++) {
      await openTab(`docs/f${i}.md`);
    }
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(MAX_TABS);

    // Reactivamos algunas para que f0 quede como la LRU.
    await setActive('docs/f1.md');
    await setActive('docs/f2.md');

    // Apertura extra dispara eviction.
    await openTab('docs/extra.md');

    list = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(MAX_TABS);
    const paths = list.map((t) => t.path);
    expect(paths).toContain('docs/extra.md');
    expect(paths).toContain('docs/f1.md');
    expect(paths).toContain('docs/f2.md');
    // f0 fue la LRU (no reactivada): debe haber sido eviccionada.
    expect(paths).not.toContain('docs/f0.md');
  });

  it('LRU eviction: tab dirty NO se evicta', async () => {
    for (let i = 0; i < MAX_TABS; i++) {
      await openTab(`docs/g${i}.md`);
    }
    // Marcar g0 como dirty → no es candidata a eviction.
    markDirty('docs/g0.md', 'unsaved');
    // Reactivamos todas menos g0 → g0 sería LRU pero está dirty.
    for (let i = 1; i < MAX_TABS; i++) {
      await setActive(`docs/g${i}.md`);
    }
    await openTab('docs/gextra.md');

    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    const paths = list.map((t) => t.path);
    // g0 sigue porque dirty no evicta.
    expect(paths).toContain('docs/g0.md');
    // Algo tuvo que salir (la LRU clean más vieja = g1).
    expect(list).toHaveLength(MAX_TABS);
    expect(paths).not.toContain('docs/g1.md');
  });

  it('markDirty marca la tab y saveActive limpia dirty', async () => {
    await openTab('docs/h.md');
    markDirty('docs/h.md', 'nuevo contenido');
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list[0].dirty).toBe(true);
    expect(list[0].content).toBe('nuevo contenido');

    const ok = await saveActive();
    expect(ok).toBe(true);
    list = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list[0].dirty).toBe(false);
  });
});
