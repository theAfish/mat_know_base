import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.API_PORT || '8000'}`,
        changeOrigin: true,
        proxyTimeout: 600_000,
        timeout: 600_000,
      },
    },
  },
})
