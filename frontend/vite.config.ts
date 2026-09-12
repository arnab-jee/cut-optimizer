import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on all interfaces, not just localhost, so the dev server is reachable from other
    // devices on the same (private) network -- e.g. over Tailscale, for factory-floor access.
    // allowedHosts: true skips Vite's Host-header allowlist check, which otherwise rejects
    // requests arriving via a non-localhost hostname (like a Tailscale MagicDNS name).
    host: true,
    port: 5174,
    strictPort: true,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
