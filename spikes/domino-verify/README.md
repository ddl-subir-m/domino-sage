# Phase 1 verification — real orchestrator, one port, under Domino's proxy prefix

Proves the Phase-1 code (single-port collapse + prefix threading) works on real Domino infra —
the same launch you did for the Phase-0 spike, but pointed at the actual orchestrator.

## Prereqs (Environment image)

The image needs **Node ≥20.19 (recommend Node 22 LTS) + uv**. The template pins `vite@^8`, which
hard-fails on older Node — the spike's `setup_20.x` layer installs 20.18.3, which is **too old**.
Update the Environment Dockerfile to Node 22 and rebuild:

```dockerfile
USER root
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv
USER ubuntu
```

Nothing else for the Tier-1 preview/HMR check — the gateway and OpenCode are NOT needed there
(OpenCode starts lazily on the first *build*). For a real *build* (Tier 2), `run.sh` installs the
repo-root deps so the `opencode` binary (`opencode-ai`) resolves; the gateway still has to be wired
for the model calls.

Clone this repo into the Domino project so the files land on the mount at `/mnt/code`
(confirmed mount path from Phase-0 STEP 2). The pluggable tool's `start` points at
`/mnt/code/spikes/domino-verify/run.sh`.

## Launch

1. Add the tool from `pluggable-tools.yaml` (tool key `sageVerify`, port 8888, `rewrite:false`).
2. Start a workspace with that tool on the latest commit of `feat/domino-workspace-builder`.
3. First boot installs template deps + syncs Python deps (~1–2 min); watch the logs for uvicorn's
   `Uvicorn running on http://0.0.0.0:8888`.

## Open it

The tool serves under Domino's prefix — open the workspace tool from the Domino UI (the same way
you opened the spike). You should land on the **sage builder UI**.

## What to check (Tier 1 — the pure Phase-1 mechanic; no gateway)

- [ ] **Builder loads under the prefix.** The page renders; the browser URL is
      `…/<owner>/<project>/notebookSession/<runId>/`. Open devtools → Network: the `api/*` calls
      go to that prefixed path and return 200 (this exercises the runtime `BASE` constant + the
      prefix-stripping middleware).
- [ ] **Preview renders through the double proxy.** Create a project in the UI (any starter). The
      preview iframe should show the template React app — served from Vite under
      `<prefix>/preview/` on the **same** port (no `:8090`).
- [ ] **HMR hot-reloads without losing state.** In a terminal (or a second tool), edit the running
      workspace's app source and save, e.g.:
      ```bash
      # workspace lives under $SAGE_WORKSPACES (default /tmp/sage-workspaces/<projectId>)
      f=$(ls -d /tmp/sage-workspaces/*/src/App.tsx | head -1)
      sed -i 's/<\/h1>/ · edited<\/h1>/' "$f"   # or just touch/edit any visible text
      ```
      The preview should update **in place** (Vite HMR over the `wss` channel on 443), not do a
      full reload. That confirms `base` + HMR routing through Domino's TLS termination.

## Tier 2 (optional — needs the gateway wired)

Set `GATEWAY_BASE_URL` (+ creds) in `backend/.env`, then drive a real build from the UI prompt box
and watch the generated edits hot-reload in the preview. This is end-to-end and not required to
sign off Phase 1.

## If something fails

- **"Could not create the app" + a RuntimeError in the logs** → Vite exited before reporting a
  port; the error now includes Vite's own recent output. `EBADENGINE … required: node ^20.19` there
  means the Node bump above wasn't applied.
- **502 in the preview iframe** with a "Vite not reachable" JSON → Vite hasn't bound yet (first
  `npm install`) or template deps missing. Check logs for Vite's `Local:` line.
- **Preview 404 / blank** → prefix mismatch. Send back the builder page's URL + one failing
  `api/*` request URL from devtools; compare against the env-derived prefix printed in the logs.
- **Build fails with an `opencode` error** → the repo-root deps didn't install, so `npx opencode
  serve` can't resolve the binary. Check the `[verify] opencode: …` line in the logs (should print a
  version, not `<not resolvable>`); if it's missing, the root `npm install` failed — look just above.
- **Assets load but no live reload** → HMR ws blocked. Send the failing `wss://…/preview/` request
  from devtools; we adjust `hmr` in `template/react-vite/vite.config.ts`.
