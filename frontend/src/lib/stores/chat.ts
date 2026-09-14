// Store del chat: mensajes por sesión + stream SSE + cancel.
// Archivo .ts PLANO → writable de svelte/store.
import { writable } from 'svelte/store';
import {
  streamMessage,
  type RelayTruncatedEvent,
  type ToolInput
} from '$lib/api/chat';
import type { MessageOut } from '$lib/api/projects';
import {
  captureStage,
  captureTimings,
  captureTotalMs,
  conflicts,
  validationReport,
  addedRequirements,
  startCapture,
  endCapture
} from '$lib/stores/capture';
import {
  onSrsProgress,
  onQualityFound,
  onGoalInferred,
  onCoverageReport,
  onSrsReady,
  onSrsDraft
} from '$lib/stores/srs';
import {
  onAnalysisProgress,
  onAnalysisMerReady,
  onAnalysisNfrReady,
  onAnalysisProcessReady,
  onAnalysisAdrReady,
  onAnalysisSubProjectReady,
  onAnalysisReady
} from '$lib/stores/analysis';
import { onGroupingReady } from '$lib/stores/requirements';
import {
  onGroupingProgress,
  onGroupingDone,
  endGrouping
} from '$lib/stores/grouping';

// --- Captura: qué tools abren/cierran el banner vivo --------------------
// startCapture() resetea el estado del pipeline (wipes conflictos/reportes),
// así que dispara UNA vez por captura: al abrirla (ingest_documents) y al
// cerrarla (commit_capture). Las tools de etapa intermedias sólo emiten fine
// events que se acumulan; no reinician el banner.
const CAPTURE_START_TOOLS = new Set(['ingest_documents']);
const CAPTURE_END_TOOLS = new Set(['commit_capture']);

// --- Tipos de mensaje (union discriminada por `kind`) -----------------------
// Timeline lineal: cada segmento de texto del agente y cada tool call son
// mensajes top-level independientes, renderizados en orden cronológico.

export interface UserMessage {
  kind: 'user';
  id: string;
  content: string;
  created_at: string;
}

export interface AssistantMessage {
  kind: 'assistant';
  id: string;
  content: string;
  streaming: boolean;
  created_at: string;
  /** Aviso cuando el relay descartó texto por los topes anti-veneno
   *  (evento SSE relay.truncated, sesión 44). */
  truncated?: RelayTruncatedEvent | null;
  /** Foto de sesión: flag persistido por el relay al cerrarse el segmento
   *  (true = razonamiento intermedio). undefined = inferir posicionalmente
   *  (ruta legacy). */
  isIntermediate?: boolean;
}

export interface ToolMessage {
  kind: 'tool';
  id: string;
  name: string;
  input: ToolInput;
  output: string | null;
  status: 'running' | 'done';
  created_at: string;
}

/** Razonamiento interno del modelo (reasoning_content de GLM, evento SSE
 *  `thinking`). Efímero de UI: NO se persiste en el backend, así que no
 *  aparece al recargar el historial. Mientras streaming=true se muestra
 *  expandido; el siguiente texto/tool lo cierra. */
export interface ThinkingMessage {
  kind: 'thinking';
  id: string;
  content: string;
  streaming: boolean;
  created_at: string;
}

export type Message = UserMessage | AssistantMessage | ThinkingMessage | ToolMessage;

// --- Stores -----------------------------------------------------------------

export const messages = writable<Message[]>([]);
export const isStreaming = writable<boolean>(false);
export const chatError = writable<string | null>(null);
export const streamingSessionId = writable<number | null>(null);

let activeController: AbortController | null = null;
let messageSeq = 0;

