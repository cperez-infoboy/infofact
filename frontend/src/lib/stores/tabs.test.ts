// Tests del store de tabs: LRU eviction al superar MAX_TABS, dirty no se
// evicta, closeTab reasigna foco, y openView crea tabs singleton sticky.
// Store puro (TS), sin DOM.
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mockeamos el módulo de API para que openTab no dispare fetch real.
vi.mock('$lib/api/workspaces', () => ({
  getFile: vi.fn(async (_projectId: number, path: string) => ({
    path,
    content: `content-for-${path}`
  })),
  putFile: vi.fn(async (_projectId: number, _path: string, _content: string) => ({
    ok: true
  }))
}));

import {
  openTabs,
  activeTabId,
  openTab,
  openView,
  closeTab,
  setActive,
  markDirty,
  saveActive,
  MAX_TABS,
  _resetTabsForTests
} from './tabs';
import { currentProject } from '$lib/stores/project';

describe('tabs store', () => {
  beforeEach(() => {
    _resetTabsForTests();
    // openTab requiere un proyecto activo (currentProjectId derive != null).
    currentProject.set({
      id: 1,
      name: 'test',
      slug: 'test',
      phase: 'requirements',
      created_at: '',
      sessions: []
    } as any);
  });

  it('openTab abre una tab nueva y la activa', async () => {
    const ok = await openTab('docs/a.md');
    expect(ok).toBe(true);
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(1);
    expect(list[0].kind).toBe('file');
    expect(list[0].path).toBe('docs/a.md');
    expect(list[0].name).toBe('a.md');
    let active = '';
    activeTabId.subscribe((v) => (active = v ?? ''))();
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
    activeTabId.subscribe((v) => (active = v ?? ''))();
    expect(['docs/a.md', 'docs/b.md']).toContain(active);
  });

  it(`LRU eviction: al abrir la tab #${MAX_TABS + 1}, cae la menos recientemente activa`, async () => {
    for (let i = 0; i < MAX_TABS; i++) {
      await openTab(`docs/f${i}.md`);
    }
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(MAX_TABS);

    await setActive('docs/f1.md');
    await setActive('docs/f2.md');
    await openTab('docs/extra.md');

    list = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(MAX_TABS);
    const paths = list.map((t) => t.path);
    expect(paths).toContain('docs/extra.md');
    expect(paths).toContain('docs/f1.md');
    expect(paths).toContain('docs/f2.md');
    expect(paths).not.toContain('docs/f0.md');
  });

  it('LRU eviction: tab dirty NO se evicta', async () => {
    for (let i = 0; i < MAX_TABS; i++) {
      await openTab(`docs/g${i}.md`);
    }
    markDirty('docs/g0.md', 'unsaved');
    for (let i = 1; i < MAX_TABS; i++) {
      await setActive(`docs/g${i}.md`);
    }
    await openTab('docs/gextra.md');

    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    const paths = list.map((t) => t.path);
    expect(paths).toContain('docs/g0.md');
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

  it('openView crea un tab singleton por kind y lo activa', async () => {
    await openView('requirements');
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list).toHaveLength(1);
    expect(list[0].kind).toBe('requirements');
    expect(list[0].id).toBe('requirements');
    expect(list[0].name).toBe('Requerimientos');
    let active = '';
    activeTabId.subscribe((v) => (active = v ?? ''))();
    expect(active).toBe('requirements');
  });

  it('openView no duplica: si el tab existe, solo lo activa', async () => {
    await openView('grouping');
    // Movemos el foco a un archivo para que la vista deje de ser la activa.
    await openTab('docs/x.md');
    await openView('grouping');
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list.filter((t) => t.kind === 'grouping')).toHaveLength(1);
    let active = '';
    activeTabId.subscribe((v) => (active = v ?? ''))();
    expect(active).toBe('grouping');
  });

  it('openView tabs son sticky: no se evictan por LRU', async () => {
    await openView('requirements');
    for (let i = 0; i < MAX_TABS + 2; i++) {
      await openTab(`docs/v${i}.md`);
    }
    let list: any[] = [];
    openTabs.subscribe((v) => (list = v))();
    expect(list.filter((t) => t.kind === 'requirements')).toHaveLength(1);
    expect(list.length).toBeLessThanOrEqual(MAX_TABS);
  });
});
