<script lang="ts">
  // Editor WYSIWYG de markdown (Tiptap v3) para archivos .md del workspace.
  //
  // Tiptap no tiene paquete Svelte: se usa la clase Editor de @tiptap/core
  // montada en onMount (patrón de la guía oficial para Svelte 5). La
  // extensión oficial @tiptap/markdown hace el round-trip: el contenido
  // entra como markdown (contentType) y cada edición serializa de vuelta
  // con editor.getMarkdown() → onchange(md) → markDirty → saveActive
  // (el archivo sigue siendo .md; backend sin cambios).
  //
  // Runes OK (.svelte).
  import { onMount, onDestroy } from 'svelte';
  import { Editor } from '@tiptap/core';
  import { StarterKit } from '@tiptap/starter-kit';
  import { Markdown } from '@tiptap/markdown';
  import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table';

  let {
    initialContent,
    onchange
  }: { initialContent: string; onchange: (md: string) => void } = $props();

  let host = $state<HTMLDivElement | null>(null);
  let editor = $state<Editor | null>(null);

  // Contador de transacciones: fuerza re-evaluar los estados activos de la
  // toolbar (los métodos del editor no son reactivos a Svelte).
  let tx = $state(0);

  // Input inline de link (aparece al pulsar el botón de link).
  let showLink = $state(false);
  let linkValue = $state('');
  let linkInput = $state<HTMLInputElement | null>(null);

  $effect(() => {
    if (showLink) linkInput?.focus();
  });

  onMount(() => {
    const e = new Editor({
      element: host!,
      extensions: [StarterKit, Markdown, Table, TableRow, TableHeader, TableCell],
      content: initialContent,
      contentType: 'markdown',
      editorProps: {
        attributes: {
          // Misma tipografía que el chat y la vista de documentos.
          class: 'md-body',
          spellcheck: 'false'
        }
      },
      onUpdate: ({ editor: ed }) => onchange(ed.getMarkdown()),
      onTransaction: () => {
        tx++;
      }
    });
    editor = e;
  });

  onDestroy(() => {
    editor?.destroy();
    editor = null;
  });

  // --- Toolbar ---

  interface Btn {
    key: string;
    label: string;
    title: string;
    cls?: string;
    run: () => void;
    active?: () => boolean;
    enabled?: () => boolean;
  }

  function chain() {
    return editor!.chain().focus();
  }

  function inTable(): boolean {
    void tx;
    return !!editor?.isActive('table');
  }

  let buttons = $derived.by(() => {
    void tx; // dependencia: re-deriva en cada transacción
    const e = editor;
    const btns: Btn[] = [];
    if (!e) return btns;
    const can = (fn: () => boolean) => {
      try {
        return fn();
      } catch {
        return false;
      }
    };

    btns.push(
      {
        key: 'undo',
        label: '↶',
        title: 'Deshacer',
        run: () => chain().undo().run(),
        enabled: () => can(() => e.can().undo())
      },
      {
        key: 'redo',
        label: '↷',
        title: 'Rehacer',
        run: () => chain().redo().run(),
        enabled: () => can(() => e.can().redo())
      },
      {
        key: 'p',
        label: 'P',
        title: 'Párrafo',
        run: () => chain().setParagraph().run(),
        active: () => e.isActive('paragraph')
      },
      {
        key: 'h1',
        label: 'H1',
        title: 'Título 1',
        run: () => chain().toggleHeading({ level: 1 }).run(),
        active: () => e.isActive('heading', { level: 1 })
      },
      {
        key: 'h2',
        label: 'H2',
        title: 'Título 2',
        run: () => chain().toggleHeading({ level: 2 }).run(),
        active: () => e.isActive('heading', { level: 2 })
      },
      {
        key: 'h3',
        label: 'H3',
        title: 'Título 3',
        run: () => chain().toggleHeading({ level: 3 }).run(),
        active: () => e.isActive('heading', { level: 3 })
      },
      {
        key: 'bold',
        label: 'B',
        title: 'Negrita',
        cls: 'font-bold',
        run: () => chain().toggleBold().run(),
        active: () => e.isActive('bold')
      },
      {
        key: 'italic',
        label: 'I',
        title: 'Itálica',
        cls: 'italic',
        run: () => chain().toggleItalic().run(),
        active: () => e.isActive('italic')
      },
      {
        key: 'strike',
        label: 'S',
        title: 'Tachado',
        cls: 'line-through',
        run: () => chain().toggleStrike().run(),
        active: () => e.isActive('strike')
      },
      {
        key: 'code',
        label: '</>',
        title: 'Código',
        run: () => chain().toggleCode().run(),
        active: () => e.isActive('code')
      },
      {
        key: 'bullet',
        label: '•',
        title: 'Lista',
        run: () => chain().toggleBulletList().run(),
        active: () => e.isActive('bulletList')
      },
      {
        key: 'ordered',
        label: '1.',
        title: 'Lista numerada',
        run: () => chain().toggleOrderedList().run(),
        active: () => e.isActive('orderedList')
      },
      {
        key: 'quote',
        label: '❝',
        title: 'Cita',
        run: () => chain().toggleBlockquote().run(),
        active: () => e.isActive('blockquote')
      },
      {
        key: 'hr',
        label: '—',
        title: 'Separador',
        run: () => chain().setHorizontalRule().run()
      },
      {
        key: 'link',
        label: '🔗',
        title: 'Link',
        run: () => {
          if (e.isActive('link')) {
            chain().unsetLink().run();
            return;
          }
          linkValue = '';
          showLink = true;
        },
        active: () => e.isActive('link')
      },
      {
        key: 'table',
        label: '▦',
        title: 'Insertar tabla',
        run: () => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
      }
    );

    if (e.isActive('table')) {
      btns.push(
        {
          key: 'add-row',
          label: '+fila',
          title: 'Agregar fila',
          run: () => chain().addRowAfter().run()
        },
        {
          key: 'add-col',
          label: '+col',
          title: 'Agregar columna',
          run: () => chain().addColumnAfter().run()
        },
        {
          key: 'del-row',
          label: '−fila',
          title: 'Eliminar fila',
          run: () => chain().deleteRow().run()
        },
        {
          key: 'del-col',
          label: '−col',
          title: 'Eliminar columna',
          run: () => chain().deleteColumn().run()
        },
        {
          key: 'del-table',
          label: '✕tabla',
          title: 'Eliminar tabla',
          run: () => chain().deleteTable().run()
        }
      );
    }
    return btns;
  });

  function applyLink() {
    if (!editor) return;
    const href = linkValue.trim();
    if (href) {
      editor.chain().focus().setLink({ href }).run();
    }
    showLink = false;
  }
