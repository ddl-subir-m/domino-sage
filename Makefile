.PHONY: setup test test-opencode lint shim opencode lock clean reasoning-evidence

# One-command reproducible setup (lockfile-driven).
#
# The template's deps are installed here and not left to the first preview, because without them
# Vite cannot start and the preview proxy seeds and starts it INSIDE the async handler: every
# /preview/ poll then blocks the event loop until the whole orchestrator stops answering, `GET /`
# included. That reads as "the Workbench hung", nowhere near the missing directory that caused it.
# The image bakes the same deps by its own hand (environment/Dockerfile) and never runs make, so
# this line is the laptop's copy of that step and changes nothing about a deploy.
# `--include=optional` for the reason the Dockerfile spells out: rolldown ships its native binding
# as a platform optionalDependency, and vite dies at startup without it.
setup:
	npm ci
	cd template/react-vite && npm ci --include=optional
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

# Tests that boot the pinned OpenCode server. Off `make test`: each one starts its own
# server, and a machine-wide lock holds that server until it exits, so they run one after
# another. `make test-opencode` runs that set and nothing else.
test-opencode:
	cd backend && uv run --extra dev pytest -q --opencode -m opencode

# Lint. Ruff is pinned exactly — `required-version` in BOTH backend/pyproject.toml and the root
# ruff.toml — so this and CI cannot disagree about what counts as clean.
#
# The `..` is the point of this target and not a typo. It was `ruff check`, which from `backend/`
# means backend only, while CLAUDE.md called the gate "the WHOLE repo". Nothing had ever linted
# `scripts/`, `spikes/` or `template/`; the first repo-wide run found 46 findings there. Narrowing
# this back re-opens that blind spot silently, because the narrower command still prints
# "All checks passed!".
lint:
	cd backend && uv run --extra dev ruff check ..

# Run the enforcement shim alone (FakeGateway unless GATEWAY_BASE_URL/KEY are set).
shim:
	cd backend && uv run uvicorn sage.shim.app:app --port 8080 --reload

# Run the full orchestrator: control API + /v1 shim (:8080) and preview proxy (:8090).
orchestrator:
	cd backend && uv run python -m sage.orchestrator.app

# Preflight: one real completion through the gateway (confirms provider + key before a build).
probe:
	cd backend && uv run python -m sage.tools.probe

# Which models on this deployment accept which reasoning efforts, measured rather than inferred.
# Prints a table and writes NOTHING. `--write` records it:
#
#     make reasoning-evidence                                every alias, printed
#     make reasoning-evidence ALIASES="--all --write"        every alias, recorded
#     make reasoning-evidence ALIASES='"haiku" "sonnet" --write'   only the ones you name
#
# Naming aliases is how you refresh part of the file: a named alias is rewritten and every other
# row is kept exactly as it is.
#
# Reads GATEWAY_BASE_URL and GATEWAY_API_KEY from backend/.env, so it measures whichever deployment
# those point at. Evidence is per-deployment (ADR-0066) and a row measured on one gateway says
# nothing about another, so pointing this at the wrong .env records the wrong deployment's answers
# under this one's name.
ALIASES ?= --all
reasoning-evidence:
	cd backend && uv run python ../scripts/reasoning-evidence.py $(ALIASES)

# OpenCode coding harness.
opencode:
	npx opencode

# Refresh lockfiles after changing dependencies.
lock:
	npm install
	cd backend && uv lock

clean:
	rm -rf node_modules backend/.venv
