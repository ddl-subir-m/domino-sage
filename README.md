# sage — Domino AI App Builder

A Domino-hosted, Replit/Cursor-style builder: a per-user container running a coding agent
(OpenCode), a live React+Vite preview, an IDE-mode escape hatch, and an LLM router that switches
between vendor and sovereign models through the Domino AI Gateway.

See `SPEC.md` (what) · `DESIGN.md` (module seams) · `PLAN.md` (execution plan) ·
`diagrams/` (architecture) · `gateway-questions.md` (open items for the gateway team).

## Prerequisites

- **Node ≥ 20** (for the OpenCode coding harness)
- **Python ≥ 3.11**
- **uv** — Python package/venv manager (https://docs.astral.sh/uv/). `brew install uv`.

## Setup (any machine)

```bash
make setup
```

This runs `npm ci` (installs the pinned OpenCode from `package-lock.json`) and `uv sync`
(creates `backend/.venv` and installs pinned backend deps from `backend/uv.lock`). Both are
lockfile-driven, so every machine gets identical versions.

Manual equivalent:
```bash
npm ci                                   # OpenCode harness (pinned)
cd backend && uv sync --extra dev        # backend deps + venv (pinned)
```

## Run

```bash
make test        # backend unit + integration tests
make shim        # run the enforcement shim on :8080 (FakeGateway unless creds set)
make opencode    # run the OpenCode harness (opencode --version to check)
```

### Enforcement shim
The shim is the OpenAI-compatible endpoint OpenCode points at. It resolves the model policy,
overrides the model when the sensitivity lock is on, tags every request (project + phase), and
forwards to the gateway.

```bash
make shim
curl -s -XPOST localhost:8080/v1/chat/completions \
  -H 'X-Sage-Project: demo' \
  -d '{"model":"x","messages":[]}'
```

Copy `.env.example` → `backend/.env` and fill in the gateway creds; the shim loads it
automatically (via python-dotenv). Leave the gateway vars blank to use the in-process fake.

### Orchestrator (the assembled builder)
`make orchestrator` runs the whole thing in one process: the control API + `/v1` shim + the
**thin UI** on `:8080` and the preview proxy on `:8090`. Open **http://localhost:8080/** for the
UI (create a project, set the model / force sovereign, build, typecheck, live preview). Or drive
it over HTTP:
```bash
make orchestrator
curl -XPOST localhost:8080/api/projects -d '{"id":"demo"}'      # creates workspace + starts Vite
open http://localhost:8090/                                     # live preview of the app
curl -XPOST localhost:8080/api/projects/demo/model -d '{"lock":true}'   # force sovereign
curl -XPOST localhost:8080/api/projects/demo/build -d '{"prompt":"build a todo app"}'  # agent build + auto-typecheck loop
curl -XPOST localhost:8080/api/projects/demo/check  # typecheck the workspace now
```
`build` runs the closed loop (prompt → wait → `tsc` → feed errors back until clean or the
circuit breaker stops). It needs gateway access, so run it in a Domino workspace. The
orchestrator starts one `opencode serve` internally and drives it via its HTTP API + SSE.
`/api/projects/{id}/model` accepts `{mode, phase, pick, lock}`. The sensitivity lock is sticky.

### OpenCode → shim
`opencode.json` (repo root) defines a `sage-gateway` provider pointed at the shim
(`http://localhost:8080/v1`) with the gateway aliases as models. Run `make shim` then
`make opencode` and pick a model like `sage-gateway/qwen-2-5`. OpenCode never talks to the
gateway directly — every call goes through the shim (that's what the Step 1.2 spike verifies).

Environment (unset → uses an in-process fake so curl works with no creds):
| Var | Purpose |
|-----|---------|
| `GATEWAY_BASE_URL` | Domino AI Gateway base URL → switches to the real client |
| `GATEWAY_API_KEY`  | gateway auth (scheme pending gateway-questions Q7) |
| `SAGE_GATEWAY_UI_URL` | override for the "Cost & activity" link; defaults to `GATEWAY_BASE_URL` minus `/v1`. Set it when the browser-facing gateway URL differs from the inference one |
| `SAGE_MANAGE_URL` | absolute URL for the platform bar's "Manage" link. Normally blank: the bar resolves `/apps/sage-manage` against the host the page came from (minus a leading `apps.`), which is this deployment's own. Set it only where Manage lives somewhere that grammar does not reach |
| `SAGE_MODEL_SOVEREIGN` / `_PLAN` / `_IMPLEMENT` / `_DEFAULT` | model ids per tier |
| `SAGE_DEBUG_STREAM` | `1` logs raw SSE chunks from the gateway to `/api/diag` (first 40 per stream), for diagnosing an "Invalid … stream event" from OpenCode, which reports no payload. Sets the initial state only — toggle at runtime with `POST /api/diag/debug-stream {"on": true}`, since on Domino this value is baked at image build time. Verbose, and the chunks contain prompt text |

## Run in a Domino workspace (live spike)

The sovereign/gateway spike is meant to run inside a Domino workspace on cloud-dogfood, where
the token sidecar (`:8899`) works with no static key.

```bash
git clone git@github.com:ddl-subir-m/domino-sage.git && cd domino-sage
cp .env.example backend/.env          # keep GATEWAY_API_KEY BLANK -> sidecar auth is automatic
make setup                            # needs Node >=20, Python >=3.11, uv (see note)
make shim                             # terminal 1: shim on :8080, sidecar token per request
make opencode                         # terminal 2: pick a model like sage-gateway/qwen-2-5
```

Toolchain note: Domino workspaces are Python-first, so **Node ≥20 and `uv` may not be
pre-installed**. If `make setup` fails: install uv (`pip install uv` or the astral installer) and
Node ≥20 (nvm or the platform's package manager) in the workspace first. The gateway base URL is
already in `.env.example`; do not set a token in a workspace — the sidecar handles it.

## Layout

```
backend/                 Python backend (uv-managed)
  sage/router/           Seam 1: LLMRouter (pure) + ModelControl (state owner)
  sage/shim/             Seam 2: EnforcementShim + FastAPI app entrypoint
  sage/gateway/          Seam 2: GatewayClient (Fake + Domino) adapter
  sage/driver/           Seam 3: AgentDriver + OpenCodeDriver (spike stub)
  tests/                 router precedence + shim policy tests
spikes/                  Phase-0 spike procedures (start with step1-routing-coverage.md)
diagrams/                architecture (mmd/svg/png/excalidraw)
package.json             OpenCode harness (pinned via package-lock.json)
```

## Status

Phase-0 spike (see `PLAN.md`). Router + shim policy done and tested; `DominoGatewayClient.route`
implemented but awaits a live gateway URL/key to verify; OpenCode routing-coverage probe (Step
1.2) is the make-or-break next test.
