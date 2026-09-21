import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on 8000 and the dev server on 5173. Proxying /api keeps the
// browser on one origin in development, so the code never needs a base URL
// that differs between dev and the nginx container that serves the build.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // Sourcemaps ship: this is a small internal cabinet, and a stack trace
    // from a tester is worth more than hiding the bundle's shape.
    sourcemap: true,
  },
})
