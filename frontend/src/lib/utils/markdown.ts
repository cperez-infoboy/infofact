// Renderizado de Markdown → HTML para contenido producido por el agente.
// Usa `marked` para parsear y `DOMPurify` para sanitizar el HTML resultante
// (el output del LLM no es de confianza: puede incluir tags crudos o
// atributos peligrosos). `breaks: true` convierte saltos de línea sueltos
// en <br>, más natural para chat.
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { browser } from '$app/environment';

marked.setOptions({
  breaks: true,
  gfm: true
});

// DOMPurify solo funciona en el navegador (depende de DOM). En SSR devolvemos
// una cadena vacía porque este renderer solo se invoca dentro de componentes
// client-side del chat.
export function renderMarkdown(src: string): string {
  if (!browser) return '';
  const raw = marked.parse(src ?? '', { async: false }) as string;
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'del', 'code', 'pre',
      'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'a', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span'
    ],
    ALLOWED_ATTR: ['href', 'title', 'target', 'rel']
  });
}
