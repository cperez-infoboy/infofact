# InfoFact — Frontend

SPA SvelteKit (Svelte 5) + Tailwind v4. Auth con cookies HttpOnly contra FastAPI.

## Desarrollo

Requiere backend corriendo en `http://127.0.0.1:8080`.

```bash
npm install
npm run dev
```

El proxy de Vite (`/api/*` → `:8080`) hace que la cookie same-origin viaje sin CORS.

## Build

```bash
npm run build       # genera build/ con index.html (SPA fallback)
npm run preview     # sirve el build localmente
npm run check       # svelte-check (tipos)
```

En producción, FastAPI sirve `build/` estáticamente desde el mismo origen.
