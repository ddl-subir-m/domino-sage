# Phase-0 spike — does Path A work under Domino's workspace proxy?

**Question this answers:** can a Vite dev server **render + hot-reload** inside a Domino
workspace, served under Domino's proxy prefix, through our own FastAPI reverse-proxy — i.e. is
**Path A** (Sage as its own pluggable workspace tool, single port, preview under a subpath)
physically viable? This is the gate for the whole `DEPLOY-PLAN.md`.

Everything here is throwaway. It is NOT the real builder — just the smallest thing that proves
the mechanics.

## What's in here

| File | Role |
|------|------|
| `probe.py` | STEP 2 diagnostic: catch-all that dumps the received path + Domino env vars |
| `server.py` | STEP 3: the real Path-A proxy (UI + `/preview` HTTP + HMR ws), one port |
| `app/` | minimal Vite + React app (a counter, for a visible HMR test) |
| `run.sh` | starts Vite + the proxy on the tool port |
| `pluggable-tools.yaml` | registers this as a "Launch Sage Path Spike" workspace tool |
| `Dockerfile` | additive layers (Node 20 + uv + this dir) for the spike Environment |

---

## STEP 1 — build the spike Environment + get the files onto the project

The spike code lives on the **project file volume**, NOT baked into the image (a Domino
Environment build has no local build context, so `COPY .` can't work — see the Dockerfile note).

1. In Domino, create (or duplicate) an Environment from a standard base image you already use.
2. **Edit Dockerfile** → paste the `RUN` layers from `Dockerfile` (Node 20 + uv). Set `FROM` to
   your base image. That's all the image needs.
3. **Pluggable Workspace Tools** → paste `pluggable-tools.yaml`. ⚠️ First open a standard
   environment's JupyterLab tool block and match its exact key shape — versions differ. The tool
   `start` points at `run.sh` on the project mount.
4. Build the Environment.
5. **Get the files onto the project:** clone this repo into the Domino project so
   `spikes/domino-pathspike/` is on the mount (`git clone git@github.com:ddl-subir-m/domino-sage.git`).
   Confirm the mount prefix in STEP 2 and align the `start` path in `pluggable-tools.yaml`.

## STEP 2 — discover the proxy prefix (diagnostic)

Point the tool `start` at the probe instead of `run.sh` for this one launch — either temporarily
set the tool `start` to:

```
[ "bash", "-lc", "uv run --with fastapi --with 'uvicorn[standard]' uvicorn probe:app --host 0.0.0.0 --port 8888" ]
```

or just launch a normal terminal in the workspace and run that command, then open the tool URL.

**Open the tool in your browser and copy the whole JSON.** The fields I need:

- `received_path` — the path Domino handed our port for the tool root. This is the answer to
  *preserve vs strip*: if it looks like `/<owner>/<project>/<session>/<runId>/`, Domino
  **preserves** the prefix (expected — good). If it's just `/`, it **strips** (tell me, I'll
  adjust `server.py`).
- `domino_env` — which vars carry owner / project / run / session, so `run.sh` can compute
  `SAGE_BASE_PREFIX` automatically instead of hardcoding.

👉 **Report back the JSON before STEP 3.** (If you'd rather push straight through, set
`SAGE_BASE_PREFIX` in `run.sh` to whatever `received_path` shows, minus the trailing slash.)

## STEP 3 — the real render + HMR test

1. Set `SAGE_BASE_PREFIX` in `run.sh` (from STEP 2), point the tool `start` back at
   `/opt/domino/spike/run.sh`, relaunch the workspace.
2. Open the tool. You should see the blue spike header with the counter app in the iframe.
3. **HMR test:** click the counter a few times, then edit `MARKER` in `app/src/App.jsx`
   (e.g. `one` → `two`) and save.

**PASS:** the text updates and the counter **keeps its value** (no full reload). Path A works.

**PARTIAL (app renders, HMR fails):** the counter resets on every edit, or DevTools shows a
failed `wss://…` request. This is the expected hard part — capture and report:
- the failing `wss://` URL from the browser Network tab,
- the Vite startup log line (it prints its hmr config),
- the browser console error.
I'll finalize `hmr.path` / `clientPort` / the ws forwarding from those.

**FAIL (app doesn't render / 404s on `/@vite`, `/src`):** copy the failing asset URLs + the
proxy log. Means `base` / path forwarding needs adjusting.

---

## While you're in there (cheap adjacent probes for the plan)

- **Gateway (0.4):** `curl http://localhost:8899/access-token` — confirm the sidecar returns a
  token; then one completion through the gateway (`cd backend && make probe` with the real
  `GATEWAY_BASE_URL`). Confirms `gateway-questions.md` open items.
- **Control-plane payloads (0.3):** with that token, `POST /projects` and
  `POST /workspace/project/{projectId}/workspace` (+ `/sessions?externalVolumeMounts=…`). Capture
  the request bodies that actually succeed — the swagger doesn't document them.
- **App identity (Phase 4):** once we have a hub App, confirm `:8899` inside a *published App*
  returns the **viewer's** token (you said extended identity propagation covers this) so "New
  app" creates projects as the viewer.

## What each outcome means

- PASS → Path A confirmed; proceed to Phase 1 (do the same threading in the real orchestrator).
- PARTIAL → Path A confirmed for HTTP; we iterate on HMR knobs (bounded, expected).
- FAIL on preserve/strip surprises → we adapt `server.py`; still likely fine.
- Only if we truly can't serve Vite under the prefix at all → reopen Path B (JupyterLab +
  `jupyter-server-proxy` `/proxy/absolute/`).
