import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Served behind sage's preview proxy under a path prefix (Phase 1). SAGE_BASE_PREFIX is the Domino
// proxy prefix (empty locally), injected by the supervisor. `base` must be the FULL path the
// browser uses so Vite bakes correct asset/HMR URLs; when there's a prefix, HMR must dial back
// through Domino's TLS termination (wss on 443 at that same path). Locally, base is just /preview/
// and HMR stays on its defaults.
//   - host true         -> reachable from the proxy inside the container
//   - allowedHosts true -> accept the proxy's Host header
//   - strictPort false (default) -> Vite may auto-increment; the supervisor DISCOVERS the real port
const prefix = (process.env.SAGE_BASE_PREFIX || "").replace(/\/$/, "");
const base = `${prefix}/preview/`;

export default defineConfig({
  plugins: [react()],
  base,
  server: {
    host: true,
    allowedHosts: true,
    hmr: prefix ? { protocol: "wss", clientPort: 443, path: base } : undefined,
  },
});
