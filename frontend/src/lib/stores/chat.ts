// Store del chat: mensajes por sesión + stream SSE + cancel.
// Archivo .ts PLANO → writable de svelte/store.
import { writable } from 'svelte/store';
import { streamMessage, type ToolInput } from '$lib/api/chat';

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

export type Message = UserMessage | AssistantMessage | ToolMessage;

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
    return updated.filter(
      (m) => !(m.kind === 'assistant' && m.id === assistantId && !m.content)
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
  // Mapping name -> toolId del tool message que estamos llenando.
  // El backend puede emitir varios tool_start antes de su tool_end
  // (uno por cada tool call). Acumulamos FIFO y matcheamos por nombre.
  const pendingTools: Array<{ id: string; name: string }> = [];

  try {
    const controller = await streamMessage(sessionId, content, {
      onToken: (delta) => {
        if (currentAssistantId === null) {
          currentAssistantId = openAssistantMessage();
        }
        appendToken(currentAssistantId, delta);
      },
      onToolStart: (name, input) => {
        // Cerrar el segmento de texto actual (drop si quedó vacío).
        if (currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, true);
          currentAssistantId = null;
        }
        const toolId = pushToolMessage(name, input);
        pendingTools.push({ id: toolId, name });
      },
      onToolEnd: (name, output) => {
        // FIFO match por nombre: primer pending con mismo name.
        const idx = pendingTools.findIndex((p) => p.name === name);
        if (idx >= 0) {
          const [match] = pendingTools.splice(idx, 1);
          completeToolMessage(match.id, output);
        }
        // Sin match: no hay tool message para cerrar, ignorar.
      },
      onCompleted: () => {
        if (currentAssistantId !== null) {
          closeAssistantMessage(currentAssistantId, true);
          currentAssistantId = null;
        }
      },
      onFailed: (err) => {
        chatError.set(err);
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
 *  lo convierte en Message[]. */
export function loadHistoryFromDetail(
  msgs: Array<{ id: number; role: string; content: string; created_at: string }>
): Message[] {
  return msgs.map((m) => {
    if (m.role === 'user') {
      return {
        kind: 'user',
        id: `u-${m.id}`,
        content: m.content,
        created_at: m.created_at
      };
    }
    return {
      kind: 'assistant',
      id: `a-${m.id}`,
      content: m.content,
      streaming: false,
      created_at: m.created_at
    };
  });
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
