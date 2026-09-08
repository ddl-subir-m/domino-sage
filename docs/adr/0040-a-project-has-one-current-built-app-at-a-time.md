---
status: accepted
extends: ADR-0008 (a Project holds many Built Apps — this says how many of them are reachable at
  once), ADR-0021 (each Scope's door lives on the surface that owns it — the same reasoning about
  which surface may write what)
---

# A Project has one current Built App at a time

ADR-0008 gave a Project many Built Apps. It did not say how many of them a person can be working in
at once, and the answer turns out to be one — not by design, but by the shape of the code. This
records that as a decision rather than leaving it as an accident, and says what would have to change
first if it is ever reversed.

## What is true

`Orchestrator.project()` returns the single bound Project, and the active app inside it is process
state rather than a parameter on the request. The client moves it with `POST
/api/apps/{app_id}/select`; every other call — `/api/bindings`, the candidate confirm, the build
stream — acts on whichever app was selected last.

Measured on 2026-09-08: with Sage open in two browser tabs, changing the app in tab 1 changes it in
tab 2. The tabs cannot hold different apps. So you cannot work on two Built Apps side by side, and a
switch made in one tab moves the other out from under whatever was on screen there.

## What a turn already pins

The selection is global, but a turn is not. `Project.app_for_turn()` answers the app a turn PINNED
AT ITS START, and falls back to the one on screen only when no turn is running. Everything a turn
does — writing code, reverting it, appending to the build log, repairing attachments afterwards —
asks that rather than asking what the rail is showing. The select route says the same thing from the
other side: looking is free, a running build is never refused, and it carries on in the app it
started in while the rail marks that row (#77).

This is the load-bearing part. **Global selection decides where a turn STARTS, and nothing more.**
The obvious fear — that switching apps mid-build lands the build in the wrong one — is already
answered, and answered in the layer that should answer it.

## What was tested and is not a fault

Two hypotheses were tested live and both were wrong. They are recorded because they are the ones
anybody meeting this will reach for:

- **A card's click does not land on the wrong app.** Switching apps removes the card from the
  transcript, so there is no card belonging to a departed app left to click.
- **Two tabs do not diverge.** They cannot, for the reason above — so the deterministic case that
  looked like the strongest reproduction is unreachable.

Two things remain true and are worth knowing: the write path carries no app id, and the client's
`appScopeTicket`/`appGen` guard covers reads rather than writes. Neither is a reachable fault while
selection is global. That is not a coincidence — it is the same fact stated twice.

## Why not per-tab

Per-tab selection is defensible, and it is what "a Project holds many Built Apps" might be expected
to deliver. It is not being built:

1. **Nobody has wanted it.** This was reached by pressing `+ New App` repeatedly by mistake, not by
   wanting two apps side by side.
2. **It is two changes, not one.** The write path carries no app id today, and global selection is
   what makes that safe. Go per-tab and "a click lands on the other tab's app" becomes reachable for
   the first time. **The write path must carry its app before selection may stop being global.**
   This prerequisite is the main reason this ADR exists.
3. **The cost is wide.** Every app-scoped route grows a parameter, and the server stops being able
   to answer "which app" from its own state.

So: Sage is a one-app-at-a-time tool inside a Project that holds many. That is a coherent thing to
be, and it is now a thing we have said.

## What made this hurt, and what fixed it

The complaint that produced this was not really about selection. `+ New App`, pressed four times,
gave four rail rows all reading `Unnamed Built App`, so a switch between two of them was invisible
and every sentence quoting an app's name said the same words about any of them. One current app is
liveable when you can see which one it is. #211 gave a nameless app the first request typed into it,
else its position in birth order, which is what makes the rule above survive contact.

## Consequences

- A person works in one Built App at a time. Switching is cheap, and a build in flight is not
  disturbed by it.
- A second browser tab is a second view of the same app, not a second workspace. Anything that
  wants two apps open at once is blocked on the write path first.
- Reversing this is allowed. It starts by giving every write its app, not by threading a parameter
  through the reads.
