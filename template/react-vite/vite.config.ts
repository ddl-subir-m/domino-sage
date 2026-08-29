import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Every runtime dependency this template ships, read from package.json rather than listed here so
// the two cannot drift: a dependency added to the template and forgotten here would reintroduce
// exactly the bug the `optimizeDeps.include` below exists to prevent.
const shipped = Object.keys(
  (JSON.parse(
    readFileSync(fileURLToPath(new URL("./package.json", import.meta.url)), "utf8"),
  ) as { dependencies?: Record<string, string> }).dependencies ?? {},
);

// Two very different runtimes serve this app, so `base` differs by Vite command:
//
//   serve (dev preview, Phase 1) — behind sage's OWN preview proxy at a KNOWN prefix
//     (SAGE_BASE_PREFIX, injected by the supervisor; empty locally). `base` must be the full path
//     the browser uses so Vite bakes correct asset/HMR URLs; with a prefix, HMR dials back through
//     Domino's TLS termination (wss on 443 at that same path).
//   build (published Domino App, Phase 5) — behind Domino's APP proxy at a prefix NOT known at
//     build time, and not even fixed per deployment: the same app answers under /apps/<uuid>/,
//     /apps-internal/<id>/ and /u/<owner>/<project>/app/, so it depends on the link the viewer
//     clicked. Hence a relative `base`. Relative resolves against the page's DIRECTORY, so on a route
//     two segments deep it asked one directory too deep (#18); `serve.py` fixes that at request time
//     by stamping a <base href> into index.html — see the mount-prefix shim there, and `appBase.ts`
//     for the router half of the same problem.
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
    // EVERY shipped dependency, not just React — because the trigger is DISCOVERY, not steady state.
    //
    // The starter app imports react and react-dom and nothing else, so a cold start pre-bundles only
    // those. The first time the agent writes `import { Search } from "lucide-react"`, Vite meets a
    // dependency it has never optimized, re-runs the optimizer, and replaces the shared runtime
    // chunk. The open preview is then holding modules from a graph that no longer exists: every
    // symbol in flight becomes `ReferenceError: X is not defined`, and the freshly optimized chunk
    // carries its own React, so `useContext` is null and every hook throws "Invalid hook call".
    //
    // Verified 2026-08-24 by doing it: `dependency optimized: lucide-react` / `optimized dependencies
    // changed. reloading`, with the rolldown runtime hash changing across the pass. The dedupe above
    // fixes the steady state and cannot help here, because the second React arrives WITH the new
    // pass. Vite reloads the page afterwards, but the errors are already on their way to the agent
    // via reportRuntimeError — which is how a build spent a long turn hunting a missing import that
    // was never missing, while tsc and `vite build` both passed.
    //
    // Listing them all costs a slower first preview (one optimize pass over the whole set instead of
    // the two libraries the placeholder uses). That is affordable exactly because the set is curated
    // and closed: the agent cannot install anything, so this list is the complete one.
    optimizeDeps: {
      include: [...new Set([...shipped, "react-dom/client",
                            "react/jsx-runtime", "react/jsx-dev-runtime"])],
    },
    server: {
      host: true,
      allowedHosts: true,
      hmr: prefix ? { protocol: "wss", clientPort: 443, path: base } : undefined,
    },
  };
});
