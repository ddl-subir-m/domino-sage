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
# vite@8/rolldown hard-fails below Node 20.19; conda's node commonly shadows nodesource, so we
# force the system node onto PATH for every shell (the recurring gotcha from Phase 1).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs git \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv
ENV PATH=/usr/bin:/usr/local/bin:$PATH

# --- OpenCode ---------------------------------------------------------------------------------
# The agent runtime the driver spawns via `npx opencode serve`. Pinned; installed globally so it
# resolves regardless of cwd. Ships its Linux binary as a platform optionalDependency with a
# local-only postinstall (no egress beyond the npm registry — same mechanism as rolldown).
RUN npm install -g --include=optional opencode-ai@1.18.4 && opencode --version

# --- Sage code + warm template, baked ---------------------------------------------------------
# ARGs above each git op so bumping the rev busts the layer cache and pulls the latest on rebuild.
ARG SAGE_REPO_URL=https://github.com/ddl-subir-m/domino-sage.git   # FILL IN if different
ARG SAGE_REV=main
# `--branch` takes a ref (branch/tag), not a bare SHA — so to force a fresh clone of the SAME branch
# (e.g. new commits on main), bump SAGE_CACHE_BUST instead; it busts this layer without touching the ref.
ARG SAGE_CACHE_BUST=2026-07-24
# NOTE: if the repo is PRIVATE, this clone needs a build-time credential — pass a token via a
# secret build-arg and use it in the URL, or make a deploy mirror public. Public clone shown here.
RUN git clone --depth 1 --branch "$SAGE_REV" "$SAGE_REPO_URL" /opt/sage

# Warm template deps baked (agent-added deps install on top, into the app fs, at runtime).
RUN cd /opt/sage/template/react-vite && npm ci --include=optional --no-fund --no-audit
# Python deps for the orchestrator baked into a uv venv.
RUN cd /opt/sage/backend && (uv sync --frozen || uv sync)

# The workspace user owns the baked tree so app.sh (run as ubuntu) can read/exec + write its venv.
RUN chown -R ubuntu:ubuntu /opt/sage
USER ubuntu
