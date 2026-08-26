import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
// defineConfig de vitest/config = la de vite + el bloque `test` tipado.
import { defineConfig } from 'vitest/config';

// `changeOrigin: false` mantiene el Host header en :5173 (vista del browser).
// SameSite=Lax del backend acepta la cookie porque el browser ve mismo origen
// vía el proxy. Si la cookie no llegaba, cambiar a true y reportar.
export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  // vitest: jsdom global (los tests de stores son agnósticos del entorno;
  // los de componentes/editor necesitan DOM). vite dev/build ignoran `test`.
  test: {
    environment: 'jsdom'
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: false,
        secure: false
      }
    }
  }
});
