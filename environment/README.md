# Sage Builder — Environment (Phase 3, the shippable artifact)

One Domino Environment image that carries Sage's code, the agent runtime (OpenCode), and a warm
React+Vite template with baked `node_modules`. A user's app runs by launching the **Sage Builder**
pluggable tool in a git-based Domino project; the app repo mounts at `/mnt/code` and is the
workspace.

## Files

| File | Goes into |
|------|-----------|
| `Dockerfile` | The Environment's **Edit Dockerfile** box (on top of your base image) |
| `pluggable-tools.yaml` | The Environment's **Pluggable Workspace Tools** field |
| `app.sh` | Baked via the repo clone; the tool's `start` runs `/opt/sage/environment/app.sh` |

## Key design decision

**Sage code is baked into `/opt/sage`; the app lives on `/mnt/code`.** A Domino Environment build
has no local build context, so nothing is `COPY`ed — the Dockerfile `git clone`s Sage into
`/opt/sage` and `npm ci`s the template there. At runtime the *user's app repo* is the mount
(`/mnt/code`), which is where the orchestrator seeds/edits/commits. This is what lets one image be
both dev artifact and ship artifact without the app and the tooling fighting over `/mnt/code`.

## Fill-ins before it builds

1. **Base image** — `FROM <your-standard-domino-base-image>` at the top of the Dockerfile.
2. **`SAGE_REPO_URL` / `SAGE_REV`** — where to clone Sage from. If the repo is **private**, the
   build-time `git clone` needs a credential (secret build-arg token, or a public deploy mirror).
3. **Gateway** — set `GATEWAY_BASE_URL` (+ creds) so builds can reach a model. Without it the UI
   and preview still work; builds can't call the LLM.

## Fast inner dev loop

Don't rebuild the image per code change: set `SAGE_APP_HOME=/mnt/code` (in a git-based project that
holds the sage *source*), so `app.sh` runs the orchestrator straight off the mount while still using
the baked Node/OpenCode/template. In that mode also set `SAGE_WORKSPACE_DIR` to a scratch dir
(e.g. `/tmp/sage-workspaces/app`) so the source tree isn't treated as an app.

## Relationship to the spike

This supersedes `spikes/domino-verify/` (which installs deps at runtime via `run.sh`). Once this
image is verified, the spike is only kept for reference.

→ **Verify:** launch **Sage Builder** from this Environment in a git-based Domino project → describe
an app → watch it build → private preview renders → a clean build commits+pushes to the app repo.
