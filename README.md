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
| `SAGE_MODEL_SOVEREIGN` / `_PLAN` / `_IMPLEMENT` / `_DEFAULT` | model ids per tier |

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
