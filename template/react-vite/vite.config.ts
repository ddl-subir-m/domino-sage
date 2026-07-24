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
  // The agent installs charting/UI libs (recharts, etc.) into an already-running dev server. Vite's
  // dep pre-bundler can then resolve a SECOND copy of React inside that lib's optimized chunk, so
  // hooks blow up with "Invalid hook call / null useContext" (dev only — production/Rollup dedupes).
  // Force a single React instance and keep it in the same optimize pass so pre-bundled deps share it.
  resolve: { dedupe: ["react", "react-dom"] },
  optimizeDeps: { include: ["react", "react-dom", "react/jsx-runtime"] },
  server: {
    host: true,
    allowedHosts: true,
    hmr: prefix ? { protocol: "wss", clientPort: 443, path: base } : undefined,
  },
});
