import { describe, expect, it } from 'vitest';
import {
  applySelection,
  parseAtTrigger,
  parseMentionSegments
} from './atMention';

describe('parseAtTrigger', () => {
  it('detecta @ al inicio con su query', () => {
    expect(parseAtTrigger('@doc', 4)).toEqual({ start: 0, query: 'doc' });
    expect(parseAtTrigger('@', 1)).toEqual({ start: 0, query: '' });
  });

  it('detecta @ después de whitespace en cualquier posición del mensaje', () => {
    expect(parseAtTrigger('revisar @docs/sr', 16)).toEqual({
      start: 8,
      query: 'docs/sr'
    });
    expect(parseAtTrigger('a b@c\n@d', 8)).toEqual({ start: 6, query: 'd' });
  });

  it('no activa con @ pegado a otra palabra', () => {
    expect(parseAtTrigger('correo@dominio', 14)).toBeNull();
  });

  it('no activa si hay whitespace entre @ y el caret', () => {
    expect(parseAtTrigger('@do cu', 6)).toBeNull();
  });

  it('corta el query exactamente en el último espacio antes del caret', () => {
    // El escaneo hacia atrás encuentra primero el espacio → inactivo; pero si
    // el caret está ANTES del espacio sí queda activo.
    expect(parseAtTrigger('@docs srs.md', 5)).toEqual({ start: 0, query: 'docs' });
    expect(parseAtTrigger('@docs srs.md', 12)).toBeNull();
  });

  it('no activa cuando el caret quedó antes del @', () => {
    expect(parseAtTrigger('hola @docs', 3)).toBeNull();
  });

  it('caret fuera de rango se acota al largo del texto', () => {
    expect(parseAtTrigger('@doc', 99)).toEqual({ start: 0, query: 'doc' });
    expect(parseAtTrigger('', 0)).toBeNull();
  });
});

describe('applySelection', () => {
  it('reemplaza solo el segmento @query y agrega espacio final', () => {
    const out = applySelection('mira @doc', { start: 5, query: 'doc' }, 'docs/srs.md');
    expect(out.text).toBe('mira @docs/srs.md ');
    expect(out.caret).toBe('mira @docs/srs.md '.length);
  });

  it('conserva el resto del mensaje (antes y después)', () => {
    const out = applySelection(
      'pre @doc pos',
      { start: 4, query: 'doc' },
      'a/b.md'
    );
    expect(out.text).toBe('pre @a/b.md  pos');
  });

  it('soporta menciones múltiples consecutivas', () => {
    // Primera mención al inicio (texto vacío).
    let res = applySelection('', { start: 0, query: '' }, 'uno.md');
    let text = res.text; // '@uno.md '
    expect(res.caret).toBe(text.length);

    // El usuario teclea otro @ justo después y confirma la segunda selección.
    res = applySelection(`${text}@`, { start: text.length, query: '' }, 'dos/dos.md');
    expect(res.text).toBe('@uno.md @dos/dos.md ');
  });
});

describe('parseMentionSegments', () => {
  it('divide el texto en segmentos normales y menciones', () => {
    expect(parseMentionSegments('revisar @docs/srs.md y listo')).toEqual([
      { kind: 'text', value: 'revisar ' },
      { kind: 'mention', value: '@docs/srs.md' },
      { kind: 'text', value: ' y listo' }
    ]);
  });

  it('detecta la mención al inicio del texto', () => {
    expect(parseMentionSegments('@README.md')).toEqual([
      { kind: 'mention', value: '@README.md' }
    ]);
  });

  it('texto sin menciones queda como un único segmento', () => {
    expect(parseMentionSegments('hola mundo')).toEqual([
      { kind: 'text', value: 'hola mundo' }
    ]);
    expect(parseMentionSegments('')).toEqual([]);
  });

  it('no marca como mención un correo (sin ancla whitespace)', () => {
    expect(parseMentionSegments('escribir a contacto@mail.com')).toEqual([
      { kind: 'text', value: 'escribir a contacto@mail.com' }
    ]);
  });

  it("'@' suelto entre espacios queda como texto", () => {
    expect(parseMentionSegments('a @ b')).toEqual([
      { kind: 'text', value: 'a @ b' }
    ]);
  });

  it('la mención termina en salto de línea', () => {
    expect(parseMentionSegments('@docs\nrevisar esto')).toEqual([
      { kind: 'mention', value: '@docs' },
      { kind: 'text', value: '\nrevisar esto' }
    ]);
  });

  it('soporta varias menciones en el mismo mensaje', () => {
    expect(parseMentionSegments('@a.md más @b/c.png')).toEqual([
      { kind: 'mention', value: '@a.md' },
      { kind: 'text', value: ' más ' },
      { kind: 'mention', value: '@b/c.png' }
    ]);
  });
});
