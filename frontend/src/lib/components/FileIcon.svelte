<script lang="ts">
  // Icono por tipo de archivo/carpeta (extensión → glifo SVG a color,
  // estilo Seti de VSCode). Los glifos son monócromos con currentColor:
  // el color lo aporta el mapa por tipo (ver DEFAULT_COLORS / EXT_MAP).
  let {
    name,
    kind,
    open = false
  }: { name: string; kind: 'file' | 'dir'; open?: boolean } = $props();

  // Color por defecto de cada glifo.
  const DEFAULT_COLORS: Record<string, string> = {
    folder: '#dcb67a',
    md: '#519aba',
    pdf: '#e5484d',
    doc: '#519aba',
    sheet: '#89ca78',
    image: '#a074c4',
    json: '#cbcb41',
    code: '#8a8f98',
    brackets: '#e37933',
    hash: '#519aba',
    sliders: '#e37933',
    term: '#89ca78',
    archive: '#e5c07b',
    flow: '#4ec9b0',
    db: '#e5c07b',
    txt: '#8a8f98',
    file: '#8a8f98'
  };

  // ext → [glifo, colorOverride?]
  const EXT_MAP: Record<string, [string] | [string, string]> = {
    md: ['md'],
    markdown: ['md'],
    pdf: ['pdf'],
    doc: ['doc'],
    docx: ['doc'],
    odt: ['doc'],
    rtf: ['doc'],
    xls: ['sheet'],
    xlsx: ['sheet'],
    csv: ['sheet'],
    tsv: ['sheet'],
    ods: ['sheet'],
    png: ['image'],
    jpg: ['image'],
    jpeg: ['image'],
    gif: ['image'],
    svg: ['image'],
    webp: ['image'],
    bmp: ['image'],
    ico: ['image'],
    json: ['json'],
    py: ['code', '#4b8bbe'],
    ts: ['code', '#3178c6'],
    tsx: ['code', '#3178c6'],
    js: ['code', '#cbcb41'],
    mjs: ['code', '#cbcb41'],
    cjs: ['code', '#cbcb41'],
    jsx: ['code', '#cbcb41'],
    html: ['brackets'],
    htm: ['brackets'],
    css: ['hash'],
    scss: ['hash'],
    less: ['hash'],
    yml: ['sliders'],
    yaml: ['sliders'],
    toml: ['sliders'],
    ini: ['sliders'],
    conf: ['sliders'],
    sh: ['term'],
    bash: ['term'],
    zsh: ['term'],
    zip: ['archive'],
    tar: ['archive'],
    gz: ['archive'],
    '7z': ['archive'],
    rar: ['archive'],
    mmd: ['flow'],
    mermaid: ['flow'],
    db: ['db'],
    sqlite: ['db'],
    sql: ['db'],
    txt: ['txt'],
    log: ['txt']
  };

  // Dotfiles sin otra marca (.env, .gitignore…) → config.
  const DOTFILE_GLYPHS: Record<string, [string] | [string, string]> = {
    '.env': ['sliders'],
    '.gitignore': ['txt'],
    '.dockerignore': ['txt']
  };

  function resolve(): { key: string; color: string } {
    if (kind === 'dir') {
      return { key: open ? 'folder-open' : 'folder', color: DEFAULT_COLORS.folder };
    }
    const lower = name.toLowerCase();
    if (DOTFILE_GLYPHS[lower]) {
      const [k, c] = DOTFILE_GLYPHS[lower];
      return { key: k, color: c ?? DEFAULT_COLORS[k] };
    }
    const dot = lower.lastIndexOf('.');
    const ext = dot > 0 ? lower.slice(dot + 1) : '';
    const def = ext ? EXT_MAP[ext] : undefined;
    if (def) {
      const [k, c] = def;
      return { key: k, color: c ?? DEFAULT_COLORS[k] };
    }
    return { key: 'file', color: DEFAULT_COLORS.file };
  }

  let glyph = $derived(resolve());
</script>

<svg
  viewBox="0 0 16 16"
  class="w-[14px] h-[14px] shrink-0"
  style="color: {glyph.color}"
  aria-hidden="true"
