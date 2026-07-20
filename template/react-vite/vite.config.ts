import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tuned for running behind sage's preview proxy (Step 3.3):
//  - host true  -> reachable from the proxy inside the container
//  - strictPort false (default) -> Vite may auto-increment; the proxy DISCOVERS the real port
//  - hmr.clientPort injected by the supervisor so live-reload works through the proxy
//  - allowedHosts true -> accept the proxy's Host header
const hmrClientPort = process.env.SAGE_HMR_CLIENT_PORT
  ? Number(process.env.SAGE_HMR_CLIENT_PORT)
  : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    allowedHosts: true,
    hmr: hmrClientPort ? { clientPort: hmrClientPort } : undefined,
  },
});
