# Running the Sage Hub as a published Domino App

By default the hub ships as a **pluggable workspace tool** (`sageHub` in `pluggable-tools.yaml`) — you
run it by launching a *Sage Hub* workspace. This doc covers the other deployment mode: publishing the
hub as a real **Domino App** with a stable, shareable URL (no per-user workspace launch).

## How it works

A Domino App deploys from a **git-based project**: it checks the repo out to `/mnt/code` and runs
`/mnt/code/app.sh` on the project's Environment. The hub's actual entrypoint (`hub.sh`) is baked into
the **Sage Environment** at `/opt/sage/environment/`, so the root `app.sh` just execs it.

## Recommended: publish THIS repo as the App

This repo already has a root `app.sh` that execs the baked hub, so you can publish the Sage source
project directly — no separate launcher project to maintain.

1. Have a **git-based Domino project** pointing at this repo (the Sage source), with its **Environment
   = the Sage Environment** (the image with `/opt/sage` baked).
2. Open the project's **App** section → pick the **hardware tier** → **Publish**.
   Domino runs `/mnt/code/app.sh` → `/opt/sage/environment/hub.sh` → the hub on `:8888`, at the App's
   shareable URL.

That's it. The hub runs the baked `/opt/sage` code (not the `/mnt/code` checkout), so which commit is
checked out doesn't matter — it's just there to give the App an entrypoint. (`app.sh` falls back to
this checkout's own `hub.sh` only when the baked copy is absent, e.g. running it off the Sage image.)

## Alternative: a standalone launcher project

If you'd rather not publish the whole source repo, publish a tiny project whose only file is a root
`app.sh` that execs the baked hub — use `environment/hub-app/app.sh` as that file. Same publish steps
as above.

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
