// Utilidades para la mención de archivos en el chat ("@ruta").
//
// Parseo puro del contenido del textarea: dado el texto y la posición del
// caret decide si hay una mención activa y cómo insertar la selección. Sin
// dependencias de DOM → testeable con vitest.

export interface AtTrigger {
  /** Índice donde empieza el '@'. */
  start: number;
  /** Texto tecleado después del '@' (sin whitespace: el trigger corta ahí). */
  query: string;
}

/**
 * Detecta una mención activa en `text` con el cursor en la posición `caret`.
 *
 * El '@' debe estar al inicio del texto o precedido por whitespace, y entre
 * él y el caret no puede haber whitespace (un espacio cancela el trigger).
 * Devuelve null si no hay mención activa.
 *
 * Ejemplos:
 *   parseAtTrigger("@doc", 4)          → { start: 0, query: "doc" }
 *   parseAtTrigger("mirar @doc", 10)   → { start: 6, query: "doc" }
 *   parseAtTrigger("correo@x", 8)      → null ('@' precedido por letra)
 *   parseAtTrigger("@do cu", 6)        → null (hay un espacio en el query)
 */
export function parseAtTrigger(text: string, caret: number): AtTrigger | null {
  const upToCaret = text.slice(0, Math.max(0, Math.min(caret, text.length)));
  let at = -1;
  // Escaneo hacia atrás desde el caret: si aparece whitespace antes que el
  // '@', no hay segmento de mención vivo.
  for (let i = upToCaret.length - 1; i >= 0; i--) {
    const ch = upToCaret[i];
    if (ch === '@') {
      at = i;
      break;
    }
    if (/\s/.test(ch)) return null;
  }
  if (at === -1) return null;
  // Ancla válida: inicio del texto o precedido por whitespace.
  if (at > 0 && !/\s/.test(upToCaret[at - 1])) return null;
  return { start: at, query: upToCaret.slice(at + 1) };
}

/**
 * Reemplaza el segmento `@query` apuntado por `trigger` por el token
 * `@selectedPath` seguido de un espacio, y devuelve el nuevo texto junto
 * con la posición final del caret.
 */
export function applySelection(
  text: string,
  trigger: AtTrigger,
  selectedPath: string
): { text: string; caret: number } {
  const token = `@${selectedPath} `;
  const before = text.slice(0, trigger.start);
  const after = text.slice(trigger.start + 1 + trigger.query.length);
  return { text: `${before}${token}${after}`, caret: before.length + token.length };
}

/** Segmento del mensaje para el resaltado visual de menciones. */
export type MentionSegment =
  | { kind: 'text'; value: string }
  | { kind: 'mention'; value: string };

/**
 * Divide `text` en segmentos normales y menciones "@ruta", para repintar
 * las menciones con un marco en la capa espejo del textarea.
 *
 * Misma regla de anclaje que `parseAtTrigger`: el '@' debe estar al inicio
 * o precedido por whitespace, y el token dura hasta el próximo whitespace.
 * Un '@' suelto ("a @ b") o pegado a una palabra (correo) queda como texto.
 */
export function parseMentionSegments(text: string): MentionSegment[] {
  const segments: MentionSegment[] = [];
  let buffer = '';
  const flushText = () => {
    if (buffer !== '') {
      segments.push({ kind: 'text', value: buffer });
      buffer = '';
    }
  };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    const anchored = i === 0 || /\s/.test(text[i - 1]);
    if (ch === '@' && anchored) {
      let j = i + 1;
      while (j < text.length && !/\s/.test(text[j])) j++;
      // j > i + 1 → hay algo después del '@' ('@' solo no es mención).
      if (j > i + 1) {
        flushText();
        segments.push({ kind: 'mention', value: text.slice(i, j) });
        i = j;
        continue;
      }
    }
    buffer += ch;
    i += 1;
  }
  flushText();
  return segments;
}
