// Smoke test del editor markdown: valida el contrato Tiptap que usa
// MarkdownEditor.svelte (mismas extensiones, contentType y getMarkdown).
// Se instancia Editor directo en jsdom (el componente es un wrapper de UI:
// toolbar + montaje); así no depende del mounting client-side de Svelte
// bajo vitest, que hoy resuelve la build server de svelte.
import { describe, it, expect, afterEach } from 'vitest';
import { Editor } from '@tiptap/core';
import { StarterKit } from '@tiptap/starter-kit';
import { Markdown } from '@tiptap/markdown';
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table';

const editors: Editor[] = [];

function makeEditor(md: string, onMd?: (s: string) => void): Editor {
  const e = new Editor({
    element: document.createElement('div'),
    extensions: [StarterKit, Markdown, Table, TableRow, TableHeader, TableCell],
    content: md,
    contentType: 'markdown',
    onUpdate: ({ editor }) => onMd?.(editor.getMarkdown())
  });
  editors.push(e);
  return e;
}

afterEach(() => {
  for (const e of editors.splice(0)) e.destroy();
});

describe('MarkdownEditor (contrato Tiptap)', () => {
  it('parsea markdown inicial a nodos ricos', () => {
    const e = makeEditor('# Título\n\nPárrafo con **negrita** y `código`.');
    const html = e.getHTML();
    expect(html).toContain('<h1');
    expect(html).toContain('<strong');
    expect(html).toContain('<code');
  });

  it('round-trip: getMarkdown serializa headings y negrita de vuelta', () => {
    const e = makeEditor('# Título\n\nTexto con **énfasis**.');
    const md = e.getMarkdown();
    expect(md).toContain('# ');
    expect(md).toContain('**');
  });

  it('round-trip: listas y citas se conservan', () => {
    const e = makeEditor('- uno\n- dos\n\n> cita');
    const md = e.getMarkdown();
    expect(md).toContain('- ');
    expect(md).toContain('>');
  });

  it('insertar tabla serializa a tabla markdown (pipes)', () => {
    const e = makeEditor('inicio');
    const ok = e
      .chain()
      .insertTable({ rows: 2, cols: 2, withHeaderRow: true })
      .run();
    expect(ok).toBe(true);
    const md = e.getMarkdown();
    expect(md).toContain('|');
    expect(md).toContain('---');
  });

  it('onUpdate dispara con markdown serializado al editar', () => {
    const got: string[] = [];
    const e = makeEditor('hola', (md) => got.push(md));
    e.commands.insertContent(' mundo');
    expect(got.length).toBeGreaterThan(0);
    expect(got[got.length - 1]).toContain('hola');
  });
});
