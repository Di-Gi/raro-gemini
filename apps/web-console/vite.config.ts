// [[RARO]]/apps/web-console/vite.config.ts
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import path from 'path'

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: path.resolve(__dirname, './src/lib'),
      $components: path.resolve(__dirname, './src/components'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0', // Allow access from outside container
    proxy: {
      // Proxy /api to Rust Kernel (HTTP)
      // Use 'kernel' (Docker service name) when running in Docker
      // Use 'localhost' when running locally
      '/api': {
        target: process.env.DOCKER_ENV === 'true' ? 'http://kernel:3000' : 'http://localhost:3000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },

      // Proxy /ws to Rust Kernel (WebSocket) - CRITICAL FOR REAL-TIME UPDATES
      '/ws': {
        target: process.env.DOCKER_ENV === 'true' ? 'ws://kernel:3000' : 'ws://localhost:3000',
        ws: true,  // Enable WebSocket proxying
        changeOrigin: true,
      },

      // Proxy /agent-api to Python Agent Service
      '/agent-api': {
        target: process.env.DOCKER_ENV === 'true' ? 'http://agents:8000' : 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent-api/, ''),
      },
    },
  },
})