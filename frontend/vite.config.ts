import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend defaults to :8000. Override with VITE_API_BASE when running the
// API elsewhere; the dev proxy keeps the browser on one origin so the SSE
// stream is not treated as cross-site.
const API_BASE = process.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: API_BASE,
        changeOrigin: true,
        // SSE must not be buffered or the agent trace arrives in one lump at
        // the end of the run instead of streaming.
        ws: false,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform'
            }
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // three.js and the force-graph renderer dominate the bundle and never
        // change between app deploys, so they get their own long-lived chunks.
        manualChunks: {
          three: ['three'],
          forcegraph: ['react-force-graph-3d'],
          react: ['react', 'react-dom'],
        },
      },
    },
  },
})
