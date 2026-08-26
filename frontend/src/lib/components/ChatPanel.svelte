<script lang="ts">
  // Panel de chat: lista de mensajes (user/assistant/tool), input + send,
  // auto-scroll, renderizado incremental de tokens SSE, bloques de tool
  // colapsables con output truncado.
  //
  // Runes OK (.svelte).
  import { onMount, onDestroy } from 'svelte';
  import {
    messages,
    isStreaming,
    chatError,
    sendMessage,
    cancelStream,
    loadHistoryFromDetail,
    setMessages
  } from '$lib/stores/chat';
  import { currentSession } from '$lib/stores/project';
  import type {
    Message,
    UserMessage,
    AssistantMessage,
    ToolMessage
  } from '$lib/stores/chat';
  import { renderMarkdown } from '$lib/utils/markdown';
  import ToolIcon from './ToolIcon.svelte';
  import CaptureStatus from './CaptureStatus.svelte';
  import SrsStatus from './SrsStatus.svelte';
  import GroupingStatus from './GroupingStatus.svelte';

  let { } = $props();

  let input = $state('');
  let container: HTMLDivElement | null = $state(null);
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
    input = '';
    isAtBottom = true;
    await sendMessage(s.id, text);
  }

  function handleKeydown(e: KeyboardEvent) {
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
      } else {
        // isFinal: el próximo mensaje NO es un tool NI otro assistant → es la
        // respuesta final del turno. Dos assistants consecutivos (subagente →
        // orquestador tras delegación via task) hacen que el primero sea
        // intermedio; sólo el último assistant del turno se renderiza completo.
        // Foto de sesión: si el relay persistió el flag isIntermediate, es
        // autoritativo (override de la inferencia posicional).
        const next = list[i + 1];
        const nextIsToolOrAssistant =
          next !== undefined && (next.kind === 'tool' || next.kind === 'assistant');
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
        <textarea
          bind:value={input}
          onkeydown={handleKeydown}
          placeholder={$currentSession
            ? 'escribe un mensaje… (Enter=enviar, Shift+Enter=salto)'
            : 'selecciona una sesión…'}
          disabled={!$currentSession || $isStreaming}
          rows="2"
          class="resize-none w-full bg-surface-2 border border-border-strong text-text text-sm px-2 py-1 pl-3 rounded-lg focus:outline-none focus:border-accent focus:shadow-[0_0_0_3px_rgba(247,248,248,0.10)] disabled:opacity-50"
        ></textarea>
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
  {@const preview = cleanPreview(msg.content)}
  {#if !msg.streaming && expandedTexts[msg.id]}
    <div class="max-w-[95%] border-l-2 border-border pl-3 py-0.5">
      <div class="text-[10px] uppercase tracking-wider text-text-faint mb-0.5">
        Razonamiento del agente
      </div>
      <div class="text-xs text-text-muted italic whitespace-pre-wrap break-words">
        {msg.content}
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
