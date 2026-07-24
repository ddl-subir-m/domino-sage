import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Two very different runtimes serve this app, so `base` differs by Vite command:
//
//   serve (dev preview, Phase 1) — behind sage's OWN preview proxy at a KNOWN prefix
//     (SAGE_BASE_PREFIX, injected by the supervisor; empty locally). `base` must be the full path
//     the browser uses so Vite bakes correct asset/HMR URLs; with a prefix, HMR dials back through
//     Domino's TLS termination (wss on 443 at that same path).
//   build (published Domino App, Phase 5) — behind Domino's APP proxy at a prefix NOT known at
//     build time. Domino Apps are expected to use relative URLs, so use a relative `base` and let
//     the browser resolve assets under whatever path the App is mounted at.
//
//   - host true         -> reachable from the proxy inside the container
//   - allowedHosts true -> accept the proxy's Host header
//   - strictPort false (default) -> Vite may auto-increment; the supervisor DISCOVERS the real port
export default defineConfig(({ command }) => {
  const prefix = (process.env.SAGE_BASE_PREFIX || "").replace(/\/$/, "");
  const base = command === "build" ? "./" : `${prefix}/preview/`;

  return {
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
  };
});
