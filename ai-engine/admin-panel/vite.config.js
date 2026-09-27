import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // base must match the mount path so built asset URLs resolve correctly
  base: '/admin/',
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:18080',
      '/api/ws': { target: 'ws://localhost:18080', ws: true }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets'
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary'],
      // Scoped to the logic layer: the store, the fetch wrapper, the config
      // helpers and the hooks. The .jsx pages are presentational and are NOT
      // covered by this suite — including them would drag the percentage down
      // to a number no threshold could usefully gate, which is worse than
      // saying plainly that they are untested.
      include: [
        'src/store.js',
        'src/fetch.js',
        'src/config.js',
        'src/hooks/**/*.js',
      ],
      // Floors, not targets — the same convention as ai-engine/.coveragerc.
      // The suite currently measures 100% on all four metrics; these sit
      // below that on purpose so a single new branch landing slightly ahead
      // of its test does not wedge CI. Ratchet them up toward 100 if that
      // slack ever gets used as an excuse.
      thresholds: {
        statements: 90,
        branches: 85,
        functions: 90,
        lines: 90,
      },
    },
  },
})
