---
status: accepted
---

# The catalog is the door for Auto and Ask

A person can now choose what every Build mode runs on. The control writes a **Model assignment** —
a slot in the `ModelCatalog`, persisted per Project — and never a **Model override**. The router
does not change: `llm_router` still returns `ASK_PINNED` without ever reading `picked_model`, and
Auto still follows the phase. What changed is that the values those two branches resolve against
are now something a person can set.

This is worth writing down because the obvious reading of the code says otherwise. A future reader
finds a model menu in Ask mode, opens `llm_router.py`, sees that Ask ignores the override, and
concludes the menu is broken. It is not. The menu writes the other thing.

## Why not extend the override

Extending `picked_model` into Auto and Ask was the first design, and it was rejected for a
mechanical reason rather than a taste one. `picked_model` is a **single field** on `SessionState`.
Auto runs two models — `catalog.plan` while it plans, `catalog.implement` while it builds — so one
field cannot express Auto's choice at all. Extending the override there would mean either a second
override field per phase, which is the catalog rebuilt in memory and forgotten on restart, or
collapsing Auto onto one model, which deletes the only thing Auto does that Implement does not
(`PRD.md` R6.1: "otherwise 'plan strong / implement cheap' is a story, not a behavior").

The catalog needed no such invention. It already had a slot per phase, it already persisted to
`.sage/model_overrides.json`, and `_effective_catalog` already layered a Project's overrides over
the deployment defaults. The gap was never the mechanism. It was that nothing on screen could reach
it.

## Both controls survive, because they are not the same control

|  | Model assignment | Model override |
|---|---|---|
| Scope | the Project, and everyone in it | this viewer's own Sage Builder |
| Lifetime | committed; survives restart | in memory; dies with the Builder |
| Modes that honour it | all four | Plan and Implement |
| Where | the drawer | the chip |

Deleting the override was considered and refused: it is the only way to try a model without
changing what a teammate sees. But two controls that both say "model", in the same chip, is the
confusion this change exists to remove — so they are separated by **place** (chip vs drawer) and by
**word** (`Model override` vs `Model assignment`, both now in `CONTEXT.md`).

Saving any assignment clears the standing override. Not "the override for the slot you changed":
`picked_model` is one mode-independent field, so a narrower clear is not expressible without
reshaping it. The alternative — an assignment that saves and visibly does nothing, because an
override still shadows it — is the worst outcome available here.

## Ask and Chat are one assignment, and the label says so

`_resolve_chat` returns `catalog.ask` as `CHAT_DEFAULT`. The Ask slot has therefore always driven
Chat's default model as well, and a person setting Ask to something cheap silently repoints Chat.
The row is labelled **Ask and Chat**.

Splitting the slot into `catalog.ask` and `catalog.chat` is the better long-term shape and is
deliberately not done here. It is a router change plus a migration of every existing
`model_overrides.json`, for a coupling that an honest label costs nothing to disclose.

## Three slots shown, six kept

`ModelCatalog` holds `sovereign_plan`, `sovereign_implement` and `sovereign_ask` as well. They are
persisted and preflighted, and the router reads none of them — they belong to a sensitivity lock
that no longer routes. They are not shown, because a row that changes nothing is worse than no row.
They are not deleted, because `resources/preflight.py` resolves them and the lock may return.

## What the backend needed

Two changes, and the second only became necessary because of the first.

`set_catalog` dropped falsy fields and only ever called `overrides.update()`. There was no delete
path, so an assignment, once made, could never be taken back — and the "Use the default" row had
nothing to call. It learns to tell *absent* from *empty* and to remove the key, which is what returns
a slot to the deployment default that `_effective_catalog` layers over.

It also learns to refuse a slot name the catalog does not have. That is not defensive padding: the
overrides file is written *before* the catalog is rebuilt, so an unknown key would be persisted and
would then raise a `TypeError` out of `replace()` on every read of that project's catalog
afterwards. A transient 400 in place of a durable brick. It is checked against all six slots rather
than the three assignable ones, because the sovereign slots have always been settable through this
route and narrowing that would take away a capability nobody asked to lose.

## Considered options

**Extend `picked_model` into Auto and Ask.** Rejected. One field cannot hold a per-phase choice; see
above.

**One "Auto model" covering both phases.** Rejected. It makes Auto identical to Implement and gives
up the per-phase cost split that Auto exists for.

**Put assignments in Account settings.** Rejected. That drawer's own copy says "They follow you into
every Project". An assignment does the opposite — it belongs to one Project and is shared with
everyone in it. Reusing that surface would have required the copy to lie about scope.

**Put them in Manage.** Rejected. Manage is a stub owned by a parallel branch.

**Push on save, so teammates get the assignment immediately.** Rejected. `POST /api/project/sync`
can run a model turn to resolve conflicts. That is far too heavy for a dropdown, so the drawer
states the delay instead: teammates get it on the next sync.

**Block the save on a reachability check.** Rejected. It makes a live gateway call a hard dependency
of writing a setting, and it would be the second control in this feature that refuses to work for a
reason outside the user's control. Unreachable aliases are greyed at draw time instead, and the
save-time check is a backstop that reports afterwards.

## Consequences

- The model chip becomes clickable in all four modes. The disabled-button-with-a-tooltip state goes.
  In Auto the label names the phase — `gpt-5.4 · planning` — because a bare id that changes under the
  user with no explanation is the thing that prompted this ADR.
- The picker is drawn from the full chat-capable alias listing, the one Chat already uses. This
  fixes a separate defect: `open_weight_models` is `[]` unless `GATEWAY_MODE == "openai"`, so on real
  Domino the chip offered only the models already assigned — a list that could not express a change.
- `PREFLIGHT_SLOTS` is a module global computed at startup and session open, over the **deployment**
  catalog. The save-time re-check therefore needs its own project-scoped call; it cannot read that
  global.
- Assignments are refused while a turn runs, matching the guard the override chip already has.
  Nothing pins the catalog for the duration of a turn, so accepting a change would move a running
  build onto another model with the first half's tool calls in context.
- If the alias listing cannot be fetched the drawer opens read-only, showing the current assignments
  with the reason and a retry. It does not fall back to the assigned-models-only list, which is the
  defect above wearing a different hat.
- `CONTEXT.md` gains **Build**, **Model assignment** and **Model override**, and records "model" as a
  permitted plain-language exception to the **LLM Alias** rule.
- Splitting `catalog.ask` into a separate Chat slot is left open.
