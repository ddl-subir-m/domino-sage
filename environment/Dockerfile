# Sage Builder — Domino Environment (Phase 3: the shippable artifact).
#
# Paste these layers into your Domino Environment's "Edit Dockerfile" box, ON TOP of a standard
# Domino base image you already use:
#
#   FROM <your-standard-domino-base-image>   # <-- FILL IN
#
# A Domino Environment build has NO local build context, so nothing is COPYed — everything is
# fetched at build time (apt / npm / git). At RUNTIME the user's APP repo mounts at /mnt/code and
# is the sage workspace; Sage's own code + the warm React+Vite template are baked here into
# /opt/sage. (Dogfooding note: when /mnt/code is the sage SOURCE repo, set SAGE_WORKSPACE_DIR to a
# scratch dir instead — see environment/app.sh.)

USER root

# --- Node 22 + uv -----------------------------------------------------------------------------
# vite@8 / rolldown@1.1.5 require Node ^20.19 || >=22.12 — v22.11 FAILS that range, which makes npm
# skip rolldown's optional native binding (@rolldown/binding-linux-x64-gnu) during `npm ci`, and
# vite then dies at startup with "Cannot find module '@rolldown/binding-linux-x64-gnu'". Pin >=22.12.
# We install the official Node.js prebuilt tarball into /usr/local rather than NodeSource's
# `deb.nodesource.com/setup_22.x | bash -`: that CDN started returning 403 to the buildkit workers,
# failing every rebuild. Pinned for reproducibility; the .gz tarball avoids depending on xz-utils.
# /usr/local/bin goes first on PATH so this node beats both a stale /usr/bin/node and conda's
# shadowing node (the recurring gotcha from Phase 1).
# NOTE: linux-x64 — assumes an amd64 base image (Domino's standard images are amd64).
ARG NODE_VERSION=v22.12.0
RUN curl -fsSLO "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.gz" \
    && tar -xzf "node-${NODE_VERSION}-linux-x64.tar.gz" -C /usr/local --strip-components=1 --no-same-owner \
    && rm "node-${NODE_VERSION}-linux-x64.tar.gz" \
    && apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && node --version && npm --version
ENV PATH=/usr/local/bin:/usr/bin:$PATH

# --- OpenCode ---------------------------------------------------------------------------------
# The agent runtime the driver spawns via `npx opencode serve`. Pinned; installed globally so it
# resolves regardless of cwd. Ships its Linux binary as a platform optionalDependency with a
# local-only postinstall (no egress beyond the npm registry — same mechanism as rolldown).
RUN npm install -g --include=optional opencode-ai@1.18.4 && opencode --version

