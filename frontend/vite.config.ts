import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/chat': 'http://127.0.0.1:8000',
      '/sessions': 'http://127.0.0.1:8000',
      '/clear': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      '/voice': 'http://127.0.0.1:8000',
      '/transcribe': 'http://127.0.0.1:8000',
      '/auth/config': 'http://127.0.0.1:8000',
    },
  },
  test: { environment: 'jsdom', css: false },
})
