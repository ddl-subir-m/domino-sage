# Collaborators — implementation handoff

Wire the Workbench's "add collaborators" control to Domino. Today it is a stub. The design is
settled; every API fact below was live-verified on cloud-dogfood on 2026-09-03.

**Read first:** `COLLABORATORS-RESEARCH.md` (the verified API), `docs/adr/0028-...md` (a contract
this work reverses), and the `Collaborator` entry in `CONTEXT.md`.

## What is broken today

- `SW.api.invite` is `async () => ({})` — a stub that calls nothing (`backend/sage/workbench/js/api.js:685`).
- The picker is always empty: `list_members` sets `directory = members` (`backend/sage/orchestrator/service.py:2708-2716`), and the modal then filters out everyone already a member (`collab.js:73`).
- `collab.js:79` filters picked ids to `id.startsWith('u_')`. Real Domino ids are 24-hex, so it posts an empty list.
- The avatar stack filters on a `presence` field the server never sends, so it renders nothing (`collab.js:26`).
- `collab.js:10` invents roles (`editor`/`reviewer`/`viewer`) that do not exist in Domino.

## The verified API

Auth is `Authorization: Bearer <token>`. **`X-Domino-Api-Key` returns 403** — do not use it.

| Purpose | Call |
|---|---|
| Everyone on the deployment | `GET /v4/users` (397 rows; `?query=<prefix>` typeaheads) |
| Who is on the Project (**names, includes the owner**) | `GET /v4/projects/{pid}/collaborators` |
| Roles + ownerId (**excludes the owner**) | `GET /v4/projects` → the project's `.collaborators[]`, `.ownerId` |
| Add | `POST /api/projects/v1/projects/{pid}/collaborators` `{"id","role"}` → 200 |
| Remove | `DELETE /api/projects/v1/projects/{pid}/collaborators/{userId}` → 200 |

Three traps, all confirmed by probe:

1. **Casing.** The write takes `contributor`; the read returns `Contributor`. Fold case when comparing.
2. **The owner asymmetry.** The name-bearing read includes the owner; the role-bearing read does not. Render the owner as a row with no role — do not drop it.
3. **Adding twice is a 400**, identifiable only by an English sentence (`"... is already part of project ..."`). **Do not match that string.** On any 400, re-read the collaborator list once; if the person is there, it succeeded.

`/api/projects/v1/...` has **no GET** — `GET` and `OPTIONS` on it 404 while `POST` works. Never
infer a path's existence from `OPTIONS` on this platform.

## The design

**Adding**
- The verb is **Add**, never "invite". There is no acceptance step. `CONTEXT.md` rules the word out.
- Multi-select. Everyone is added as `contributor`. No role picker — the roles differ in ways a creator cannot judge, and `projectImporter` silently cannot open the published App.
- The request body carries **no projectId**. The server uses its own `_domino_project_id` (`service.py:2513`). A client-supplied project id is an authorization surface.
- Partial failure reports precisely ("Added 2 people. Could not add Priya — <reason>") and rolls nothing back. The failed person stays selected for a one-click retry.
- **No permission pre-check.** Let Domino refuse and show the refusal — ADR-0018 rejected exactly this shape of Sage-side authorization list.

**Removing** (in scope)
- Lives in the same modal, because ADR-0011 puts removal with the list that owns the scope.
- No Remove on the owner's row or the caller's own row, each explaining why ("Project owner", "You"). Do not rely on a 403 after the click.
- The confirm names both effects: "Remove Priya from Sage? They will lose access to this Project and to any App published from it." Under `GRANT_BASED` these are one act.

**The list**
- Shows the raw platform role value. It is a runtime value, so the paranoid brand pack never scans it (`brand_coverage.toml`, `[rules.runtime-values]`).
- On a `ProjectImporter` row only, a Sage-authored caption: "Cannot open published Apps."
- The avatar stack drops the presence filter and shows collaborators instead. Do not fake presence — nothing on the server implements it.

**Three states, never conflated:** not connected to the platform → connected but nobody else → the read failed (with a Retry). Off Domino, `FakeResourceProvider.collaborators` is empty by design, and that must read as "not connected", not as "nobody to add".

**Copy** goes through `SW.brand.text` wherever it says Project or App — both are keyed nouns.

## Server changes

- Grow `GET /api/members` (`app.py:2219`): add `role` per member, plus `ownerId` and `self`. Its docstring names only one of its two callers — rewrite it. Do not add a second endpoint returning an overlapping copy of the same truth.
- Add the write routes. Fetch the whole directory for the picker (397 rows ≈ 60KB); filter in the browser.
- **Per ADR-0028: `list_collaborators` now raises when it cannot read**, instead of returning empty. Move the forgiveness into the plan-review caller, which catches and degrades to empty. Read the ADR before touching this — the old behaviour was deliberate and documented in the Protocol at `provider.py:582-584`.
- Extend `FakeResourceProvider` (`provider.py:1530`) with add/remove so the whole flow runs off Domino.

## How to work

TDD, per `docs/agents/`. Tests here are full sentences — `test_a_problem_says_what_broke_and_who_owns_it.py`, `test_an_identifier_is_not_a_word`. Match that.

Commit straight to `main`, no feature branch. Commit messages follow the log's style:
`feat(workbench): a colleague added to the Project can open the App`.

**Stage explicit paths — never `git add -A`.** Several Claude sessions run on this repo at once.

**Check `git status` before you start.** Another session was editing
`backend/sage/workbench/js/api.js` and `store.js` — the exact file holding the `invite` stub. Let
that land before you touch it, or you will resolve conflicts mid-build.

**Do not use worktrees.** `~/.config/opencode` is global and bakes in a per-worktree port; a second
boot silently wins.

## Live verification at the end

Credentials are in `backend/.env` as `GATEWAY_API_KEY` (a Keycloak JWT despite the name; see the
`sage-real-domino-local-run` note). Probe against `quick-start`
(`66a821b2ecadae7f043a5171`) and add the **service account** `repro-practitioner-sa`
(`6a8f594e0fdcc91dd93b8e2a`) rather than a colleague. Remove it afterwards and verify it is gone.

---

## Built (2026-09-03) — where the implementation diverged from this page

Shipped in `feat(workbench): a colleague added to the Project can open the App`. The design above
was followed as written; two lines of the API table were wrong and are corrected here. Full detail,
with the live responses, is in the implementation addendum at the end of `COLLABORATORS-RESEARCH.md`.

- **Roles + ownerId come from `GET /v4/projects/{pid}`, not from searching `GET /v4/projects`.**
  The listing has no paging guard, so a builder in enough projects gets a `200` without their own
  project in it — and a healthy project then reports as a failed read. The single-project route
  answers about the project asked for, or refuses.
- **The role entry is keyed `collaboratorId`, not `id`.** The name-bearing read calls the same
  person `id`. Joining on the wrong one matches nobody and blanks every role with no error to show
  for it, which is exactly what happened on the first pass. Only the live probe caught it.
- **`/api/projects/v1/...` does have a GET**; the 404 is scoped to the `/collaborators` sub-path.
  `GET /api/projects/v1/projects/{pid}` returns `ownerId` and collaborators — in the third of the
  three field spellings this platform uses for one fact. Sage does not use it, but read the
  addendum before reaching for any project record, because which route you pick changes the field
  names AND the casing of the role you display.

One thing this page asked for and did not get a separate endpoint: `/api/members` grew instead, as
instructed. Its three states (`connected`, `error`, neither) are what keep "not on the platform",
"nobody else here" and "the read failed" apart, and its two reads are caught separately so a
directory outage cannot blank the plan page's reviewer names.
