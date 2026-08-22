.PHONY: setup test shim opencode lock clean

# One-command reproducible setup (lockfile-driven).
setup:
	npm ci
	cd backend && uv sync --extra dev

# Backend tests. -n auto fans them across cores. Drop it to read interleaved output or to run
# a single failure under a debugger: `cd backend && uv run pytest -q <nodeid>`.
test:
	cd backend && uv run pytest -q -n auto

# Run the enforcement shim alone (FakeGateway unless GATEWAY_BASE_URL/KEY are set).
shim:
	cd backend && uv run uvicorn sage.shim.app:app --port 8080 --reload

# Run the full orchestrator: control API + /v1 shim (:8080) and preview proxy (:8090).
orchestrator:
	cd backend && uv run python -m sage.orchestrator.app

# Preflight: one real completion through the gateway (confirms provider + key before a build).
probe:
	cd backend && uv run python -m sage.tools.probe

# OpenCode coding harness.
opencode:
	npx opencode

# Refresh lockfiles after changing dependencies.
lock:
	npm install
	cd backend && uv lock

clean:
	rm -rf node_modules backend/.venv
