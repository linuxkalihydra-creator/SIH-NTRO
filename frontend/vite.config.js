import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend serves the built dashboard from `/` and its API under `/api`.
// In development the dashboard runs on Vite's port, so `/api` is proxied to the
// backend and the client can keep using the same relative base URL.
const BACKEND = process.env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Relative asset URLs: the build works from `/` and from any sub-path or static host.
  base: './',
  server: {
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    // Cytoscape is loaded on demand as its own chunk; it is large but only fetched for graphs.
    chunkSizeWarningLimit: 700,
  },
})
