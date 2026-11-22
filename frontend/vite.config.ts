import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    // Offline responder app (spec 4.7, demo scope): the app shell is precached, the Mumbai
    // basemap and its fonts are cached on first use, and the API is never cached by the
    // service worker (the app keeps its own copy of tasks and an outbox in localStorage).
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'script',
      manifest: {
        name: 'Mumbai flood relief',
        short_name: 'Flood relief',
        start_url: '/#responder',
        display: 'standalone',
        background_color: '#ffffff',
        theme_color: '#1d4ed8',
        icons: [{ src: '/favicon.svg', sizes: 'any', type: 'image/svg+xml' }],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [
          {
            // PMTiles reads byte ranges; the whole file is cached once and sliced per request.
            urlPattern: /\/basemap\/.*\.pmtiles$/,
            handler: 'CacheFirst',
            options: { cacheName: 'basemap', rangeRequests: true },
          },
          {
            urlPattern: /\/basemap\//,
            handler: 'CacheFirst',
            options: { cacheName: 'basemap-assets' },
          },
        ],
      },
    }),
  ],
  // MapLibre starts its worker with { type: 'module' }.
  worker: { format: 'es' },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
