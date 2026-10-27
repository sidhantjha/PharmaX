import { defineConfig } from 'vite';

const proxy = { '/api/v1': { target: process.env.PHARMAX_API_TARGET || 'http://127.0.0.1:8766' } };

export default defineConfig({
  server: { proxy, watch: process.env.PHARMAX_USE_POLLING === '1' ? { usePolling: true, interval: 500 } : undefined },
  preview: { proxy },
  base: './',
  build: {
    rollupOptions: {
      onwarn(warning, warn) {
        // Client-component directives are expected in this client-only build.
        if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.message.includes('use client')) return;
        warn(warning);
      },
    },
  },
});
