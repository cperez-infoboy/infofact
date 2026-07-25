<script lang="ts">
  // Icono arcade pixel-art para bloques de tool del chat.
  // Mario (estilo DK arcade, estilizado) con martillo que rota mientras la
  // tool corre; estático cuando termina. Self-contained, sin deps externas.
  //
  // Runes OK (.svelte).

  let { running = false }: { running?: boolean } = $props();

  // Paleta arcade — punto de color único en la UI grayscale del design system.
  const P: Record<string, string> = {
    R: '#e5484d', // rojo: gorro, camisa, brazos
    S: '#ffcaa8', // piel: cara
    B: '#5a3a1a', // marrón: pelo, bigote, botines, mango martillo
    O: '#2860c0', // azul: overoles
    Y: '#facc15', // amarillo: cabeza del martillo
  };

  // Mario body — 12 cols x 12 rows. '.' = transparente.
  const MARIO_BODY = [
    '...RRR......',
    '..RRRRR.....',
    '..BBSSB.....',
    '..SSSSSB....',
    '...SBSB.....',
    '...BBBB.....',
    '..ROOOOR....',
    '.ROOOOOOR...',
    '.ROOOOOOR...',
    '..OOOOOO....',
    '..BB..BB....',
    '.BBBB.BBB...',
  ];

  // Martillo — 4 cols x 5 rows. Pivot = esquina inf-izq (grip donde la mano agarra).
  const HAMMER = [
    'YYYY',
    'YYYY',
    '.BB.',
    '.BB.',
    '.BB.',
  ];

  // Offset del martillo relativo al body (mano derecha de Mario).
  const HAMMER_X = 9;
  const HAMMER_Y = 1;

  type Pixel = { x: number; y: number; fill: string };

  function toPixels(
    rows: string[],
    palette: Record<string, string>,
    ox = 0,
    oy = 0,
  ): Pixel[] {
    const out: Pixel[] = [];
    for (let r = 0; r < rows.length; r++) {
      for (let c = 0; c < rows[r].length; c++) {
        const ch = rows[r][c];
        const fill = palette[ch];
        if (!fill) continue;
        out.push({ x: ox + c, y: oy + r, fill });
      }
    }
    return out;
  }

  const bodyPixels = toPixels(MARIO_BODY, P);
  const hammerPixels = toPixels(HAMMER, P, HAMMER_X, HAMMER_Y);
</script>

<svg
  class="px"
  class:running
  viewBox="-2 0 18 14"
  width="16"
  height="16"
  aria-hidden="true"
  focusable="false"
>
  <g class="mario-body">
    {#each bodyPixels as px, i (i)}
      <rect x={px.x} y={px.y} width="1" height="1" fill={px.fill} />
    {/each}
  </g>
  <g class="hammer">
    {#each hammerPixels as px, i (i)}
      <rect x={px.x} y={px.y} width="1" height="1" fill={px.fill} />
    {/each}
  </g>
</svg>

<style>
  .px {
    shape-rendering: crispEdges;
    display: block;
    flex-shrink: 0;
  }

  /* Solo anima si running. Done = estático. */
  .px.running .mario-body {
    transform-box: fill-box;
    transform-origin: 50% 100%;
    animation: tool-icon-bob 0.44s ease-in-out infinite alternate;
  }

  .px.running .hammer {
    transform-box: fill-box;
    transform-origin: 20% 90%;
    animation: tool-icon-swing 0.22s ease-in-out infinite alternate;
    will-change: transform;
  }

  @keyframes tool-icon-bob {
    from {
      transform: translateY(0);
    }
    to {
      transform: translateY(-0.6px);
    }
  }

  @keyframes tool-icon-swing {
    from {
      transform: rotate(-75deg);
    }
    to {
      transform: rotate(75deg);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .px.running .mario-body,
    .px.running .hammer {
      animation: none;
    }
  }
</style>