# --- Gateway config, baked into the image so EVERY workspace inherits it -----------------------
# Domino requires each Environment Variable to be declared as an ARG here; a bare ARG is build-time
# only, so we promote it to ENV so it survives into the running container. This is what makes the
# gateway follow the ship image: the hub AND every child builder it launches boot in `domino` mode.
# (Child workspaces created via the v4 API inherit the Environment's baked ENV, NOT the hub project's
# own env vars — which is why a project-scoped GATEWAY_BASE_URL leaves child builders in `fake` mode.)
# Set the value in the Environment's "Environment Variables" box; leave it blank to run offline/fake.
# NON-SECRET ONLY: GATEWAY_BASE_URL is a URL. Do NOT bake GATEWAY_API_KEY this way — a key promoted to
# ENV is written into an image layer. In `domino` mode leave the key unset; the per-workspace sidecar
# token at :8899 is re-acquired per call and works in every workspace, child builders included.
ARG GATEWAY_BASE_URL=
ENV GATEWAY_BASE_URL=${GATEWAY_BASE_URL}
# Optional: only needed to force a non-auto mode (e.g. openai). Non-secret; safe to bake.
ARG SAGE_GATEWAY_MODE=
ENV SAGE_GATEWAY_MODE=${SAGE_GATEWAY_MODE}
# Optional: 1 logs raw gateway SSE chunks to /api/diag (see README). Sets the INITIAL state only —
# this value is baked at build time, so changing it here costs a rebuild. To toggle a running builder,
# POST /api/diag/debug-stream {"on": true} instead. Leave blank normally.
ARG SAGE_DEBUG_STREAM=
ENV SAGE_DEBUG_STREAM=${SAGE_DEBUG_STREAM}
# Optional: remap the six catalog slots onto whatever aliases YOUR gateway actually serves. The
# defaults in _build_catalog() (bedrock-qwen3-coder / sonnet / qwen-2-5) are the dogfood gateway's
# names; on any other gateway the missing ones 404 — and not at boot, but on the first turn that
# doesn't carry an explicit model pick, because llm_router falls through to catalog.implement only
# when picked_model is None. A UI-level pick masks it on most turns, so leaving these blank looks
# like it works right up until a build dies mid-phase. Leave blank to keep the defaults.
ARG SAGE_MODEL_PLAN=
ENV SAGE_MODEL_PLAN=${SAGE_MODEL_PLAN}
ARG SAGE_MODEL_IMPLEMENT=
ENV SAGE_MODEL_IMPLEMENT=${SAGE_MODEL_IMPLEMENT}
ARG SAGE_MODEL_ASK=
ENV SAGE_MODEL_ASK=${SAGE_MODEL_ASK}
ARG SAGE_MODEL_SOVEREIGN_PLAN=
ENV SAGE_MODEL_SOVEREIGN_PLAN=${SAGE_MODEL_SOVEREIGN_PLAN}
ARG SAGE_MODEL_SOVEREIGN_IMPLEMENT=
ENV SAGE_MODEL_SOVEREIGN_IMPLEMENT=${SAGE_MODEL_SOVEREIGN_IMPLEMENT}
ARG SAGE_MODEL_SOVEREIGN_ASK=
ENV SAGE_MODEL_SOVEREIGN_ASK=${SAGE_MODEL_SOVEREIGN_ASK}

# --- Sage code + warm template, baked ---------------------------------------------------------
# ARGs above each git op so bumping the rev busts the layer cache and pulls the latest on rebuild.
ARG SAGE_REPO_URL=https://github.com/ddl-subir-m/domino-sage.git   # FILL IN if different
ARG SAGE_REV=main
# `--branch` takes a ref (branch/tag), not a bare SHA — so to force a fresh clone of the SAME branch
# (e.g. new commits on main), bump SAGE_CACHE_BUST instead; it busts this layer without touching the ref.
ARG SAGE_CACHE_BUST=2026-08-07-ui-reattach-running-turn
# NOTE: if the repo is PRIVATE, this clone needs a build-time credential — pass a token via a
# secret build-arg and use it in the URL, or make a deploy mirror public. Public clone shown here.
RUN git clone --depth 1 --branch "$SAGE_REV" "$SAGE_REPO_URL" /opt/sage

# Warm template deps baked (agent-added deps install on top, into the app fs, at runtime).
# Even with a compatible Node, `npm ci` can still skip rolldown's platform-native optional binary
# (@rolldown/binding-linux-x64-gnu) — then vite dies at startup with "Cannot find module
# '@rolldown/binding-linux-x64-gnu'". So after npm ci, check the .node file exists (filesystem —
# NOT `require('rolldown')`, which is ESM-only and always throws), self-heal by force-installing the
# exact version if it's missing, then assert it's present so a broken image fails the build here.
RUN cd /opt/sage/template/react-vite \
    && npm ci --include=optional --no-fund --no-audit \
    && if ! find node_modules/@rolldown/binding-linux-x64-gnu -name '*.node' 2>/dev/null | grep -q .; then \
         echo "[sage] rolldown linux binding missing after npm ci — installing explicitly"; \
         npm install --no-save --include=optional \
           "@rolldown/binding-linux-x64-gnu@$(node -p "require('./node_modules/rolldown/package.json').version")"; \
       fi \
    && find node_modules/@rolldown/binding-linux-x64-gnu -name '*.node' 2>/dev/null | grep -q . \
    && echo "[sage] rolldown native binding OK"
# Python deps for the orchestrator baked into a uv venv.
RUN cd /opt/sage/backend && (uv sync --frozen || uv sync)

# The workspace user owns the baked tree so app.sh (run as ubuntu) can read/exec + write its venv.
RUN chown -R ubuntu:ubuntu /opt/sage
USER ubuntu
