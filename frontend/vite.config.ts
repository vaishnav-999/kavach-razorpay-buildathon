import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// BUILD_SPEC §14 — the bundle is written into app/static/ so FastAPI serves it
// from '/'. There is no separate frontend deploy.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../app/static',
    // The directory is wiped on every build, so app/static/.gitkeep — which is
    // committed, and is what keeps the mount point in the repo — is shipped
    // from frontend/public/ and rewritten by each build rather than deleted.
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` talks to a locally running uvicorn. Production serves the
    // built bundle from the same origin, so no proxy exists there.
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/merchant': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
})
