import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API port. Defaults to 8000, but 8000 is often taken (Docker, among
// others), so `make dev API_PORT=8010` passes the real one through.
const apiPort = process.env['API_PORT'] ?? '8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // The browser talks to the dev server only, and the dev server forwards to
    // the API. This means the frontend cannot be pointed at the wrong port by
    // forgetting an environment variable -- a "Failed to fetch" that looks like
    // an application bug but is only a misconfigured origin.
    proxy: {
      '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true },
      '/health': { target: `http://localhost:${apiPort}`, changeOrigin: true },
    },
  },
})
