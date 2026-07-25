import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// `changeOrigin: false` mantiene el Host header en :5173 (vista del browser).
// SameSite=Lax del backend acepta la cookie porque el browser ve mismo origen
// vía el proxy. Si la cookie no llegaba, cambiar a true y reportar.
export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
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