</script>

<div class="md-editor h-full flex flex-col min-h-0 bg-bg">
  <!-- Toolbar -->
  <div
    class="flex flex-wrap items-center gap-0.5 px-2 py-1 border-b border-border
           bg-surface-2 sticky top-0 z-10"
    role="toolbar"
    aria-label="Barra de herramientas del editor"
  >
    {#each buttons as b (b.key)}
      {#if b.key === 'h1' || b.key === 'bold' || b.key === 'bullet' || b.key === 'hr' || b.key === 'table' || b.key === 'add-row'}
        <span class="w-px h-4 bg-border mx-1 shrink-0"></span>
      {/if}
      <button
        type="button"
        class="h-6 min-w-6 px-1.5 text-xs font-mono rounded-sm shrink-0
               transition-colors disabled:opacity-30 disabled:cursor-default
               {b.active?.()
          ? 'bg-surface-3 text-text'
          : 'text-text-dim hover:bg-surface-3 hover:text-text'} {b.cls ?? ''}"
        title={b.title}
        aria-label={b.title}
        aria-pressed={b.active?.() ? 'true' : undefined}
        disabled={b.enabled ? !b.enabled() : false}
        onclick={() => b.run()}
      >
        {b.label}
      </button>
    {/each}
    {#if showLink}
      <input
        bind:this={linkInput}
        bind:value={linkValue}
        type="text"
        placeholder="https://…"
        class="h-6 w-44 px-2 ml-1 text-xs font-mono bg-bg border border-border-strong
               rounded-sm outline-none focus:border-accent"
        onkeydown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            applyLink();
          } else if (e.key === 'Escape') {
            e.preventDefault();
            showLink = false;
          }
        }}
        onblur={() => (showLink = false)}
      />
    {/if}
  </div>

  <!-- Área editable (Tiptap monta el contenteditable acá) -->
  <div class="flex-1 min-h-0 overflow-auto px-6 py-4 max-w-4xl w-full mx-auto" bind:this={host}>
  </div>
</div>
