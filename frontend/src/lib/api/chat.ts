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
//   event: srs.progress\ndata: {"stage": "...", "message": "..."}
//   event: quality.found\ndata: {rule_id, severity, dimension, message}
//   event: goal.inferred\ndata: {code, kind, statement, status}
//   event: coverage.report\ndata: {total, functional, nfr, gaps_25010, ...}
//   event: srs.ready\ndata: {version, status, requirement_count, ...}
//   event: analysis.progress\ndata: {"stage": "...", "message": "..."}
//   event: analysis.mer_ready\ndata: {entities, relationships}
//   event: analysis.nfr_ready\ndata: {...}
//   event: analysis.process_ready\ndata: {diagrams}
//   event: analysis.adr_ready\ndata: {adrs}
//   event: analysis.subproject_ready\ndata: {sub_projects}
//   event: analysis.ready\ndata: {version, status, requirement_count, entities, relationships, adrs, sub_projects}

export interface ToolInput {
  [key: string]: unknown;
}

/** Coarse pipeline stage (extraction.progress).
 *
 *  Payload de profiling opcional (wall-clock por etapa):
 *  - phase: "start" | "end" marca el límite de una etapa.
 *  - elapsed_ms: wall-clock de la etapa, en phase:"end".
 *  - timings: dict completo por etapa (ms), sólo en stage:"done".
 *  - total_ms: suma de timings, sólo en stage:"done".
 */
export interface ProgressEvent {
  stage: string;
  message: string;
  phase?: string;
  elapsed_ms?: number;
  timings?: Record<string, number>;
  total_ms?: number;
  current?: number;
  total?: number;
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

/** Fine event del subagente srs-agent: un hallazgo accionable (quality.found). */
export interface QualityFoundEvent {
  rule_id: string;
  severity: string;
  dimension: string;
  message: string;
}

/** Fine event: un goal inferido (goal.inferred). */
export interface GoalInferredEvent {
  code: string;
  kind: string;
  statement: string;
  status: string;
}

/** Fine event: reporte de cobertura en vivo (coverage.report). */
export interface CoverageReportEvent {
  total: number;
  functional: number;
  nfr: number;
  gaps_25010: string[];
  unrealized_goals: unknown[];
  unmitigated_obstacles: unknown[];
}

/** Fine event: SRS candidato listo (srs.ready). */
export interface SrsReadyEvent {
  version: number;
  status: string;
  requirement_count: number;
  findings: number;
  blockers: number;
  goals: number;
}

/** Fine event: MER listo (analysis.mer_ready). */
export interface AnalysisMerReadyEvent {
  entities?: number;
  relationships?: number;
}

/** Fine event: NFR listo (analysis.nfr_ready). Payload opaco por ahora. */
export interface AnalysisNfrReadyEvent {
  [key: string]: unknown;
}

/** Fine event: diagramas de proceso listos (analysis.process_ready). */
export interface AnalysisProcessReadyEvent {
  diagrams?: number;
}

/** Fine event: ADRs listos (analysis.adr_ready). */
export interface AnalysisAdrReadyEvent {
  adrs?: number;
}

/** Fine event: sub-proyectos listos (analysis.subproject_ready). */
export interface AnalysisSubProjectReadyEvent {
  sub_projects?: number;
}

/** Fine event: análisis candidato listo (analysis.ready). */
export interface AnalysisReadyEvent {
  version: number;
  status: string;
  requirement_count: number;
  entities: number;
  relationships: number;
  adrs: number;
  sub_projects: number;
}

export interface StreamHandlers {
  onToken?: (delta: string) => void;
  onToolStart?: (name: string, input: ToolInput) => void;
  onToolEnd?: (name: string, output: string) => void;
  onProgress?: (p: ProgressEvent) => void;
  onConflict?: (c: ConflictEvent) => void;
  onValidationReport?: (r: ValidationReport) => void;
  onRequirementAdded?: (r: RequirementAdded) => void;
  // Fine events del subagente srs-agent (comando /srs).
  onSrsProgress?: (p: ProgressEvent) => void;
  onQualityFound?: (f: QualityFoundEvent) => void;
  onGoalInferred?: (g: GoalInferredEvent) => void;
  onCoverageReport?: (c: CoverageReportEvent) => void;
  onSrsReady?: (r: SrsReadyEvent) => void;
  // Fine events del subagente analysis-agent (Phase 2: Analysis & Design).
  onAnalysisProgress?: (p: ProgressEvent) => void;
  onAnalysisMerReady?: (e: AnalysisMerReadyEvent) => void;
  onAnalysisNfrReady?: (e: AnalysisNfrReadyEvent) => void;
  onAnalysisProcessReady?: (e: AnalysisProcessReadyEvent) => void;
  onAnalysisAdrReady?: (e: AnalysisAdrReadyEvent) => void;
  onAnalysisSubProjectReady?: (e: AnalysisSubProjectReadyEvent) => void;
  onAnalysisReady?: (r: AnalysisReadyEvent) => void;
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
        phase: typeof payload.phase === 'string' ? payload.phase : undefined,
        elapsed_ms:
          typeof payload.elapsed_ms === 'number' ? payload.elapsed_ms : undefined,
        timings:
          payload.timings && typeof payload.timings === 'object'
            ? (payload.timings as Record<string, number>)
            : undefined,
        total_ms:
          typeof payload.total_ms === 'number' ? payload.total_ms : undefined,
        current:
          typeof payload.current === 'number' ? payload.current : undefined,
        total:
          typeof payload.total === 'number' ? payload.total : undefined,
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
    case 'srs.progress':
      handlers.onSrsProgress?.({
        stage: (payload.stage as string) ?? '',
        message: (payload.message as string) ?? '',
      });
      break;
    case 'quality.found':
      handlers.onQualityFound?.(payload as unknown as QualityFoundEvent);
      break;
    case 'goal.inferred':
      handlers.onGoalInferred?.(payload as unknown as GoalInferredEvent);
      break;
    case 'coverage.report':
      handlers.onCoverageReport?.(payload as unknown as CoverageReportEvent);
      break;
    case 'srs.ready':
      handlers.onSrsReady?.(payload as unknown as SrsReadyEvent);
      break;
    case 'analysis.progress':
      handlers.onAnalysisProgress?.({
        stage: (payload.stage as string) ?? '',
        message: (payload.message as string) ?? '',
      });
      break;
    case 'analysis.mer_ready':
      handlers.onAnalysisMerReady?.(
        payload as unknown as AnalysisMerReadyEvent
      );
      break;
    case 'analysis.nfr_ready':
      handlers.onAnalysisNfrReady?.(
        payload as unknown as AnalysisNfrReadyEvent
      );
      break;
    case 'analysis.process_ready':
      handlers.onAnalysisProcessReady?.(
        payload as unknown as AnalysisProcessReadyEvent
      );
      break;
    case 'analysis.adr_ready':
      handlers.onAnalysisAdrReady?.(
        payload as unknown as AnalysisAdrReadyEvent
      );
      break;
    case 'analysis.subproject_ready':
      handlers.onAnalysisSubProjectReady?.(
        payload as unknown as AnalysisSubProjectReadyEvent
      );
      break;
    case 'analysis.ready':
      handlers.onAnalysisReady?.(payload as unknown as AnalysisReadyEvent);
      break;
    case 'completed':
      handlers.onCompleted?.();
      break;
    case 'failed':
      handlers.onFailed?.((payload.error as string) ?? 'unknown_error');
      break;
  }
}
