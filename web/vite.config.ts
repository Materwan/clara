import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// Paths are relative to web/ (npm runs scripts there). The site is built at release time into the Python package (src/clara/web/dist): the machine needs no Node.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: '../src/clara/web/dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    assetsInlineLimit: 0, // no data: URIs: the site's CSP allows fonts and images from itself only
    sourcemap: false,
    cssCodeSplit: true,
    rollupOptions: {
      input: { main: 'index.html', picker: 'picker.html' },
    },
  },
  server: { proxy: { '/v1': 'http://127.0.0.1:8765', '/health': 'http://127.0.0.1:8765' } },
});
