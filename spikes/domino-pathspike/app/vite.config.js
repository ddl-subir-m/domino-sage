import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// SAGE_BASE_PREFIX is Domino's proxy prefix discovered in STEP 2 (e.g.
// "/sub-user/sage-spike/abc123/1"). Vite must (a) emit asset URLs under <prefix>/preview/
// and (b) dial its HMR websocket at that same public path, because once Domino's proxy is in
// front, that is the browser-visible origin path. The FastAPI proxy forwards the identical
// path through to this dev server.
const prefix = (process.env.SAGE_BASE_PREFIX || '').replace(/\/$/, '')
const base = `${prefix}/preview/`

export default defineConfig({
  plugins: [react()],
  base,
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    allowedHosts: true, // accept the Domino workspace hostname on proxied requests
    hmr: {
      protocol: 'wss',  // Domino serves the workspace over https
      clientPort: 443,  // browser dials wss://<domino-host>:443
      path: base,       // ...at <prefix>/preview/ , which the FastAPI ws proxy forwards to Vite
    },
  },
})
