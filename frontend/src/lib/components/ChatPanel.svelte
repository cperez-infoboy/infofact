<script lang="ts">
  // Panel de chat: lista de mensajes (user/assistant/tool), input + send,
  // auto-scroll, renderizado incremental de tokens SSE, bloques de tool
  // colapsables con output truncado.
  //
  // Runes OK (.svelte).
  import { onMount, onDestroy, tick } from 'svelte';
  import { get } from 'svelte/store';
  import {
    messages,
    isStreaming,
    chatError,
    sendMessage,
    cancelStream,
    loadHistoryFromDetail,
    setMessages
  } from '$lib/stores/chat';
  import { currentSession, currentProjectId } from '$lib/stores/project';
  import type {
    Message,
    UserMessage,
    AssistantMessage,
    ThinkingMessage,
    ToolMessage
  } from '$lib/stores/chat';
  import { renderMarkdown } from '$lib/utils/markdown';
  import { revealStep, REVEAL_TICK_MS } from '$lib/utils/typewriter';
  import { searchFiles, type SearchEntry } from '$lib/api/workspaces';
  import {
    parseAtTrigger,
    applySelection,
    parseMentionSegments,
    type AtTrigger
  } from '$lib/utils/atMention';
  import ChatFilePicker from './ChatFilePicker.svelte';
  import ToolIcon from './ToolIcon.svelte';
  import CaptureStatus from './CaptureStatus.svelte';
  import SrsStatus from './SrsStatus.svelte';
  import GroupingStatus from './GroupingStatus.svelte';
  import AnalysisStatus from './AnalysisStatus.svelte';

  let { } = $props();

  let input = $state('');
  let container: HTMLDivElement | null = $state(null);

  // --- Mención de archivos con "@" (autocompletado del workspace) --------
  // Al teclear '@' seguido de texto se abre ChatFilePicker sobre el input:
  // lista plana filtrada por substring (backend /api/workspaces/search).
  // Teclado: ↑↓ navegan, Enter/Tab insertan, Esc cierra.
  const MENTION_LIMIT = 50;

  let textareaEl: HTMLTextAreaElement | null = $state(null);
  let mention: AtTrigger | null = $state(null);
  let mentionItems: SearchEntry[] = $state([]);
  let mentionIndex = $state(0);
  let mentionLoading = $state(false);

  // Capa espejo del resaltado de menciones (ver markup del input): un div
  // absoluto DETRÁS del textarea que repinta el texto con los tokens @ruta
  // enmarcados. El texto de la capa va transparente; los glifos reales
  // siempre los dibuja el textarea, la espejo solo aporta el resaltado.
  let mirrorEl: HTMLDivElement | null = $state(null);
  const mentionSegments = $derived(parseMentionSegments(input));
  let mentionTimer: ReturnType<typeof setTimeout> | null = null;
  let mentionSeq = 0;

  // Track si el usuario está en el fondo para evitar scroll-fighting.
  let isAtBottom = $state(true);

  const OUTPUT_PREVIEW_LIMIT = 2000;

  // Límite de caracteres para el preview de texto del asistente. Mensajes
  // más largos (típicamente razonamiento del modelo) se colapsan a un
  // preview; los breves ("Voy a analizar…") se muestran completos.
  const ASSISTANT_PREVIEW_LIMIT = 150;

  // Etiquetas legibles para los nombres técnicos de tools.
  const TOOL_LABELS: Record<string, string> = {
    ingest_documents: 'Ingresando documentos',
    discover_conventions: 'Detectando convenciones',
    extract_requirements: 'Extrayendo requerimientos',
    consolidate_requirements: 'Consolidando duplicados',
    critique_requirements: 'Validando calidad',
    classify_requirements: 'Clasificando requerimientos',
    commit_capture: 'Guardando captura',
    analyze_quality: 'Analizando calidad',
    infer_goals: 'Infiriendo objetivos',
    check_coverage: 'Verificando cobertura',
    commit_srs: 'Generando SRS',
    review_grouping: 'Revisando agrupamientos',
    apply_grouping_plan: 'Aplicando agrupamiento',
    task: 'Delegando al subagente',
    orient_documents: 'Orientando documentos',
    web_search: 'Buscando en la web',
    fetch_url: 'Leyendo página web',
    analyze_image: 'Analizando imagen',
    generate_mer: 'Generando MER del dominio',
    analyze_nfrs: 'Analizando requerimientos no funcionales',
    generate_processes: 'Generando diagramas de proceso',
    generate_adrs: 'Redactando decisiones arquitectónicas',
    discover_projects: 'Descubriendo proyectos',
    propose_subprojects: 'Proponiendo sub-proyectos',
    generate_architecture: 'Generando arquitectura del sistema',
    commit_analysis: 'Guardando análisis',
    refine_analysis: 'Cargando análisis para refinamiento',
    patch_commit: 'Guardando análisis refinado',
  };

  function toolLabel(name: string): string {
    return TOOL_LABELS[name] ?? name;
  }

  // Estado "mostrar todo" por tool id (mutado por componente anidado via callback).
  let expandedOutputs: Record<string, boolean> = $state({});
  // Estado expandir/colapsar texto del asistente por message id.
  let expandedTexts: Record<string, boolean> = $state({});
  // Estado mostrar/ocultar JSON de input/output por tool id.
  let expandedToolDetails: Record<string, boolean> = $state({});

  // --- Typewriter: reveal progresivo del texto en streaming ---------------
  // Estado solo de RENDER: el store conserva el contenido completo (historial
  // y respuestas cerradas intactos); este mapa acota cuántos caracteres de
  // cada assistant message EN streaming se pintan por tick. Un interval avanza
  // el reveal con paso adaptativo (revealStep): con streaming real sigue al
  // ritmo de llegada; ante una ráfaga (respuesta completa en un frame) drena
  // en ~10 ticks en vez de arrastrarse a paso fijo. Mensajes sin streaming se
  // muestran enteros, sin animación.
  let revealedChars: Record<string, number> = $state({});
  let revealTimer: ReturnType<typeof setInterval> | null = null;

  function revealTick(): void {
    let pending = false;
    for (const m of get(messages)) {
      if (m.kind !== 'assistant' || !m.streaming) continue;
      const current = revealedChars[m.id] ?? 0;
      if (current >= m.content.length) continue;
      const next = Math.min(
        current + revealStep(m.content.length - current),
        m.content.length
      );
      revealedChars[m.id] = next;
      if (next < m.content.length) pending = true;
    }
    if (!pending) stopRevealLoop();
  }

  function startRevealLoop(): void {
    if (revealTimer !== null) return;
    revealTimer = setInterval(revealTick, REVEAL_TICK_MS);
  }

  function stopRevealLoop(): void {
    if (revealTimer !== null) {
      clearInterval(revealTimer);
      revealTimer = null;
    }
  }

  /** Texto visible del assistant message: completo si no está en streaming
   *  (historial restaurado, respuesta cerrada); prefijo revelado hasta el
   *  tick actual mientras llega el stream (typewriter). */
  function revealedContent(msg: AssistantMessage): string {
    if (!msg.streaming) return msg.content;
    const shown = revealedChars[msg.id] ?? 0;
    return msg.content.slice(0, Math.min(shown, msg.content.length));
  }

  // Cuando cambia la sesión, cargar su historial dentro del chat store.
  let lastLoadedSession: number | null = null;
  $effect(() => {
    const s = $currentSession;
    if (!s) {
      setMessages([]);
      lastLoadedSession = null;
      return;
    }
    if (lastLoadedSession !== s.id) {
      lastLoadedSession = s.id;
      setMessages(loadHistoryFromDetail(s.messages));
    }
  });

  // Auto-scroll cuando llegan tokens nuevos y el usuario está en el fondo.
  let lastMsgCount = 0;
  $effect(() => {
    // Re-leer store para registrar dependencia reactiva.
    const list = $messages;
    // Contar longitud acumulada (token + tool agrega items).
    const currentLen = list.reduce((acc, m) => {
      if (m.kind === 'assistant') return acc + m.content.length;
      if (m.kind === 'thinking') return acc + m.content.length;
      if (m.kind === 'tool') return acc + (m.output?.length ?? 0) + 1;
      return acc + 1;
    }, 0);
    if (container && isAtBottom) {
      // requestAnimationFrame para que el DOM se actualice primero.
      requestAnimationFrame(() => {
        if (container) container.scrollTop = container.scrollHeight;
      });
    }
    lastMsgCount = currentLen;
  });

  // Arranca/para el loop del typewriter según haya assistant en streaming.
  $effect(() => {
    const streaming = $messages.some(
      (m) => m.kind === 'assistant' && m.streaming
    );
    if (streaming) startRevealLoop();
    else stopRevealLoop();
  });

  function handleScroll() {
    if (!container) return;
    const threshold = 40;
    isAtBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || $isStreaming) return;
    const s = $currentSession;
    if (!s) return;
    closeMention();
    input = '';
    isAtBottom = true;
    await sendMessage(s.id, text);
  }

  // --- Mención "@": sync del trigger, búsqueda debounced y teclado -------

  /** Recalcula si hay mención activa según el texto y el caret actuales. */
  function syncMention() {
    const caret = textareaEl?.selectionStart ?? input.length;
    mention = parseAtTrigger(input, caret);
    mentionIndex = 0;
  }

  /** Programa la búsqueda con debounce corto (una request por pausa tipeo). */
  function scheduleMentionSearch() {
    if (mentionTimer !== null) clearTimeout(mentionTimer);
    if (!mention) return;
    const query = mention.query;
    mentionTimer = setTimeout(() => {
      mentionTimer = null;
      runMentionSearch(query);
    }, 150);
  }

  async function runMentionSearch(query: string) {
    const projectId = get(currentProjectId);
    if (!mention || projectId === null) return;
    const seq = ++mentionSeq; // respuestas viejas no pisan las nuevas
    mentionLoading = true;
    try {
      const res = await searchFiles(projectId, query, MENTION_LIMIT);
      if (seq !== mentionSeq) return;
      mentionItems = res.entries;
    } catch {
      // Error de red → "sin coincidencias"; el próximo tecleo reintenta.
      if (seq === mentionSeq) mentionItems = [];
    } finally {
      if (seq === mentionSeq) mentionLoading = false;
    }
  }

  /** Cierra el popup e invalida cualquier búsqueda en vuelo. */
  function closeMention() {
    if (mentionTimer !== null) {
      clearTimeout(mentionTimer);
      mentionTimer = null;
    }
    mentionSeq++;
    mention = null;
    mentionItems = [];
    mentionLoading = false;
  }

  function cycleMention(delta: number) {
    const n = mentionItems.length;
    if (n === 0) return;
    mentionIndex = (mentionIndex + delta + n) % n;
  }

  /** Inserta `@ruta ` en el mensaje y devuelve el focus al textarea. */
  async function pickMention(item: SearchEntry) {
    if (!mention || !textareaEl) return;
    const applied = applySelection(input, mention, item.path);
    input = applied.text;
    closeMention();
    await tick();
    textareaEl.focus();
    textareaEl.setSelectionRange(applied.caret, applied.caret);
  }

  function handleInput() {
    syncMention();
    scheduleMentionSearch();
  }

  /** Alinea la capa espejo cuando el textarea scrollea internamente (texto
   *  más largo que las filas visibles); si no, el resaltado se desfasa. */
  function syncMirrorScroll() {
    if (mirrorEl && textareaEl) {
      mirrorEl.scrollTop = textareaEl.scrollTop;
      mirrorEl.scrollLeft = textareaEl.scrollLeft;
    }
  }

  /**
   * Re-sync del trigger al soltar una tecla (mueve el caret sin editar).
   * Las teclas que maneja el popup NO re-sincronizan: preventDefault evita
   * que el caret se mueva, pero el keyup igual dispara, y un sync acá
   * pisa mentionIndex volviendo a la primera fila (navegación imposible).
   */
  function handleKeyup(e: KeyboardEvent) {
    if (
      e.key === 'ArrowUp' ||
      e.key === 'ArrowDown' ||
      e.key === 'Enter' ||
      e.key === 'Tab' ||
      e.key === 'Escape'
    ) {
      return;
    }
    syncMention();
  }

  function handleKeydown(e: KeyboardEvent) {
    // Precedencia del popup: mientras hay mención activa, el teclado navega
    // la lista antes de cualquier acción de envío.
    if (mention) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        cycleMention(1);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        cycleMention(-1);
        return;
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && mentionItems.length > 0) {
        e.preventDefault();
        void pickMention(mentionItems[mentionIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        closeMention();
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function toggleOutput(toolId: string) {
    expandedOutputs[toolId] = !expandedOutputs[toolId];
  }

  function toggleText(msgId: string) {
    expandedTexts[msgId] = !expandedTexts[msgId];
  }

  function toggleToolDetails(toolId: string) {
    expandedToolDetails[toolId] = !expandedToolDetails[toolId];
  }

  function prettyJson(input: unknown): string {
    try {
      return JSON.stringify(input, null, 2);
    } catch {
      return String(input);
    }
  }

  /** Extrae un preview breve del contenido del asistente: hasta el primer
   *  doble salto de línea o ASSISTANT_PREVIEW_LIMIT caracteres, lo que sea
   *  más corto. Corta en el último espacio para no partir palabras. */
  function getPreview(content: string): string {
    const nnIdx = content.indexOf('\n\n');
    const limit =
      nnIdx > 0 && nnIdx <= ASSISTANT_PREVIEW_LIMIT
        ? nnIdx
        : ASSISTANT_PREVIEW_LIMIT;
    if (content.length <= limit) return content;
    const cut = content.lastIndexOf(' ', limit);
    return content.slice(0, cut > 60 ? cut : limit).trimEnd() + '…';
  }

  /** Limpia JSON y code blocks del texto para el preview de mensajes
   *  intermedios. Remueve fenced code blocks y corta en la primera línea
   *  que parezca JSON. Devuelve '' si no queda texto narrativo útil. */
  function cleanPreview(content: string): string {
    let text = content.replace(/```[\s\S]*?```/g, '');
    const lines = text.split('\n');
    const clean: string[] = [];
    for (const line of lines) {
      const t = line.trim();
      if (t.startsWith('{') || t.startsWith('[')) break;
      clean.push(line);
    }
    text = clean.join('\n').trim();
    if (!text) return '';
    return getPreview(text);
  }

  // Agrupacion de mensajes para render: cada run contiguo de tools se colapsa
  // en un solo bloque por batch. user y assistant pasan trough como items.
  // isFinal distingue el último mensaje del turno (respuesta al usuario, se
  // muestra completo) del razonamiento intermedio (seguido de tools, colapsable).
  type RenderItem =
    | { type: 'user'; msg: UserMessage }
    | { type: 'assistant'; msg: AssistantMessage; isFinal: boolean }
    | { type: 'thinking'; msg: ThinkingMessage }
    | { type: 'tool-group'; tools: ToolMessage[] };

  function toRenderItems(list: Message[]): RenderItem[] {
    const items: RenderItem[] = [];
    let i = 0;
    while (i < list.length) {
      const m = list[i];
      if (m.kind === 'tool') {
        const batch: ToolMessage[] = [];
        while (i < list.length && list[i].kind === 'tool') {
          batch.push(list[i] as ToolMessage);
          i++;
        }
        items.push({ type: 'tool-group', tools: batch });
      } else if (m.kind === 'user') {
        items.push({ type: 'user', msg: m });
        i++;
      } else if (m.kind === 'thinking') {
        items.push({ type: 'thinking', msg: m });
        i++;
      } else {
        // isFinal: el próximo mensaje NO es un tool, otro assistant NI un
        // thinking → es la respuesta final del turno. Dos assistants
        // consecutivos (subagente → orquestador tras delegación via task)
        // hacen que el primero sea intermedio; sólo el último assistant del
        // turno se renderiza completo. Foto de sesión: si el relay persistió
        // el flag isIntermediate, es autoritativo (override de la inferencia
        // posicional).
        const next = list[i + 1];
        const nextIsToolOrAssistant =
          next !== undefined &&
          (next.kind === 'tool' ||
            next.kind === 'assistant' ||
            next.kind === 'thinking');
        const isFinal =
          m.isIntermediate !== undefined
            ? !m.isIntermediate
            : !nextIsToolOrAssistant;
        items.push({ type: 'assistant', msg: m, isFinal });
        i++;
      }
    }
    return items;
  }

  let renderItems = $derived(toRenderItems($messages));

  // Al desmontar el panel, limpiar timer/búsquedas pendientes del picker.
  onDestroy(() => {
    closeMention();
    stopRevealLoop();
  });
</script>

<section
  class="h-full flex flex-col bg-surface text-text border-l border-border min-w-0"
>
  <div class="h-9 flex items-center justify-between px-3 border-b border-border">
    <span class="text-xs uppercase tracking-wider text-text-faint font-mono"
      >Chat</span
    >
    {#if $currentSession}
      <span class="text-xs text-text-dim font-mono">#{$currentSession.id}</span>
    {/if}
  </div>

  <!-- Estado vivo del pipeline de captura (eventos finos SSE) -->
  <CaptureStatus />

  <!-- Estado vivo de la generación de SRS (eventos finos SSE) -->
  <SrsStatus />

  <!-- Estado vivo del /agrupar (grouping.progress, ruta directa) -->
  <GroupingStatus />

  <!-- Estado vivo del /analisis (analysis.progress por lote) -->
  <AnalysisStatus />

  <!-- Mensajes -->
  <div class="flex-1 min-h-0 m-2">
    <div
      bind:this={container}
      onscroll={handleScroll}
      class="max-h-full overflow-y-auto space-y-3"
    >
      {#each renderItems as item, i (i)}
        {#if item.type === 'user'}
          <div class="flex justify-end">
            <div
              class="max-w-[85%] bg-surface-2 text-text px-3 py-2 text-sm rounded-md"
            >
              {item.msg.content}
            </div>
          </div>
        {:else if item.type === 'assistant'}
          {@render assistantBlock(item.msg, item.isFinal)}
        {:else if item.type === 'thinking'}
          {@render thinkingBlock(item.msg)}
        {:else}
          {@render toolGroupBlock(item.tools)}
        {/if}
      {/each}

      {#if renderItems.length === 0}
        <div
          class="h-full flex items-center justify-center text-text-dim text-sm text-center font-mono"
        >
          {#if $currentSession}
            <span>Dile al agente qué necesitas</span>
          {:else}
            <span>Crea o selecciona una sesión para empezar</span>
          {/if}
        </div>
      {/if}
    </div>
  </div>

  {#if $chatError}
    <div
      class="mx-3 mb-2 text-xs text-danger bg-danger/5 border border-danger/40 px-2 py-1 rounded-md"
    >
      ! {$chatError}
    </div>
  {/if}

  <!-- Input (terminal prompt style) -->
  <div class="p-2 border-t border-border">
    <div class="flex items-stretch gap-2">
      <div class="flex-1 relative">
        {#if mention}
          <ChatFilePicker
            items={mentionItems}
            activeIndex={mentionIndex}
            loading={mentionLoading}
            onpick={(item) => pickMention(item)}
            onhover={(i) => (mentionIndex = i)}
          />
        {/if}
        <textarea
          bind:this={textareaEl}
          bind:value={input}
          onkeydown={handleKeydown}
          oninput={handleInput}
          onclick={syncMention}
          onkeyup={handleKeyup}
          onscroll={syncMirrorScroll}
          placeholder={$currentSession
            ? 'escribe un mensaje… (Enter=enviar, Shift+Enter=salto)'
            : 'selecciona una sesión…'}
          disabled={!$currentSession || $isStreaming}
          rows="2"
          class="resize-none w-full bg-surface-2 border border-border-strong text-text text-sm px-2 py-1 pl-3 rounded-lg focus:outline-none focus:border-accent focus:shadow-[0_0_0_3px_rgba(247,248,248,0.10)] disabled:opacity-50"
        ></textarea>
        {#if $currentSession && !$isStreaming && mentionSegments.length > 0}
          <!-- Capa espejo de menciones: repinta el texto del textarea glifo a
               glifo (mismas métricas: font/padding/borde) sobre él, con el
               texto en transparente (los glifos visibles siguen siendo los
               del textarea de abajo) y cada @ruta enmarcada. No captura
               eventos (pointer-events-none); absoluto sobre el estático. -->
          <div
            bind:this={mirrorEl}
            aria-hidden="true"
            class="pointer-events-none absolute inset-0 rounded-lg border border-transparent text-sm px-2 py-1 pl-3 pr-2 text-transparent whitespace-pre-wrap break-words overflow-hidden"
          >
            {#each mentionSegments as seg, i (i)}{#if seg.kind === 'mention'}<span
                class="-mx-px rounded-[3px] bg-accent/15 ring-1 ring-inset ring-accent/60 px-px">{seg.value}</span
              >{:else}{seg.value}{/if}{/each}
          </div>
        {/if}
      </div>
      {#if $isStreaming}
        <button
          type="button"
          class="px-3 py-2 bg-surface-2 border border-border text-text-dim hover:bg-surface-3 hover:text-text text-sm rounded-md transition-colors"
          onclick={cancelStream}>Cancelar</button
        >
      {:else}
        <button
          type="button"
          class="px-3 py-2 bg-accent text-bg hover:bg-accent-hover disabled:opacity-50 text-sm rounded-md transition-colors"
          onclick={handleSend}
          disabled={!input.trim() || !$currentSession}>Enviar</button
        >
      {/if}
    </div>
  </div>
</section>

{#snippet assistantBlock(msg: AssistantMessage, isFinal: boolean)}
  <div class="flex flex-col gap-1">
    {#if msg.streaming || !isFinal}
      {@render reasoningBlock(msg)}
    {:else if msg.content}
      <div
        class="md-body max-w-[95%] border border-border-strong bg-surface px-3 py-2 text-sm text-text-muted"
      >
        {@html renderMarkdown(msg.content)}
      </div>
    {/if}
    {#if msg.truncated}
      <div
        class="self-start max-w-[95%] text-xs text-text-dim bg-surface-2 border border-border px-2 py-1 rounded-md"
      >
        Salida truncada: se omitieron {msg.truncated.omitted.toLocaleString('es')} caracteres
      </div>
    {/if}
  </div>
{/snippet}

<!-- Razonamiento intermedio del agente (o texto aún en streaming, que aún no
     se sabe si será respuesta final): estilo atenuado con barra lateral y
     etiqueta, deliberadamente distinto de la caja de respuesta final para que
     no se confunda con ella. Expandir muestra texto plano crudo, nunca
     markdown. -->
{#snippet reasoningBlock(msg: AssistantMessage)}
  {@const content = revealedContent(msg)}
  {@const preview = cleanPreview(content)}
  {#if !msg.streaming && expandedTexts[msg.id]}
    <div class="max-w-[95%] border-l-2 border-border pl-3 py-0.5">
      <div class="text-[10px] uppercase tracking-wider text-text-faint mb-0.5">
        Razonamiento del agente
      </div>
      <div class="text-xs text-text-muted italic whitespace-pre-wrap break-words">
        {content}
      </div>
      <button
        type="button"
        class="text-xs text-text-dim hover:underline"
        onclick={() => toggleText(msg.id)}>mostrar menos</button
      >
    </div>
  {:else if msg.streaming && !preview}
    <div class="text-xs text-text-faint italic font-mono">pensando…</div>
  {:else if preview}
    <div class="max-w-[95%] border-l-2 border-border pl-3 py-0.5">
      <div class="text-[10px] uppercase tracking-wider text-text-faint mb-0.5">
        Razonamiento del agente
      </div>
      <div class="text-xs text-text-muted italic">
        <span class="whitespace-pre-wrap break-words">{preview}</span>
        {#if msg.streaming}<span
          class="animate-pulse ml-0.5 text-text-faint"
          aria-label="escribiendo">▋</span
        >{/if}
        {#if !msg.streaming}
          <button
            type="button"
            class="ml-1 text-xs text-text hover:underline"
            onclick={() => toggleText(msg.id)}>mostrar más</button
          >
        {/if}
      </div>
    </div>
  {/if}
{/snippet}

<!-- Razonamiento interno del modelo (reasoning_content de GLM, evento SSE
     `thinking`). Mientras llega va expandido (a menos que el usuario lo
     colapse); al cerrarse queda colapsado con un preview de una línea.
     Texto plano crudo, nunca markdown; no se restaura en el historial
     (efímero del turno). -->
{#snippet thinkingBlock(msg: ThinkingMessage)}
  {@const expanded = expandedTexts[msg.id] ?? msg.streaming}
  {@const preview = cleanPreview(msg.content)}
  <div class="max-w-[95%] border-l-2 border-border pl-3 py-0.5">
    <button
      type="button"
      class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-faint cursor-pointer select-none"
      onclick={() => toggleText(msg.id)}
    >
      <svg
        viewBox="0 0 24 24"
        class="h-3 w-3 transition-transform"
        class:rotate-180={expanded}
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
      Pensamiento interno
    </button>
    {#if expanded}
      <div
        class="text-xs text-text-faint italic whitespace-pre-wrap break-words mt-0.5"
      >
        {msg.content}{#if msg.streaming}<span
          class="animate-pulse ml-0.5"
          aria-label="pensando">▋</span
        >{/if}
      </div>
    {:else if preview}
      <div class="text-xs text-text-faint italic mt-0.5 truncate">{preview}</div>
    {/if}
  </div>
{/snippet}

{#snippet toolGroupBlock(tools: ToolMessage[])}
  {@const anyRunning = tools.some((t) => t.status === 'running')}
  {@const latest = tools.at(-1)!}
  <details
    class="max-w-[95%] bg-surface-2 border border-border text-xs rounded-md"
  >
    <summary class="flex items-center gap-2 px-2 py-1 cursor-pointer select-none">
      <ToolIcon running={anyRunning} />
      <span class="text-text-dim">{toolLabel(latest.name)}</span>
      <span class="font-mono text-text-faint"
        >· {tools.length} {tools.length === 1 ? 'tool' : 'tools'}</span
      >
      {#if anyRunning}
        <span class="text-xs text-text-dim animate-pulse">running…</span>
      {:else}
        <span class="text-xs text-text-faint">done</span>
      {/if}
    </summary>
    <div class="px-2 pb-2 space-y-2">
      {#each tools as part (part.id)}
        <div
          class="space-y-1 border-t border-border pt-2 first:border-t-0 first:pt-0"
        >
          <div class="flex items-center gap-2">
            <span class="font-mono text-text-dim">{toolLabel(part.name)}</span>
            {#if part.status === 'running'}
              <span class="text-text-dim animate-pulse">running…</span>
            {:else}
              <span class="text-text-faint">done</span>
            {/if}
          </div>
          {#if expandedToolDetails[part.id]}
            {#if Object.keys(part.input).length > 0}
              <div>
                <div class="text-text-faint mb-0.5">&gt; input</div>
                <pre
                  class="text-text-dim font-mono whitespace-pre-wrap break-all text-[11px]">{prettyJson(part.input)}</pre>
              </div>
            {/if}
            {#if part.output !== null}
              <div>
                <div class="text-text-faint mb-0.5">&gt; output</div>
                <pre class="text-text-dim font-mono whitespace-pre-wrap break-all text-[11px]">{#if expandedOutputs[part.id] || part.output.length <= OUTPUT_PREVIEW_LIMIT}{part.output}{:else}{part.output.slice(0, OUTPUT_PREVIEW_LIMIT)}… <button
                    type="button"
                    class="text-text hover:underline"
                    onclick={() => toggleOutput(part.id)}>mostrar todo</button>{/if}</pre>
              </div>
            {/if}
            <button
              type="button"
              class="text-text-faint hover:underline"
              onclick={() => toggleToolDetails(part.id)}>ocultar detalles</button>
          {:else}
            <button
              type="button"
              class="text-text-faint hover:underline"
              onclick={() => toggleToolDetails(part.id)}>ver detalles técnicos</button>
          {/if}
        </div>
      {/each}
    </div>
  </details>
{/snippet}
