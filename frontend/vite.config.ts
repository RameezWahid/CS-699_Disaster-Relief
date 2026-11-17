import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // In development, send API calls to the backend (nginx does this in the container).
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
