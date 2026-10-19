import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Hosted builds call the backend on its own domain: allow it in connect-src
const apiOrigin = (() => {
  try { return new URL(loadEnv('production', process.cwd(), '').VITE_API_BASE || '').origin } catch { return '' }
})()

// Production pages only run this app's own scripts (blocks injected <script> and inline handlers).
// Build-only: the dev server needs inline scripts for hot reload. Serve the same policy as an HTTP
// header from the web server too, with frame-ancestors 'none' (not allowed in a meta tag).
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  `connect-src 'self'${apiOrigin ? ' ' + apiOrigin : ''}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ')

const cspMeta = {
  name: 'csp-meta',
  apply: 'build',
  transformIndexHtml: html =>
    html.replace('<meta charset="UTF-8" />',
      `<meta charset="UTF-8" />\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
}

export default defineConfig({
  plugins: [react(), cspMeta],
  build: { sourcemap: false },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
