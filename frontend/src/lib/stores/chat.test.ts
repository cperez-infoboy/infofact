// Tests del store de chat: acumulación de tokens, bloques de tool, flujo
// completo de un stream sintético inyectado directamente en el parser.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

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

// Mockeamos la API de requirements para poder probar el handler de
// grouping.ready del requirements store (loadPlans + selectPlan) sin fetch.
vi.mock('$lib/api/requirements', () => ({
  listGroupingPlans: vi.fn(async () => ({
    plans: [{ id: 7, status: 'proposed', groups: 1, accepted: 0 }]
  })),
  getGroupingPlan: vi.fn(async () => ({ id: 7, groups: [] }))
}));

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
import { parseSseEvents, dispatchEvent } from '$lib/api/chat';
import {
  setProject,
  onGroupingReady,
  activePlanId
} from '$lib/stores/requirements';
import {
  listGroupingPlans,
  getGroupingPlan
} from '$lib/api/requirements';

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

describe('chat store - relay.truncated (topes anti-veneno, sesión 44)', () => {
  beforeEach(() => {
    _resetChatForTests();
    handlersHolder.handlers = null;
  });

  it('onRelayTruncated marca el assistant message activo con el aviso', async () => {
    await sendMessage(42, 'captura');
    const h = handlersHolder.handlers!;
    h.onToken?.('x'.repeat(2000));
    h.onRelayTruncated?.({ shown: 2000, omitted: 1_098_000, limit: 30_000 });
    h.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const a = list.find((m) => m.kind === 'assistant');
    expect(a.truncated).toEqual({ shown: 2000, omitted: 1_098_000, limit: 30_000 });
    expect(a.content).toBe('x'.repeat(2000));
    expect(a.streaming).toBe(false);
  });

  it('onRelayTruncated sin segmento abierto abre uno para el aviso (y no se cae)', async () => {
    await sendMessage(42, 'captura');
    const h = handlersHolder.handlers!;
    // Sin tokens previos: el aviso debe tener dónde adjuntarse.
    h.onRelayTruncated?.({ shown: 0, omitted: 500, limit: 30_000 });
    h.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const a = list.find((m) => m.kind === 'assistant');
    expect(a).toBeDefined();
    expect(a.truncated.omitted).toBe(500);
  });
});

describe('api chat - dispatchEvent', () => {
  it('relay.truncated dispara onRelayTruncated con payload numérico', () => {
    const calls: any[] = [];
    dispatchEvent(
      { event: 'relay.truncated', data: '{"shown":1,"omitted":2,"limit":3}' },
      { onRelayTruncated: (t) => calls.push(t) }
    );
    expect(calls).toEqual([{ shown: 1, omitted: 2, limit: 3 }]);
  });

  it('tipos desconocidos se ignoran sin romper (default explícito)', () => {
    expect(() =>
      dispatchEvent({ event: 'future.event', data: '{"x":1}' }, {})
    ).not.toThrow();
  });

  it('token y completed siguen fluyendo igual que antes', () => {
    const tokens: string[] = [];
    let completed = 0;
    dispatchEvent(
      { event: 'token', data: '{"delta":"a"}' },
      { onToken: (d) => tokens.push(d) }
    );
    dispatchEvent(
      { event: 'completed', data: '{}' },
      { onCompleted: () => (completed += 1) }
    );
    expect(tokens).toEqual(['a']);
    expect(completed).toBe(1);
  });
});

