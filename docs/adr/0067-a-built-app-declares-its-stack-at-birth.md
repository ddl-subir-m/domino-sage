---
status: accepted
extends: ADR-0002 (Python serves the Built App — the no-build stack removes the last reason Node was
  kept), ADR-0008 (a Project holds many Built Apps — which is why the choice is per app and per
  viewer, never per Project), ADR-0010 (publish reads the declaration — the declaration files are
  stack-neutral and both stacks read them)
---

# A Built App declares its stack at birth; safe recovery restores a missing record

Sage seeded one kind of app for a long time: React + TypeScript + Vite, built by Node and served by
Python (ADR-0002). A second kind is now seeded by default — FastAPI serving a page that loads React,
Ant Design, Day.js and Highcharts as plain scripts, the stack the Workbench itself is built on, with
no build step and no `node_modules` (#490). The first kind stays: every Project volume out there
holds one, and a workspace never re-seeds (#40).

## What a stack is

`backend/sage/workspace/stack.py` holds one frozen `Stack` per kind and the registry `STACKS`.
Every fact the backend used to know as a constant about the app's shape is a field: the seed
sentinel (`package.json` / `app.py`), the deploy files publish refreshes and their order, the
Sage-owned sources attach refreshes, where the helpers live and what extension they carry, whether
the preview reads a config file, which server the entry script execs, which file is the entry, how
the preview is served (Vite / uvicorn), what the end-of-turn check is (tsc / compile-and-parse),
and which globs are the app's own source. Callers take the app's `Stack` and ask it. Nobody works
the shape out again.

## The record, and why it is not detection

An app records its kind as `stack` in its own `.sage/settings.json`, written at birth beside
`createdAt`. A valid known record is authoritative, even when files from another stack exist.

Updated by #503 / #557: a missing record no longer means React. One pure resolver serves the
manager, preview, instructions, scope and feedback. Only one complete layout (the stack's sentinel
and entry file), with no evidence of another stack, permits metadata recovery. The manager writes
only the missing stack field. Mixed or incomplete unrecorded apps, malformed settings and unknown
stack names are preserved and need recovery. Readers never write metadata. A directory with no
stack file at all — whatever else Chat has recorded in it, a bindings manifest or a display
name — is an app not yet born, and seeds.

Deleting a sentinel from an UNRECORDED app no longer reseeds it: the app reads as ambiguous and
is left alone. A recorded app that lost a template file gets the missing files back on the next
attach, without replacing anything else — the record names the template, so nothing is guessed.
Explicit Reset still restores the recorded stack, and resolves it before removing any files. New apps get
one atomic birth record with `seedState: pending` before the copy. Only this marker permits an
interrupted copy to resume, recursively copying missing files without replacing edits. Completion
records `seedState: complete`. Old apps without this marker are never assumed to be interrupted
seeds. This deliberately replaces the old sentinel-deletion recovery rule.

One registry. `Workspace` (a value object built in many places) and the manager both answer off
the module's `STACKS`; the first run of the seam's tests found a manager-local registry seeding
one template while the value object named another's entry file.

## Where the choice lives

A stack is fixed when an app is seeded and cannot change after, so the choice is made at the one
moment it applies: `POST /api/apps {"stack": ...}` from the Build rail's New app. What the rail
sends is the viewer's saved answer — `appStack` in `prefs.js`, per viewer, in the browser
(ADR-0044's layer) — set from Account settings. Not per Project: a Project holds many apps
(ADR-0008), and a Project-wide switch would read as converting the current one, which is
impossible. The deployment's default for a caller that sends nothing is `SAGE_DEFAULT_STACK`,
falling back to `fastapi-antd`; a name Sage cannot seed is a 400 before anything is minted.

## What the two stacks share

The query half of the published app's server, `sage_queries.py`, is one file in both templates,
byte-identical, and the platform relay `sage_domino.py` (#489) the same. ADR-0010's declaration
files — `.sage/bindings.json`, `.sage/queries.json`, `.sage/attachments.json` — are read by both.
The preview proxy intercepts `/api/queries/*`, `/api/llm/*` and `/api/domino/*` ahead of either
upstream, so a page reads its data and its models the same way while it is being built whichever
kind it is.

## Consequences

- ADR-0002's "Node builds, Python serves" holds for react-vite only. A fastapi-antd app's cold
  start is the data rehydrate and uvicorn binding its port; the first publish after this change
  adds a row to ADR-0002's table.
- Every browser asset the no-build page loads is vendored under `static/vendor/`, a copy of the
  Workbench's own bundles, so one NOTICE describes both and no page Sage seeds asks a CDN for
  anything (ADR-0014; #19 is the same rule for fonts). An app repo carries ~1.6 MB of scripts
  instead of a 202 MB `node_modules` symlink.
- The test suite pins `SAGE_DEFAULT_STACK=react-vite` in `conftest.py`, because every fake
  template a test builds is one; a single test removes the pin and proves the real default on the
  real templates.
- A published fastapi-antd app reads the platform API as its PUBLISHER (#491). Both stacks' agent
  instructions say so.
