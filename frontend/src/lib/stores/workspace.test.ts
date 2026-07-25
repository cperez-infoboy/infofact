// Tests del store de workspace: normalizeNode maneja缺失children/type defaults.
import { describe, it, expect } from 'vitest';
import { normalizeNode } from './workspace';

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
