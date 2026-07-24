# Additive layers for the spike Environment. Put these in your Domino Environment's
# "Edit Dockerfile" box, on TOP of a standard Domino base image (pick one you already use).
# Goal: Node >=20 + uv + this spike dir on the image, with run.sh executable.
#
#   FROM <your-standard-domino-base-image>

USER root

# Node 20 (Vite needs >=18; we pin 20) + uv (fast Python runner for the FastAPI proxy).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

USER ubuntu

# NOTE: do NOT try to COPY the spike into the image. A Domino Environment build has no local
# build context, so `COPY . ...` has nothing to copy. Instead the spike files come from the
# PROJECT FILE VOLUME: clone this repo into the Domino project (README does `git clone`), and
# point the pluggable tool's `start` at run.sh on that mount, e.g.
#   /mnt/<projectName>/spikes/domino-pathspike/run.sh   (git-based project)
# STEP 2 (probe.py -> /whoami -> domino_env) confirms the exact mount path. run.sh does the
# npm install at runtime into the mount (persists across sessions, so only the first boot pays).
