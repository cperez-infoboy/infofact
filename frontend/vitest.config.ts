import { defineConfig } from 'vitest/config';
import { sveltekit } from '@sveltejs/kit/vite';

// Config dedicada a vitest: vite.config.ts (build) queda libre del campo
// `test`. Vitest requiere plugins de vite (sveltekit) para resolver $lib.
export default defineConfig({
  plugins: [sveltekit()],
  test: {
    // Testeo de stores puros (TS plano) — sin DOM; jsdom como entorno seguro
    // por si alguna dependencia toca window/globalThis al importarse.
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
    coverage: {
      reporter: ['text', 'json-summary']
    }
  }
});
