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
  }
})
