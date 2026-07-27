// Cliente SSE del chat. Usa fetch + ReadableStream: NO EventSource (no soporta
// POST ni headers custom). Parsea el stream SSE manualmente.
//
// Protocolo (backend/routers/chat.py):
//   event: token\ndata: {"delta": "..."}
//   event: tool_start\ndata: {"name": "...", "input": {...}}
//   event: tool_end\ndata: {"name": "...", "output": "..."}
//   event: completed\ndata: {}
//   event: failed\ndata: {"error": "..."}
//   event: extraction.progress\ndata: {"stage": "...", "message": "..."}
//   event: conflict.found\ndata: {kind:"duplicate"|"contradiction", ...}
//   event: validation.report\ndata: {total, kept, rejected, flagged}
//   event: requirement.added\ndata: {code, statement, type, priority, ...}

export interface ToolInput {
  [key: string]: unknown;
}

/** Coarse pipeline stage (extraction.progress). */
export interface ProgressEvent {
  stage: string;
  message: string;
}

/** Fine event: one per duplicate or contradiction (conflict.found). */
export interface ConflictDuplicate {
  kind: 'duplicate';
  kept_id: string;
  member_ids: string[];
  kept_statement: string;
}
export interface ConflictContradiction {
  kind: 'contradiction';
  a_id: string;
  b_id: string;
  reason: string;
  confidence: number;
}
export type ConflictEvent = ConflictDuplicate | ConflictContradiction;

/** Fine event: post-critique summary (validation.report). */
export interface ValidationReport {
  total: number;
  kept: number;
  rejected: number;
  flagged: number;
}

/** Fine event: one per persisted row (requirement.added). */
export interface RequirementAdded {
  code: string;
  statement: string;
  type: string;
  priority: string;
  explicit: boolean;
  derived: boolean;
  confidence: number;
  span_verified: boolean;
  source: unknown;
  parent_code?: string;
}

export interface StreamHandlers {
  onToken?: (delta: string) => void;
  onToolStart?: (name: string, input: ToolInput) => void;
  onToolEnd?: (name: string, output: string) => void;
  onProgress?: (p: ProgressEvent) => void;
  onConflict?: (c: ConflictEvent) => void;
  onValidationReport?: (r: ValidationReport) => void;
  onRequirementAdded?: (r: RequirementAdded) => void;
  onCompleted?: () => void;
  onFailed?: (error: string) => void;
}

interface ParsedEvent {
  event: string;
  data: string;
}

/**
 * Parsea un buffer SSE en eventos completos. Cada evento termina con `\n\n`.
 * Devuelve los eventos parseados y el resto del buffer (sin terminator `\n\n`).
 *
 * Un evento puede tener varias líneas `data:` — la spec SSE las concatena con
 * `\n`. Aquí solo usamos una línea `data:` por evento, pero lo soportamos.
 */
export function parseSseEvents(buffer: string): {
  events: ParsedEvent[];
  remainder: string;
} {
  const events: ParsedEvent[] = [];
  let remainder = buffer;

  while (true) {
    const idx = remainder.indexOf('\n\n');
    if (idx === -1) break;

    const raw = remainder.slice(0, idx);
    remainder = remainder.slice(idx + 2);

    let event = 'message';
    const dataLines: string[] = [];

    for (const line of raw.split('\n')) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
      // Ignoramos comentarios (líneas que empiezan con `:`) y líneas vacías.
    }

    if (dataLines.length > 0) {
      events.push({ event, data: dataLines.join('\n') });
    }
  }

  return { events, remainder };
}

/**
 * Inicia el stream SSE de un mensaje. Devuelve un AbortController para
 * cancelar (botón Cancel del frontend).
 *
 * TRAMPA: la cookie JWT viaja vía `credentials: 'include'`. Sin eso, 401.
 */
export async function streamMessage(
  sessionId: number,
  content: string,
  handlers: StreamHandlers
): Promise<AbortController> {
  const controller = new AbortController();

  const res = await fetch(`/api/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
    signal: controller.signal
  });

  if (!res.ok || !res.body) {
    let code = 'request_failed';
    try {
      const body = await res.json();
      code = body.detail ?? code;
    } catch {
      // Respuesta no-JSON.
    }
    handlers.onFailed?.(code);
    return controller;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseEvents(buffer);
      buffer = parsed.remainder;

      for (const ev of parsed.events) {
        dispatchEvent(ev, handlers);
      }
    }
    // Flush final por si quedó un evento sin `\n\n` (no debería, pero defensivo).
    if (buffer.trim()) {
      const parsed = parseSseEvents(buffer + '\n\n');
      for (const ev of parsed.events) {
        dispatchEvent(ev, handlers);
      }
    }
  } catch (err) {
    if ((err as Error).name === 'AbortError') {
      // Cancelado por el usuario: no propagar como error.
      return controller;
    }
    handlers.onFailed?.((err as Error).message || 'stream_error');
  }

  return controller;
}

function dispatchEvent(ev: ParsedEvent, handlers: StreamHandlers): void {
  // DEBUG: ver todos los eventos SSE que llegan del backend.
  // Quitar tras diagnosticar el flujo del subagente.
  console.log('[sse] event', ev.event, ev.data);
  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(ev.data);
  } catch {
    // Payload inválido: ignoramos el evento.
    return;
  }

  switch (ev.event) {
    case 'token':
      if (typeof payload.delta === 'string') {
        handlers.onToken?.(payload.delta);
      }
      break;
    case 'tool_start':
      handlers.onToolStart?.(
        (payload.name as string) ?? 'unknown',
        (payload.input as ToolInput) ?? {}
      );
      break;
    case 'tool_end':
      handlers.onToolEnd?.(
        (payload.name as string) ?? 'unknown',
        (payload.output as string) ?? ''
      );
      break;
    case 'extraction.progress':
      handlers.onProgress?.({
        stage: (payload.stage as string) ?? '',
        message: (payload.message as string) ?? '',
      });
      break;
    case 'conflict.found':
      handlers.onConflict?.(payload as unknown as ConflictEvent);
      break;
    case 'validation.report':
      handlers.onValidationReport?.({
        total: Number(payload.total ?? 0),
        kept: Number(payload.kept ?? 0),
        rejected: Number(payload.rejected ?? 0),
        flagged: Number(payload.flagged ?? 0),
      });
      break;
    case 'requirement.added':
      handlers.onRequirementAdded?.(payload as unknown as RequirementAdded);
      break;
    case 'completed':
      handlers.onCompleted?.();
      break;
    case 'failed':
      handlers.onFailed?.((payload.error as string) ?? 'unknown_error');
      break;
  }
}
