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

// Vite's own error overlay tells the truth and still misleads about whose fault it is. During a build
// the agent writes the app one file at a time, so `App.tsx` routinely lands holding imports for
// components that are still several writes away — `[plugin:vite:import-analysis] Failed to resolve
// import "./components/RowDetail"`, live on 2026-08-31. The dev server is right, the file really is
// not there yet, and it arrives seconds later. What the creator sees in the meantime is a full-screen
// red stack trace over an app that is merely mid-write, once per failing write.
//
// So the overlay asks the builder whether a turn is running — the same question the crash card in
// `ErrorBoundary.tsx` asks, against the same endpoint — and stands down only then. Two deliberate
// differences from that card:
//
//   - This one hides FIRST and reveals on a negative answer, where the card starts blunt and softens.
//     The card is raised once per crash, so a moment of the honest version costs nothing there; this
//     overlay is raised on every failing write, so starting blunt IS the red flash being removed. The
//     window is one same-origin fetch, and every failure path still reveals.
//   - It lives here rather than in `src/`, because a transform error takes the app's whole module
//     graph down with it: `main.tsx` never evaluates, so nothing imported from it can be watching.
//     An inline script in <head> is a separate graph and survives the failure it has to react to.
//
// What it does NOT do is hide a working render behind a card. On HMR the previous modules are still
// live, so hiding the overlay uncovers the app the creator had; the card goes up only when `#root` is
// empty, which is the cold-load case where there is nothing behind the overlay to uncover.
//
// Serve only, for the reason the reporter is DEV-only: a published App has no builder to ask.
function buildAwareOverlay(base: string) {
  // Derived from `base` exactly as `src/reportRuntimeError.ts` derives it, and same-origin for the
  // same reason: dev preview and control app sit behind the one proxy.
  const api = base.replace(/preview\/?$/, "") + "api/";
  return {
    name: "sage-build-aware-overlay",
    apply: "serve" as const,
    transformIndexHtml() {
      return [{
        tag: "script",
        injectTo: "head-prepend" as const,
        children: `
(function () {
  var API = ${JSON.stringify(api)};
  var POLL_MS = 2000; // the interval the crash card uses
  var overlay = null, card = null, timer = null;

  // Every failure path answers "no", which reveals Vite's overlay. That is the safe direction to be
  // wrong in: hiding a real error the creator has to act on would be the damaging one.
  function buildIsRunning() {
    return fetch(API + "project/build/state")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) { return !!(b && b.running); })
      .catch(function () { return false; });
  }

  function stop() {
    if (timer !== null) { clearInterval(timer); timer = null; }
    if (card) { card.remove(); card = null; }
  }

  function reveal() {
    stop();
    if (overlay) overlay.style.display = "";
  }

  function calmCard() {
    var el = document.createElement("div");
    el.setAttribute("data-sage-building", "");
    el.style.cssText = "position:fixed;inset:0;z-index:99999;display:flex;align-items:center;" +
      "justify-content:center;padding:24px;box-sizing:border-box;background:#fff;" +
      "font:14px/1.5 system-ui,sans-serif;color:#3F4547";
    var box = document.createElement("div");
    box.style.cssText = "max-width:520px;border:1px solid #DBE4E8;border-radius:6px;padding:24px";
    var h = document.createElement("h2");
    h.style.cssText = "margin:0 0 8px;font-size:20px";
    h.textContent = "Sage is still building this app";
    var p = document.createElement("p");
    p.style.cssText = "margin:0;color:#7F8385";
    p.textContent = "The agent is part-way through writing these files, so this error is expected. " +
      "The preview reloads on its own when the rest of the code lands.";
    box.appendChild(h); box.appendChild(p); el.appendChild(box);
    return el;
  }

  function onOverlay(el) {
    overlay = el;
    el.style.display = "none"; // before paint: the observer runs as a microtask on insertion
    buildIsRunning().then(function (running) {
      if (overlay !== el) return;      // a newer overlay replaced this one
      if (!running) { reveal(); return; }
      var root = document.getElementById("root");
      if (!card && root && root.childElementCount === 0) {
        card = calmCard();
        document.body.appendChild(card);
      }
      // Sage retries a broken build a bounded number of times, so a build can end with the error
      // still standing. When it does, the error is the creator's to act on and the overlay comes back.
      if (timer === null) {
        timer = setInterval(function () {
          buildIsRunning().then(function (still) { if (!still) reveal(); });
        }, POLL_MS);
      }
    });
  }

  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var added = records[i].addedNodes, removed = records[i].removedNodes;
      for (var a = 0; a < added.length; a++) {
        if (added[a].localName === "vite-error-overlay") onOverlay(added[a]);
      }
      for (var d = 0; d < removed.length; d++) {
        // Vite cleared its own overlay, so the error is gone and the card has nothing left to explain.
        if (removed[d] === overlay) { overlay = null; stop(); }
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
`,
      }];
    },
  };
}

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
    plugins: [react(), buildAwareOverlay(base)],
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
