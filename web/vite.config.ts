import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// The API port. Defaults to 8000, but 8000 is often taken (Docker, among
// others), so `make dev API_PORT=8010` passes the real one through.
const apiPort = process.env['API_PORT'] ?? '8000'

export default defineConfig(({ mode }) => {
  // Loaded with an empty prefix so real environment variables are seen, not
  // only what a .env file holds -- Vercel supplies this one as a build env var.
  const env = loadEnv(mode, process.cwd(), '')

  // A production build without it would SUCCEED and then not work: the bundle
  // falls back to same-origin, so every /api call hits whatever host served the
  // page and 404s. That failure surfaces to a user as a dead app with a green
  // deployment behind it, which is the worst shape a failure can take. Fail
  // here instead, where somebody is watching a build log.
  if (mode === 'production' && !env['VITE_API_URL']) {
    throw new Error(
      'VITE_API_URL is not set.\n' +
        'A production build without it ships a frontend that calls its own origin ' +
        'for /api and 404s on every request.\n' +
        'Set it to the API base URL, e.g. VITE_API_URL=https://rxconcile.up.railway.app',
    )
  }

  return {
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
  }
})
