# Running the Sage Hub as a published Domino App

By default the hub ships as a **pluggable workspace tool** (`sageHub` in `pluggable-tools.yaml`) — you
run it by launching a *Sage Hub* workspace. This doc covers the other deployment mode: publishing the
hub as a real **Domino App** with a stable, shareable URL (no per-user workspace launch).

## Why a launcher project is needed

A Domino App deploys from a **git-based project** and runs `/mnt/code/app.sh` on the project's
Environment. The hub's actual entrypoint (`hub.sh`) is baked into the **Sage Environment** at
`/opt/sage/environment/`, not on any project mount. So you publish a tiny **launcher project** whose
only file is a root `app.sh` that execs the baked hub — that file is `environment/hub-app/app.sh` here.

## Steps

1. **Create a git-based project** (e.g. `sage-hub`) and set its **Environment = the Sage Environment**
   (the same image the builder/hub tools use — it has `/opt/sage` baked).
2. **Add one file** at the repo root: copy `environment/hub-app/app.sh` from this repo to the launcher
   project's `/mnt/code/app.sh`. Nothing else is required.
3. **Publish the App**: in the project, open **App** → pick the **hardware tier** → **Publish**.
   Domino runs `/mnt/code/app.sh` → `/opt/sage/environment/hub.sh` → the hub on `:8888`, behind the
   App's URL.

## Why you do NOT inject environment variables

The hub launches child builder workspaces into `DOMINO_ENVIRONMENT_ID` / `DOMINO_HARDWARE_TIER_ID`.
Because the App itself runs on the Sage Environment, Domino injects *this run's own* env/tier into
those variables — which are exactly the values the child builders need. Reading the injected values is
therefore correct by construction; hardcoding separate ones would be redundant and could drift.
(`hub.sh` documents this; the App run gets the full `DOMINO_*` run env, not just the short list in the
app-building docs.)

## The one thing to verify live

The hub gets the GitHub token via `git credential fill` (`backend/sage/provision/credentials.py`). This
is confirmed to work in a **workspace** runtime; confirm it also works in the **App** runtime before
relying on it — publish the launcher App, then from the running hub try creating one app. If the global
`git credential` helper isn't wired into App runs, repo creation is where it will surface (the UI/list
still load). Everything else — sidecar token at `:8899`, env/tier injection, `:8888` binding — is the
same as the workspace path the hub already runs on.
