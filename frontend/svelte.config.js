import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // SPA pura: fallback a index.html para que SvelteKit maneje rutas client-side.
    adapter: adapter({ fallback: 'index.html' }),
    alias: { $lib: 'src/lib' }
  }
};

export default config;
