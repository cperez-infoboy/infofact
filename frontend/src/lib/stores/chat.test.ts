// Tests del store de chat: acumulación de tokens, bloques de tool, flujo
// completo de un stream sintético inyectado directamente en el parser.
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mockeamos streamMessage para inyectar handlers sin fetch real.
const handlersHolder: {
  handlers: import('$lib/api/chat').StreamHandlers | null;
} = { handlers: null };

vi.mock('$lib/api/chat', async () => {
  const actual = await vi.importActual<typeof import('$lib/api/chat')>('$lib/api/chat');
  return {
    ...actual,
    streamMessage: vi.fn(async (
      _sessionId: number,
      _content: string,
      handlers: import('$lib/api/chat').StreamHandlers
    ) => {
      handlersHolder.handlers = handlers;
      return new AbortController();
    })
  };
});

import {
  messages,
  sendMessage,
  pushUserMessage,
  openAssistantMessage,
  appendToken,
  pushToolMessage,
  completeToolMessage,
  closeAssistantMessage,
  loadHistoryFromDetail,
  _resetChatForTests
} from './chat';
import { parseSseEvents } from '$lib/api/chat';

describe('chat store - low-level ops', () => {
  beforeEach(() => {
    _resetChatForTests();
  });

  it('pushUserMessage + openAssistantMessage + appendToken acumulan', () => {
    pushUserMessage('hola');
    const aid = openAssistantMessage();
    appendToken(aid, 'Ho');
    appendToken(aid, 'la');

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    expect(list).toHaveLength(2);
    expect(list[0].kind).toBe('user');
    expect(list[0].content).toBe('hola');
    expect(list[1].kind).toBe('assistant');
    expect(list[1].content).toBe('Hola');
    expect(list[1].streaming).toBe(true);
  });

  it('pushToolMessage + completeToolMessage dejan un tool message done con output', () => {
    const aid = openAssistantMessage();
    appendToken(aid, 'leyendo…');
    const tid = pushToolMessage('read_file', { file_path: '/x.md' });
    completeToolMessage(tid, '# Hola\nmundo');

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    // Assistant + tool son ambos top-level en el timeline lineal.
    expect(list).toHaveLength(2);
    expect(list[0].kind).toBe('assistant');
    expect(list[0].content).toBe('leyendo…');

    const tool = list.find((m) => m.kind === 'tool');
    expect(tool).toBeDefined();
    expect(tool!.id).toBe(tid);
    expect(tool!.name).toBe('read_file');
    expect(tool!.status).toBe('done');
    expect(tool!.output).toBe('# Hola\nmundo');
    expect(tool!.input.file_path).toBe('/x.md');
  });

  it('closeAssistantMessage(dropIfEmpty=true) elimina el assistant msg vacío', () => {
    const aid = openAssistantMessage();
    // Sin tokens → content sigue vacío.
    closeAssistantMessage(aid, true);

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    expect(list).toHaveLength(0);
  });

  it('closeAssistantMessage(dropIfEmpty=true) conserva el assistant msg con contenido', () => {
    const aid = openAssistantMessage();
    appendToken(aid, 'texto');
    closeAssistantMessage(aid, true);

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    expect(list).toHaveLength(1);
    expect(list[0].kind).toBe('assistant');
    expect(list[0].content).toBe('texto');
    expect(list[0].streaming).toBe(false);
  });
});

