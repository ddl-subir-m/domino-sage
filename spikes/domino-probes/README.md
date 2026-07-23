# Phase-0 probes — control plane (0.3) + gateway (0.4)

Two standalone probes to finish Phase 0 while you're in a live Domino workspace. Both auth via
the workspace sidecar (`DOMINO_API_PROXY` → `:8899/access-token`), print raw responses, and never
hard-fail on a single call. Run from the workspace terminal (or via a `bash -lc` tool `start`).

## 0.3 — control-plane payloads (Phase 4 hub)

```bash
cd /mnt/code/spikes/domino-probes
uv run --with httpx control_plane.py                  # discovery only (safe GETs)
PROBE_CREATE=1 uv run --with httpx control_plane.py    # + create & auto-delete a throwaway project
```

What we're after: the **request bodies** for `POST /projects` and `POST /workspace/.../workspace`
+ `/sessions` (undocumented in swagger). The create bodies in the script are best guesses — if
they 4xx, the printed error tells us the required fields and we iterate. Discovery GETs also
reveal the response shapes and the `workspaceDefinitionId` the tool uses.

⚠️ If `/v4/...` calls 404 across the board, the base path is wrong — tell me and I'll adjust
(`API = f"{API_HOST}/v4"` at the top of the script).

## 0.4 — gateway (close gateway-questions.md)

```bash
cd /mnt/code/spikes/domino-probes
GATEWAY_BASE_URL=https://<host>/apps/<id>/v1 uv run --with httpx gateway.py
```

Fill `GATEWAY_BASE_URL` from `.env.example` / gateway-questions.md (`https://<host>/apps/<id>/v1`).
Confirms: auth works, sovereign models are listed, one real tagged completion succeeds, and the
`/api/usage/mine` cost shape (the script tries a few likely paths).

## What to send back

The full stdout of each. From 0.3: the accepted create bodies (or the 4xx errors) + the project
list shape. From 0.4: the `/models` list, the completion status, and whichever usage path returned
200 with its JSON.