function nextId(prefix: string): string {
  messageSeq += 1;
  return `${prefix}-${messageSeq}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

/** Crea un mensaje de usuario en la lista (antes de disparar el stream). */
export function pushUserMessage(content: string): void {
  messages.update((list) => [
    ...list,
    { kind: 'user', id: nextId('u'), content, created_at: nowIso() }
  ]);
}

/** Crea un mensaje assistant vacío y devuelve su id (para ir llenándolo). */
export function openAssistantMessage(): string {
  const id = nextId('a');
  messages.update((list) => [
    ...list,
    {
      kind: 'assistant',
      id,
      content: '',
      streaming: true,
      created_at: nowIso()
    }
  ]);
  return id;
}

/** Acumula un delta de token en el assistant message. */
export function appendToken(assistantId: string, delta: string): void {
  messages.update((list) =>
    list.map((m) =>
      m.kind === 'assistant' && m.id === assistantId
        ? { ...m, content: m.content + delta }
        : m
    )
  );
}

/** Marca el assistant message con el aviso de salida truncada
 *  (evento SSE relay.truncated: el relay descartó texto del turno). */
export function markAssistantTruncated(
  assistantId: string,
  info: RelayTruncatedEvent
): void {
  messages.update((list) =>
    list.map((m) =>
      m.kind === 'assistant' && m.id === assistantId
        ? { ...m, truncated: info }
        : m
    )
  );
}

/** Crea un thinking message vacío y devuelve su id (deltas siguientes lo
 *  llenan). Uno por llamada al modelo; el orquestador y cada subagente
 *  generan el suyo. */
export function openThinkingMessage(): string {
  const id = nextId('th');
  messages.update((list) => [
    ...list,
    { kind: 'thinking', id, content: '', streaming: true, created_at: nowIso() }
  ]);
  return id;
}

/** Acumula un delta de thinking en el mensaje indicado. */
export function appendThinking(thinkingId: string, delta: string): void {
  messages.update((list) =>
    list.map((m) =>
      m.kind === 'thinking' && m.id === thinkingId
        ? { ...m, content: m.content + delta }
        : m
    )
  );
}

/** Cierra el thinking message (streaming=false). */
export function closeThinkingMessage(thinkingId: string): void {
  messages.update((list) =>
    list.map((m) =>
      m.kind === 'thinking' && m.id === thinkingId
        ? { ...m, streaming: false }
        : m
    )
  );
}

/** Crea un tool message top-level (status=running). Devuelve su id. */
export function pushToolMessage(name: string, input: ToolInput): string {
  const id = nextId('t');
  messages.update((list) => [
    ...list,
    {
      kind: 'tool',
      id,
      name,
      input,
      output: null,
      status: 'running',
      created_at: nowIso()
    }
  ]);
  return id;
}

/** Marca un tool message como done con su output. */
export function completeToolMessage(toolId: string, output: string): void {
  messages.update((list) =>
    list.map((m) =>
      m.kind === 'tool' && m.id === toolId
        ? { ...m, status: 'done' as const, output }
        : m
    )
  );
}

/**
 * Cierra el assistant message (streaming=false). Si dropIfEmpty y el mensaje
 * quedó vacío (sin tokens), se elimina de la lista para no dejar un bubble
 * vacío cuando el agente arranca con una tool call en lugar de texto.
 */
export function closeAssistantMessage(
  assistantId: string,
  dropIfEmpty = false
): void {
  messages.update((list) => {
    const updated = list.map((m) =>
      m.kind === 'assistant' && m.id === assistantId
        ? { ...m, streaming: false }
        : m
    );
    if (!dropIfEmpty) return updated;
    // Un mensaje con aviso de truncado se conserva aunque no tenga contenido:
    // el aviso es lo que el usuario debe ver.
    return updated.filter(
      (m) =>
        !(
          m.kind === 'assistant' &&
          m.id === assistantId &&
          !m.content &&
          !m.truncated
        )
    );
  });
}

/** Resetea la lista de mensajes (al cambiar de sesión). */
export function setMessages(list: Message[]): void {
  messages.set(list);
}

/**
 * Envía un mensaje a la sesión y conecta handlers al stream SSE.
 *
 * Modelo de timeline lineal: cada segmento de texto del agente abre un nuevo
 * assistant message; cada tool call es un tool message top-level. Cuando llega
 * una tool call se cierra el segmento de texto actual (si lo hay), y el
 * próximo token abre un assistant message nuevo — así texto y tools se
 * intercalan en orden cronológico en la lista.
 */
export async function sendMessage(sessionId: number, content: string): Promise<boolean> {
  if (activeController !== null) {
    // Ya hay un stream activo: rechazar (doble POST protege el backend).
    chatError.set('stream_active');
    return false;
  }

  pushUserMessage(content);

  isStreaming.set(true);
  streamingSessionId.set(sessionId);
  chatError.set(null);

  // Segmento de texto actual (lazy: se abre con el primer token).
  let currentAssistantId: string | null = null;
  // Thinking interno en curso (lazy: se abre con el primer delta de
  // reasoning_content). Cualquier transición (texto, tool) lo cierra
  // visualmente, PERO el bloque del turno se puede REABRIR: con delegación
  // via task el orquestador y el subagente stremeane en paralelo
  // (subgraphs=True) y sus eventos se intercalan a mitad de palabra; cerrar
  // y abrir un bloque nuevo por cada intercalado fragmentaba el razonamiento
  // en decenas de "Pensamiento interno" diminutos (sesión 15). Ahora hay UN
  // bloque por TURNO: turnThinkingId sobrevive hasta completed/failed.
  let currentThinkingId: string | null = null;
  let turnThinkingId: string | null = null;
  const closeThinking = () => {
    if (currentThinkingId !== null) {
      closeThinkingMessage(currentThinkingId);
      currentThinkingId = null;
    }
  };
  // Fin lógico del turno: el bloque deja de ser reabrible.
  const endTurnThinking = () => {
    closeThinking();
    turnThinkingId = null;
  };
  // Mapping name -> toolId del tool message que estamos llenando.
  // El backend puede emitir varios tool_start antes de su tool_end
  // (uno por cada tool call). Acumulamos FIFO y matcheamos por nombre.
  const pendingTools: Array<{ id: string; name: string }> = [];

  try {
    const controller = await streamMessage(sessionId, content, {
      onThinking: (delta) => {
        if (currentThinkingId === null) {
          currentThinkingId = turnThinkingId ?? openThinkingMessage();
          turnThinkingId = currentThinkingId;
        }
        appendThinking(currentThinkingId, delta);
      },
      onToken: (delta) => {
        closeThinking();
        if (currentAssistantId === null) {
          currentAssistantId = openAssistantMessage();
        }
        appendToken(currentAssistantId, delta);
      },
      onToolStart: (name, input) => {
        closeThinking();
        // Cerrar el segmento de texto actual (drop si quedó vacío).
        if (currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, true);
          currentAssistantId = null;
        }
        const toolId = pushToolMessage(name, input);
        pendingTools.push({ id: toolId, name });
        // Si arranca la captura, resetear el estado vivo del pipeline.
        if (CAPTURE_START_TOOLS.has(name)) {
          startCapture();
        }
      },
      onRelayTruncated: (t) => {
        // El relay descartó texto del turno (topes anti-veneno, sesión 44).
        // Defensivo: si no hay segmento abierto (el truncado llegó sin tokens
        // previos), abrir uno para que el aviso tenga dónde adjuntarse.
        if (currentAssistantId === null) {
          currentAssistantId = openAssistantMessage();
        }
        markAssistantTruncated(currentAssistantId, t);
      },
      onToolEnd: (name, output) => {
        // El tool_end de la delegación cierra también el thinking del
        // subagente (llegó DENTRO de task, después del tool_start).
        closeThinking();
        // FIFO match por nombre: primer pending con mismo name.
        const idx = pendingTools.findIndex((p) => p.name === name);
        if (idx >= 0) {
          const [match] = pendingTools.splice(idx, 1);
          completeToolMessage(match.id, output);
        }
        // Cuando termina la delegación (task), cerrar el segmento de texto
        // del subagente para que el texto del orquestador abra un mensaje
        // nuevo. Sin esto, el texto del orquestador se concatena al último
        // mensaje del subagente (currentAssistantId sigue apuntando ahí).
        if (name === 'task' && currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, false);
          currentAssistantId = null;
        }
        // Sin match: no hay tool message para cerrar, ignorar.
        // Fin de la captura: conservar hallazgos en UI, sólo bajar flag running.
        // commit_capture cierra la variante agentica por etapas.
        if (CAPTURE_END_TOOLS.has(name)) {
          endCapture();
        }
      },
      onProgress: (p) => {
        captureStage.set(p);
        // Profiling: acumular wall-clock por etapa conforme llegan los
        // eventos phase:"end"; al llegar stage:"done" se reemplaza con el
        // dict completo + el total, para que el banner muestre el desglose.
        if (p.phase === 'end' && typeof p.elapsed_ms === 'number') {
          // Local const: acceder a p.elapsed_ms dentro de la arrow pierde el
          // narrowing y tipa el spread como Record<string, number | undefined>.
          const elapsed = p.elapsed_ms;
          captureTimings.update((t) => ({ ...t, [p.stage]: elapsed }));
        }
        if (p.stage === 'done' && p.timings) {
          captureTimings.set(p.timings);
          if (typeof p.total_ms === 'number') captureTotalMs.set(p.total_ms);
        }
      },
      onConflict: (c) => {
        conflicts.update((list) => [...list, c]);
      },
      onValidationReport: (r) => {
        validationReport.set(r);
      },
      onRequirementAdded: (req) => {
        addedRequirements.update((list) => [...list, req]);
      },
      // Fine events del subagente srs-agent (comando /srs): el store SRS
      // auto-inicia el run en el primer srs.progress y lo cierra en srs.ready.
      onSrsProgress: (p) => onSrsProgress(p),
      onQualityFound: (f) => onQualityFound(f),
      onGoalInferred: (g) => onGoalInferred(g),
      onCoverageReport: (c) => onCoverageReport(c),
      onSrsReady: (r) => onSrsReady(r),
      onSrsDraft: (d) => onSrsDraft(d),
      // Fine events del subagente analysis-agent (Phase 2): el store de
      // análisis auto-inicia el run en el primer analysis.progress y lo
      // cierra en analysis.ready.
      onAnalysisProgress: (p) => onAnalysisProgress(p),
      onAnalysisMerReady: (e) => onAnalysisMerReady(e),
      onAnalysisNfrReady: (e) => onAnalysisNfrReady(e),
      onAnalysisProcessReady: (e) => onAnalysisProcessReady(e),
      onAnalysisAdrReady: (e) => onAnalysisAdrReady(e),
      onAnalysisSubProjectReady: (e) => onAnalysisSubProjectReady(e),
      onAnalysisReady: (r) => onAnalysisReady(r),
      // grouping.progress (/agrupar directo): etapa/lote para el banner vivo.
      onGroupingProgress: (p) => onGroupingProgress(p),
      // grouping.ready (/agrupar directo o tool): refresca planes + selecciona
      // el nuevo plan en el panel de agrupamiento (sin abrir la pestaña) y
      // cierra el banner de progreso con el resultado.
      onGroupingReady: (e) => {
        onGroupingReady(e);
        onGroupingDone(e);
      },
      onCompleted: () => {
        endTurnThinking();
        if (currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, true);
          currentAssistantId = null;
        }
      },
      onFailed: (err) => {
        chatError.set(err);
        endGrouping();
        endTurnThinking();
        if (currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, true);
          currentAssistantId = null;
        }
      }
    });
    activeController = controller;
    return true;
  } catch (e) {
    chatError.set((e as Error).message);
    if (currentAssistantId !== null) {
      closeAssistantMessage(currentAssistantId, true);
    }
    return false;
  } finally {
    isStreaming.set(false);
    streamingSessionId.set(null);
    activeController = null;
  }
}

/** Cancela el stream activo. Aborta el fetch; el assistant msg queda con lo
 *  acumulado hasta el momento. */
export function cancelStream(): void {
  if (activeController) {
    activeController.abort();
    activeController = null;
  }
  isStreaming.set(false);
  streamingSessionId.set(null);
}

/** Carga el historial de una sesión (desde GET /api/chat/sessions/{id}) y
 *  lo convierte en Message[]. Incluye llamadas a herramientas (role:'tool')
 *  reconstruidas desde el checkpointer del agente, además de user/assistant. */
export function loadHistoryFromDetail(msgs: MessageOut[]): Message[] {
  const out: Message[] = [];
  for (const m of msgs) {
    if (m.role === 'user') {
      out.push({
        kind: 'user',
        id: `u-${m.id}`,
        content: m.content,
        created_at: m.created_at
      });
    } else if (m.role === 'tool') {
      out.push({
        kind: 'tool',
        id: `t-${m.id}`,
        name: m.tool_name ?? '',
        input: (m.tool_args as ToolInput | null | undefined) ?? {},
        output: m.content || null,
        status: m.status === 'running' ? 'running' : 'done',
        created_at: m.created_at
      });
    } else {
      // assistant: omitir blobs vacíos (no renderizan en histórico).
      if (!m.content) continue;
      out.push({
        kind: 'assistant',
        id: `a-${m.id}`,
        content: m.content,
        streaming: false,
        created_at: m.created_at,
        ...(m.is_intermediate != null ? { isIntermediate: m.is_intermediate } : {})
      });
    }
  }
  return out;
}

/** Resetea para tests. */
export function _resetChatForTests(): void {
  messages.set([]);
  isStreaming.set(false);
  chatError.set(null);
  streamingSessionId.set(null);
  activeController = null;
  messageSeq = 0;
}