describe('chat store - sendMessage end-to-end con handlers sintéticos', () => {
  beforeEach(() => {
    _resetChatForTests();
    handlersHolder.handlers = null;
  });

  it('inyectando eventos SSE a través de handlers produce timeline lineal correcto', async () => {
    // sendMessage dispara streamMessage mockeado → captura handlers.
    await sendMessage(42, 'leé docs/hello.md');

    const h = handlersHolder.handlers;
    expect(h).not.toBeNull();

    // Simulamos el stream del backend: tokens + tool_start + tool_end + tokens + completed.
    h!.onToken?.('L');
    h!.onToken?.('eyendo ');
    h!.onToolStart?.('read_file', { file_path: '/workspaces/x/docs/hello.md' });
    h!.onToolEnd?.('read_file', '# Hola\nmundo');
    h!.onToken?.('El archivo dice "Hola mundo".');
    h!.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    // Timeline lineal: user, assistant("Leyendo "), tool, assistant("El archivo…").
    expect(list).toHaveLength(4);

    expect(list[0].kind).toBe('user');
    expect(list[0].content).toBe('leé docs/hello.md');

    expect(list[1].kind).toBe('assistant');
    expect(list[1].streaming).toBe(false);
    expect(list[1].content).toBe('Leyendo ');

    expect(list[2].kind).toBe('tool');
    expect(list[2].name).toBe('read_file');
    expect(list[2].status).toBe('done');
    expect(list[2].output).toBe('# Hola\nmundo');
    expect(list[2].input.file_path).toBe('/workspaces/x/docs/hello.md');

    expect(list[3].kind).toBe('assistant');
    expect(list[3].streaming).toBe(false);
    expect(list[3].content).toBe('El archivo dice "Hola mundo".');
  });

  it('tool_start sin tokens previos no deja assistant vacío', async () => {
    await sendMessage(42, 'lee');

    const h = handlersHolder.handlers!;
    // Arranca directo con tool call, sin texto previo.
    h.onToolStart?.('read_file', { file_path: '/a.md' });
    h.onToolEnd?.('read_file', 'ok');
    h.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    // user + tool solamente; sin assistant vacío.
    expect(list).toHaveLength(2);
    expect(list[0].kind).toBe('user');
    expect(list[1].kind).toBe('tool');
    expect(list[1].status).toBe('done');
  });

  it('onFailed cierra el assistant msg y deja chatError', async () => {
    await sendMessage(42, 'rompe');
    const h = handlersHolder.handlers!;
    // Abrimos assistant con un token para que onFailed tenga algo que cerrar.
    h.onToken?.('x');
    h.onFailed?.('boom');

    let list: any[] = [];
    let err: string | null = null;
    messages.subscribe((v) => (list = v))();
    const { chatError } = await import('./chat');
    chatError.subscribe((v) => (err = v))();

    expect(list[1].kind).toBe('assistant');
    expect(list[1].streaming).toBe(false);
    expect(list[1].content).toBe('x');
    expect(err).toBe('boom');
  });
});

describe('chat store - parser SSE', () => {
  it('parsea evento único con data JSON', () => {
    const buf = 'event: token\ndata: {"delta":"x"}\n\n';
    const { events, remainder } = parseSseEvents(buf);
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe('token');
    expect(events[0].data).toBe('{"delta":"x"}');
    expect(remainder).toBe('');
  });

  it('parsea varios eventos separados por \\n\\n', () => {
    const buf =
      'event: token\ndata: {"delta":"a"}\n\n' +
      'event: token\ndata: {"delta":"b"}\n\n' +
      'event: completed\ndata: {}\n\n';
    const { events } = parseSseEvents(buf);
    expect(events).toHaveLength(3);
    expect(events[2].event).toBe('completed');
  });

  it('deja resto sin terminator en remainder', () => {
    const buf = 'event: token\ndata: {"delta":"a"}\n\nevent: token\ndata: {"delta":"b"}';
    const { events, remainder } = parseSseEvents(buf);
    expect(events).toHaveLength(1);
    expect(remainder).toBe('event: token\ndata: {"delta":"b"}');
  });

  it('multi-línea data: concatena con \\n', () => {
    const buf = 'event: x\ndata: linea1\ndata: linea2\n\n';
    const { events } = parseSseEvents(buf);
    expect(events[0].data).toBe('linea1\nlinea2');
  });
});

describe('chat store - loadHistoryFromDetail', () => {
  it('mapea roles a Message kinds', () => {
    const history = [
      { id: 1, role: 'user', content: 'hola', created_at: '2024-01-01T00:00:00Z' },
      { id: 2, role: 'assistant', content: 'hey', created_at: '2024-01-01T00:00:01Z' }
    ];
    const result = loadHistoryFromDetail(history);
    expect(result[0].kind).toBe('user');
    expect(result[1].kind).toBe('assistant');
    expect((result[1] as any).streaming).toBe(false);
  });
});
