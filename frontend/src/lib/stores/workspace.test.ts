// Tests del store de workspace: normalizeNode maneja缺失children/type defaults.
import { describe, it, expect } from 'vitest';
import { normalizeNode, parentOf, humanizeWorkspaceError } from './workspace';

// --- refreshTree(silent): el polling no debe borrar errores visibles ------
// (bug de visibilidad: el poll borraba workspaceError cada 5s, ocultando
// errores de operaciones tras máximo 5 segundos.)
import { vi } from 'vitest';

vi.mock('$lib/api/workspaces', () => ({
  getTree: vi.fn(),
  createFolder: vi.fn(),
  createFile: vi.fn(),
  moveEntry: vi.fn(),
  copyEntry: vi.fn(),
  deleteEntry: vi.fn(),
  downloadFile: vi.fn()
}));
vi.mock('$lib/stores/tabs', () => ({
  closeTabsUnderPath: vi.fn(),
  retargetTabsUnderPath: vi.fn()
}));
vi.mock('$lib/stores/project', () => ({
  currentProjectId: { subscribe: (fn: (v: number | null) => void) => { fn(1); return () => {}; } }
}));

import { getTree, type TreeNode } from '$lib/api/workspaces';
import {
  workspaceError,
  refreshTree
} from './workspace';

describe('refreshTree silent (polling)', () => {
  it('silent=true no setea ni borra workspaceError (ni en éxito ni en fallo)', async () => {
    workspaceError.set('target_exists'); // error visible de una operación
    (getTree as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));

    await refreshTree('.', false, true);
    // fallo silencioso: el error de la operación sigue visible
    let err: string | null = null;
    workspaceError.subscribe((v) => (err = v))();
    expect(err).toBe('target_exists');

    (getTree as ReturnType<typeof vi.fn>).mockResolvedValue({
      name: '.', path: '.', type: 'dir', children: []
    } as unknown as TreeNode);
    await refreshTree('.', false, true);
    // éxito silencioso: tampoco lo borra
    workspaceError.subscribe((v) => (err = v))();
    expect(err).toBe('target_exists');
  });

  it('refresh sin silent limpia el error en éxito y lo setea en fallo', async () => {
    workspaceError.set('target_exists');
    (getTree as ReturnType<typeof vi.fn>).mockResolvedValue({
      name: '.', path: '.', type: 'dir', children: []
    } as unknown as TreeNode);
    await refreshTree();
    let err: string | null = 'x';
    workspaceError.subscribe((v) => (err = v))();
    expect(err).toBeNull();

    (getTree as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'));
    await refreshTree();
    workspaceError.subscribe((v) => (err = v))();
    expect(err).toBe('boom');
  });
});

describe('workspace store - normalizeNode', () => {
  it('file node sin children', () => {
    const r = normalizeNode({ name: 'a.md', path: 'docs/a.md', type: 'file' });
    expect(r.type).toBe('file');
    expect(r.children).toBeUndefined();
    expect(r.name).toBe('a.md');
    expect(r.path).toBe('docs/a.md');
  });

  it('dir node con children', () => {
    const r = normalizeNode({
      name: 'docs',
      path: 'docs',
      type: 'dir',
      children: [
        { name: 'a.md', path: 'docs/a.md', type: 'file' }
      ]
    });
    expect(r.type).toBe('dir');
    expect(r.children).toHaveLength(1);
    expect(r.children![0].name).toBe('a.md');
  });

  it('type faltante → default file', () => {
    const r = normalizeNode({ name: 'x.md', path: 'x.md' });
    expect(r.type).toBe('file');
  });

  it('type unknown → default file', () => {
    const r = normalizeNode({ name: 'x.md', path: 'x.md', type: 'symlink' } as unknown as any);
    expect(r.type).toBe('file');
  });

  it('dir sin children → children=[]', () => {
    const r = normalizeNode({ name: 'empty', path: 'empty', type: 'dir' });
    expect(r.type).toBe('dir');
    expect(r.children).toEqual([]);
  });

  it('dir con children no-array → children=[]', () => {
    const r = normalizeNode({
      name: 'x',
      path: 'x',
      type: 'dir',
      children: 'not-an-array'
    } as unknown as any);
    expect(r.children).toEqual([]);
  });

  it('null/undefined input → nodo file vacío', () => {
    const r1 = normalizeNode(null as unknown as any);
    const r2 = normalizeNode(undefined as unknown as any);
    expect(r1.type).toBe('file');
    expect(r2.type).toBe('file');
  });

  it('name/path missing → string vacía', () => {
    const r = normalizeNode({});
    expect(r.name).toBe('');
    expect(r.path).toBe('');
  });

  it('recursividad: normalize hijos también', () => {
    const r = normalizeNode({
      name: 'root',
      path: '.',
      type: 'dir',
      children: [
        { name: 'a', path: 'a', type: 'dir', children: [
          { name: 'b.md', path: 'a/b.md', type: 'file' }
        ]},
        // Hijo con type malicio/missing debe normalizarse también.
        { name: 'c.md', path: 'c.md' }
      ]
    });
    expect(r.children!.length).toBe(2);
    expect(r.children![0].children![0].name).toBe('b.md');
    expect(r.children![1].type).toBe('file');
  });
});

describe('parentOf', () => {
  it("top-level → '.'", () => {
    expect(parentOf('a.md')).toBe('.');
  });

  it('primer nivel', () => {
    expect(parentOf('docs/a.md')).toBe('docs');
  });

  it('anidado', () => {
    expect(parentOf('docs/sub/a.md')).toBe('docs/sub');
  });
});

describe('humanizeWorkspaceError', () => {
  it('mapea códigos conocidos a español', () => {
    expect(humanizeWorkspaceError('target_exists')).toBe(
      'Ya existe un elemento con ese nombre en el destino'
    );
    expect(humanizeWorkspaceError('is_root')).toBe(
      'La raíz del workspace no se puede modificar'
    );
  });

  it('código desconocido se muestra tal cual', () => {
    expect(humanizeWorkspaceError('weird_code')).toBe('weird_code');
  });
});
