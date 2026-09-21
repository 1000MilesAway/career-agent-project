import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Separate from vite.config.ts: the dev server's proxy has nothing to do with
// the test run, and a test that reaches a proxy is a test that needs a server.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
    // No live API and no live model, same rule as the Python suite.
    restoreMocks: true,
  },
})