>
  {#if glyph.key === 'folder'}
    <path
      d="M1.5 13V4.5A1.5 1.5 0 0 1 3 3h3.2c.35 0 .68.15.9.4l1.1 1.1h4.3A1.5 1.5 0 0 1 14 6v7H1.5z"
      fill="currentColor"
    />
  {:else if glyph.key === 'folder-open'}
    <path
      d="M1.5 13V4.5A1.5 1.5 0 0 1 3 3h3.2c.35 0 .68.15.9.4l1.1 1.1h4.3A1.5 1.5 0 0 1 14 6v1H3.9a1 1 0 0 0-.97.76L1.5 13z"
      fill="currentColor"
    />
    <path
      d="M3.55 8h11.2a.6.6 0 0 1 .58.76l-.88 3.1a1.2 1.2 0 0 1-1.16.9H2.6a.68.68 0 0 1-.66-.85L3.55 8z"
      fill="currentColor"
      fill-opacity="0.65"
    />
  {:else if glyph.key === 'md'}
    <rect x="1.5" y="3.5" width="13" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.2" />
    <path d="M4 10V6.2l2.2 2.4L8.4 6.2V10" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" />
    <path d="M11.2 6.2v2.5M10.1 7.6l1.1 1.1 1.1-1.1" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" />
  {:else if glyph.key === 'pdf' || glyph.key === 'doc' || glyph.key === 'txt'}
    <!-- Documento con esquina doblada + líneas (el color diferencia el tipo). -->
    <path d="M4 1.5h5l3 3v10H4v-13z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M5.5 7.5h5M5.5 9.5h5M5.5 11.5h3" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
  {:else if glyph.key === 'sheet'}
    <path d="M4 1.5h5l3 3v10H4v-13z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M5.5 6.8h5v4.7h-5zM5.5 9.15h5M8 6.8v4.7" fill="none" stroke="currentColor" stroke-width="1" />
  {:else if glyph.key === 'image'}
    <rect x="2" y="3.5" width="12" height="9" rx="1" fill="none" stroke="currentColor" stroke-width="1.1" />
    <circle cx="5.6" cy="6.4" r="1.1" fill="currentColor" />
    <path d="M3 12l3.2-3.2 2 2 2.3-2.3L13 12" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
  {:else if glyph.key === 'json'}
    <path d="M6.5 2.5c-1.5 0-1.9 1-1.9 2.3v1.3c0 1-.6 1.8-1.6 1.9 1 .1 1.6.9 1.6 1.9v1.3c0 1.3.4 2.3 1.9 2.3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
    <path d="M9.5 2.5c1.5 0 1.9 1 1.9 2.3v1.3c0 1 .6 1.8 1.6 1.9-1 .1-1.6.9-1.6 1.9v1.3c0 1.3-.4 2.3-1.9 2.3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
  {:else if glyph.key === 'code'}
    <path d="M6 5 3 8l3 3M10 5l3 3-3 3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
  {:else if glyph.key === 'brackets'}
    <path d="M5.8 4.2 2.2 8l3.6 3.8M10.2 4.2 13.8 8l-3.6 3.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
  {:else if glyph.key === 'hash'}
    <path d="M6 3.5 5 12.5M11 3.5l-1 9M3 6.5h10M2.8 9.5h10" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
  {:else if glyph.key === 'sliders'}
    <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
    <circle cx="10.5" cy="4.5" r="1.5" fill="currentColor" />
    <circle cx="5" cy="8" r="1.5" fill="currentColor" />
    <circle cx="8.5" cy="11.5" r="1.5" fill="currentColor" />
  {:else if glyph.key === 'term'}
    <rect x="2" y="3" width="12" height="10" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.1" />
    <path d="M4.5 6.3l2 1.7-2 1.7M8.3 9.7h3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" />
  {:else if glyph.key === 'archive'}
    <path d="M2.5 2.5h11v2h-11z" fill="currentColor" />
    <path d="M3.5 5.5h9v8h-9z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M8 6.5v1.4M8 9v1.4M8 11.5V13" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
  {:else if glyph.key === 'flow'}
    <circle cx="4" cy="4.2" r="1.7" fill="none" stroke="currentColor" stroke-width="1.1" />
    <circle cx="12" cy="4.7" r="1.7" fill="none" stroke="currentColor" stroke-width="1.1" />
    <circle cx="8" cy="12" r="1.7" fill="none" stroke="currentColor" stroke-width="1.1" />
    <path d="M5.7 4.4h4.5M4.7 5.9 7 10.5M11.3 6.4 9 10.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" />
  {:else if glyph.key === 'db'}
    <ellipse cx="8" cy="3.6" rx="5" ry="1.9" fill="none" stroke="currentColor" stroke-width="1.1" />
    <path d="M3 3.6v8.8c0 1 2.2 1.9 5 1.9s5-.9 5-1.9V3.6" fill="none" stroke="currentColor" stroke-width="1.1" />
    <path d="M3 8c0 1 2.2 1.9 5 1.9S13 9 13 8" fill="none" stroke="currentColor" stroke-width="1.1" />
  {:else}
    <!-- file: documento genérico -->
    <path d="M4 1.5h5l3 3v10H4v-13z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
    <path d="M9 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round" />
  {/if}
</svg>
