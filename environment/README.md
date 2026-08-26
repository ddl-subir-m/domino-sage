# Sage Workbench — Environment

One Domino Environment image that carries Sage's code, the agent runtime (OpenCode), and a warm
React+Vite template with baked `node_modules`. Chat and Build are one orchestrator process.

Two launch paths, same process:

1. **Published App — the door.** Publish **this repo** as a Domino App. Root `app.sh` starts the
   workbench (`SAGE_PROXY_MODE=app`, port 8888). Domino's App proxy strips the mount prefix; Vite's
   prefix is empty. It does **not** run Chat or Build: `/mnt/code` is Sage source, so the App finds
   or creates the viewer's Default Project and sends them to their own Sage Builder (ADR-0004).
   Listings, provisioning and model calls all use the sidecar at `:8899`, which is the viewer.
   To provision, the App's container needs an **HTTPS Git credential** (Account Settings > Git
   Credentials) — it creates the `sage-*` repo and pushes the seed with it. Set `SAGE_GIT_HOST`
   when that host is not `github.com`.
2. **Sage Builder workspace** — launch the `sageBuilder` pluggable tool in a **git-based app
   project**. [`environment/app.sh`](app.sh) starts the same orchestrator with
   `SAGE_WORKSPACE_DIR=/mnt/code`. That is where **Publish** ships the Built App.

There is no Hub, no second server, and no `sageHub` tool.

## Files

| File | Goes into |
|------|-----------|
| `Dockerfile` | The Environment's **Edit Dockerfile** box (on top of your base image) |
| `pluggable-tools.yaml` | The Environment's **Pluggable Workspace Tools** field (`sageBuilder` only) |
| `app.sh` | Baked via the repo clone; the tool's `start` runs `/opt/sage/environment/app.sh` |
| repo-root `app.sh` | What a published App runs (`/mnt/code/app.sh`) — execs the same orchestrator with App settings |

## Key design decision

**Sage code is baked into `/opt/sage`; the user's app lives on `/mnt/code` in a Builder workspace.**
A Domino Environment build has no local build context, so nothing is `COPY`ed — the Dockerfile
`git clone`s Sage into `/opt/sage` and `npm ci`s the template there. At runtime a Sage Builder
session mounts the *user's app repo* at `/mnt/code`, which is where the orchestrator seeds/edits/
commits. A published Workbench App must **not** treat that checkout as an app — root `app.sh` points
`SAGE_WORKSPACE_DIR` at a scratch dir when `/mnt/code` is this Sage tree.

## Fill-ins before it builds

1. **Base image** — `FROM <your-standard-domino-base-image>` at the top of the Dockerfile.
2. **`SAGE_REPO_URL` / `SAGE_REV`** — where to clone Sage from. If the repo is **private**, the
   build-time `git clone` needs a credential (secret build-arg token, or a public deploy mirror).
3. **Gateway** — set `GATEWAY_BASE_URL` in the Environment's **Environment Variables** box so builds
   can reach a model. Without it the UI and preview still work; builds can't call the LLM.
   - Set it at the **Environment** level, not the project level. Workspaces inherit the
     Environment's baked env.
   - Domino requires each Environment Variable to also be declared as an `ARG` in the Dockerfile.
     Ours (`GATEWAY_BASE_URL`, `SAGE_GATEWAY_MODE`) are declared *and* promoted to `ENV` so they
     survive into the running container — a bare `ARG` is build-time only.
   - **Don't** put `GATEWAY_API_KEY` here: promoted to `ENV` it lands in an image layer. In `domino`
     mode leave the key unset; listings and model calls use the sidecar token at `:8899`.

## Fast inner dev loop

Don't rebuild the image per code change: set `SAGE_APP_HOME=/mnt/code` (in a git-based project that
holds the sage *source*), so `app.sh` runs the orchestrator straight off the mount while still using
the baked Node/OpenCode/template. `SAGE_TEMPLATE` stays pinned to the baked `/opt/sage` copy (it does
*not* follow `SAGE_APP_HOME`), so the warm `node_modules` are always present and the preview never
boots cold. In that mode also set `SAGE_WORKSPACE_DIR` to a scratch dir (e.g. `/tmp/sage-workspaces/app`)
so the source tree isn't treated as an app.

Note what that mode does NOT cover: a Sage Builder the door creates has no such mount, so it always
runs the baked `/opt/sage`. Two containers, two Sages — the App can be running your branch off
`/mnt/code` while every builder it opens is on whatever the image holds. When a builder behaves like
older code, check `/api/diag` (`sage_rev`) there before suspecting the door.

Builders take the Environment's **active** revision — Sage sends no `environmentRevisionSpec` — so a
rebuilt Environment reaches the next builder without restarting the Workbench App. A published Built
App is the opposite: it pins the revision it was published on, so a deployed app keeps running the
image it was tested against.

## Relationship to the spike

This supersedes `spikes/domino-verify/` (which installs deps at runtime via `run.sh`). Once this
image is verified, the spike is only kept for reference.

→ **Verify (App):** publish this repo as an App on the Sage Environment →
open Chat → listings come from the sidecar. `#/chat` / `#/build` and `./preview/` work.

→ **Verify (workspace):** launch **Sage** (`sageBuilder`) in a git-based app project → describe an
app → watch it build → private preview renders → Publish deploys that project as a Built App.
