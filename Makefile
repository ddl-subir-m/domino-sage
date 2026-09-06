.PHONY: setup test lint shim opencode lock clean

# One-command reproducible setup (lockfile-driven).
setup:
	npm ci
	cd backend && uv sync --extra dev

# Backend tests. -n auto fans them across cores. Drop it to read interleaved output or to run
# a single failure under a debugger: `cd backend && uv run --extra dev pytest -q <nodeid>`.
#
# `--extra dev` rather than relying on `make setup` having run: pytest lives in that extra, and a
# fresh checkout or git worktree has a venv built from the default dependencies alone. Without it
# `uv run pytest` does not run a smaller suite, it fails to spawn at all — and piped through
# anything that swallows the exit code, that failure reads as a pass (#166).
test:
	cd backend && uv run --extra dev pytest -q -n auto

# Lint. Ruff is pinned exactly (see `required-version` in backend/pyproject.toml), so this and CI
# cannot disagree about what counts as clean.
lint:
	cd backend && uv run --extra dev ruff check

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
