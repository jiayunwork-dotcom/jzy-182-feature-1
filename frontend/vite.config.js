import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes straight into the backend static directory so the
// python:3.12-slim runtime image serves the SPA from FastAPI.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