describe('api chat + stores - grouping.ready (comando /agrupar)', () => {
  beforeEach(() => {
    _resetChatForTests();
    handlersHolder.handlers = null;
    vi.clearAllMocks();
    setProject(null);
    activePlanId.set(null);
  });

  afterEach(() => {
    setProject(null);
    activePlanId.set(null);
  });

  it('dispatchEvent grouping.ready dispara onGroupingReady con coerción numérica', () => {
    const calls: any[] = [];
    dispatchEvent(
      { event: 'grouping.ready', data: '{"plan_id":"7","group_count":2}' },
      { onGroupingReady: (g) => calls.push(g) }
    );
    expect(calls).toEqual([{ plan_id: 7, group_count: 2 }]);
  });

  it('payload incompleto de grouping.ready coerciona a 0 sin romper', () => {
    const calls: any[] = [];
    expect(() =>
      dispatchEvent(
        { event: 'grouping.ready', data: '{}' },
        { onGroupingReady: (g) => calls.push(g) }
      )
    ).not.toThrow();
    expect(calls).toEqual([{ plan_id: 0, group_count: 0 }]);
  });

  it('el requirements store recarga planes y selecciona el nuevo plan', async () => {
    setProject(1);
    await onGroupingReady({ plan_id: 7, group_count: 2 });

    expect(listGroupingPlans).toHaveBeenCalledWith(1);
    expect(getGroupingPlan).toHaveBeenCalledWith(1, 7);

    let pid: number | null = null;
    activePlanId.subscribe((v) => (pid = v))();
    expect(pid).toBe(7);
  });

  it('onGroupingReady sin proyecto activo no toca la API', async () => {
    // _projectId === null: guard — nada que recargar.
    await onGroupingReady({ plan_id: 7, group_count: 2 });
    expect(listGroupingPlans).not.toHaveBeenCalled();
    expect(getGroupingPlan).not.toHaveBeenCalled();
  });

  it('sendMessage conecta el handler SSE al requirements store', async () => {
    setProject(1);
    await sendMessage(42, '/agrupar');
    const h = handlersHolder.handlers!;
    expect(h.onGroupingReady).toBeDefined();

    h.onGroupingReady?.({ plan_id: 7, group_count: 2 });
    // El handler es async: esperar un microtask antes de asertar.
    await new Promise((r) => setTimeout(r, 0));
    expect(listGroupingPlans).toHaveBeenCalledWith(1);
    expect(getGroupingPlan).toHaveBeenCalledWith(1, 7);
  });
});

describe('chat store - thinking interno (evento SSE thinking)', () => {
  beforeEach(() => {
    _resetChatForTests();
    handlersHolder.handlers = null;
  });

  it('onThinking abre un thinking message y acumula deltas', async () => {
    await sendMessage(42, 'hola');
    const h = handlersHolder.handlers!;

    h.onThinking?.('primer paso ');
    h.onThinking?.('segundo paso');

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const th = list.filter((m) => m.kind === 'thinking');
    expect(th).toHaveLength(1);
    expect(th[0].content).toBe('primer paso segundo paso');
    expect(th[0].streaming).toBe(true);
  });

  it('el primer token cierra el thinking y abre el segmento assistant', async () => {
    await sendMessage(42, 'hola');
    const h = handlersHolder.handlers!;

    h.onThinking?.('razonando…');
    h.onToken?.('respuesta');

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const th = list.find((m) => m.kind === 'thinking');
    expect(th.streaming).toBe(false);
    const a = list.find((m) => m.kind === 'assistant');
    expect(a.content).toBe('respuesta');
    // Orden cronológico: thinking antes del texto.
    expect(list.findIndex((m) => m.kind === 'thinking')).toBeLessThan(
      list.findIndex((m) => m.kind === 'assistant')
    );
  });

  it('tool_start cierra el thinking y tool_end cierra el thinking del subagente', async () => {
    await sendMessage(42, 'delega');
    const h = handlersHolder.handlers!;

    // Thinking del orquestador cerrado por la tool call;
    // el thinking del subagente (dentro de task) lo cierra tool_end.
    h.onThinking?.('pienso que…');
    h.onToolStart?.('task', { task: 'x' });
    h.onThinking?.('pensamiento del subagente');
    h.onToolEnd?.('task', 'ok');
    h.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const thinking = list.filter((m) => m.kind === 'thinking');
    expect(thinking).toHaveLength(2);
    expect(thinking.every((m) => m.streaming === false)).toBe(true);
  });

  it('completed cierra un thinking que quedó abierto', async () => {
    await sendMessage(42, 'solo pienso');
    const h = handlersHolder.handlers!;

    h.onThinking?.('sin texto después');
    h.onCompleted?.();

    let list: any[] = [];
    messages.subscribe((v) => (list = v))();
    const th = list.find((m) => m.kind === 'thinking');
    expect(th.streaming).toBe(false);
    // Sin tokens no hay assistant vacío.
    expect(list.some((m) => m.kind === 'assistant')).toBe(false);
  });

  it('dispatchEvent thinking dispara onThinking con el delta', () => {
    const calls: string[] = [];
    dispatchEvent(
      { event: 'thinking', data: '{"delta":"paso"}' },
      { onThinking: (d) => calls.push(d) }
    );
    expect(calls).toEqual(['paso']);
  });

  it('dispatchEvent thinking con payload raro no rompe', () => {
    expect(() =>
      dispatchEvent({ event: 'thinking', data: '{}' }, {})
    ).not.toThrow();
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
