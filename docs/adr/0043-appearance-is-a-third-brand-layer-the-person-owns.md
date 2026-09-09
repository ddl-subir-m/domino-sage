---
status: accepted
extends: ADR-0014 (the brand pack, its author-time substitution and its refusal of remote assets —
  this adds a layer under those rules rather than beside them), ADR-0026 (the lint over marked
  positions, which now skips the theme id because an id is not a name)
---

# Appearance is a third brand layer, and the person owns it

The brand pack was built for one reader: an OEM partner who bakes `brand.json` into an Environment
image and never touches it again. `docs/workbench/brand.md` says so under **Locked** — *"OEM /
partner overlay… Not per-organization on one process"* — and `brand.py` opens with *"One pack per
process; not per project."* Everything about it is read-only by construction: two file paths, both
resolved at request time, neither ever written.

The ask was to change the product's name and its theme from inside the running product, from
Account settings. That is a person, at a keyboard, changing a pack — which is a reader the pack was
explicitly not designed for.

## Deployment-wide was asked for and is not what shipped

The first shape considered was an admin editing one pack that every viewer on the deployment then
reads. Two things in the codebase refuse it, and neither is a small refusal.

**There is no shared writable store.** A Sage Builder is one user in one project in one container,
with one orchestrator process bound to one project volume — `/api/projects/{id}/open` says switching
project means *leaving this container for another one*. Nothing outside `/mnt/code` is written by
this process, and `/mnt/code` is the wrong scope twice over: a collaborator pulling the repo would
inherit somebody else's theme, and one person's two projects would disagree about what the product
is called. `brand.md` already rules `.sage/brand.json` out by name. Writing a file in one container
reaches nobody else and dies with the container.

**There is no admin.** `/api/me` returns an `id` and a `name` off the viewer JWT and nothing more; no
route in the process reads a role, and `manageUrl` — the closest thing to an admin door — is a
deployment config flag, not a permission. `Manage` is a separate Domino App precisely because the
Workbench does not implement that persona. An admin-only route would mean inventing the concept.

So deployment-wide is a real project — shared storage, an authorization model, and an amendment to
ADR-0014's "no per-org packs" — and not a settings panel. What ships instead is the layer that is
honestly available.

## The decision

A third layer under the two that exist, read last:

```
brand.DEFAULT  →  /opt/sage/brand.json  →  SAGE_BRAND_FILE  →  the override
```

It lives at `~/.config/sage/brand.json`, and `SAGE_BRAND_OVERRIDE` moves it. It goes through the
same `_merge` as every other layer, so a file hand-edited into nonsense warns and falls back rather
than stopping the boot — the rule ADR-0014 set for packs holds for this one, and matters more now
that a person can reach it.

**Three keys, `brand.WRITABLE_KEYS`: `productName`, `assistantName`, `theme`.** The logo, the
favicon, the nouns and the peer products stay the OEM's to bake. Those are the keys ADR-0014 built
an image allowlist and a lint around, and a text field on a settings panel is not where that gets
re-litigated.

**An empty value drops its key rather than storing `""`.** Without that, a text box could take a
name away from a baked pack and never give it back.

**A theme is mostly CSS.** The pack carries the id and the three colours it already had, because
those are the only parts read outside a stylesheet: Ant Design's `colorPrimary`, and Highcharts'
first accent. Everything else a theme changes — the top bar inverting from dark to light, the two
type faces, what a heading weighs, whether a chip is a pill — is `[data-theme]` in
`css/tokens.css`. Picking a theme picks its palette; an explicit `colors` block still outranks it,
which is the only reading under which naming both means anything.

## The scope this actually has

Container-local, and the honest statement of that is two sentences, not one:

- On a **Sage Builder**, one container is one person in one project, so this is a per-person
  answer that does not follow them to their next project.
- On a **published Workbench App**, one container serves every viewer of it, so there it is shared
  by all of them — with no admin gate in front of it, because there is no admin to gate on.

That second case is the one to read twice. It is accepted here because the App's viewers are
already the App's publisher's audience and the blast radius is a name and a stylesheet, not data —
but it is the reason `SAGE_BRAND_OVERRIDE` exists. A deployment that grows a volume mounted into
every container points that at it and gets one answer for everyone, with none of this code
changing. That is the upgrade path to what was originally asked for.

## A writable pack is a new trust boundary

`ui()` substitutes the pack into `index.html` and `door.html` and escapes nothing. That was safe for
exactly as long as every value in a pack was baked into an image by a partner. It is not any more:
`assistantName` now reaches markup from a text field.

Names are therefore refused rather than escaped when they carry `< > " ' \``, or a control
character, or run past 40 characters. Refused, because nobody's product is called `<script>`, and an
escape would have to be undone again by every other reader of the pack. This is the one place the
brand code raises instead of warning — a pack file nobody is looking at must not stop the boot, and
a form somebody just submitted has a person waiting to be told what was wrong with it.

## Renaming the assistant restarts it

OpenCode reads `opencode.json` once, at start. A rename that only rewrote the file would leave the
agent introducing itself by its old name until something else restarted it — and that is the half of
a rename a person actually watches for. So `PUT /api/brand` re-runs `_install_opencode_config` and
drops the server holding the old words, and `_ensure_opencode` starts a fresh one on the next turn.
Somebody who renames the product and then walks away pays for no restart at all.

It takes the turn lock to do it, like every other act that moves the ground under a running build,
and answers 409 when a turn holds it. The sentence is written at the route rather than taken from
the service, because the service's would read as a rename that failed: it did not, the name is on
disk and the screen already wears it, and what waits for the build is only the agent's voice. A
theme change never takes the lock — it is CSS, and nothing behind the browser reads it.

## Two smaller consequences

**The lint skips `theme`.** `brand_lint` forbids every string value in `DEFAULT` at a marked
position, which is what makes a renamable name unwritable in prose. A theme id is not a name — it is
a key of `brand.THEMES`, chosen to label a stylesheet block, and no screen prints it: the picker
labels the Domino theme with `{platformName}`. Left in, the four marked strings that say `domino`
about the **gateway mode** became bare-name findings, and the lint would have been reporting on an
identifier ADR-0014's third arm tells it not to touch.

**The Google Cloud theme does not ship Google's fonts.** `index.html` carries Inter from our own
origin and says *"Do not `<link>` Google fonts"* — an air-gap rule, the same one behind ADR-0014's
refusal of remote images, not a style preference. So the theme names `Google Sans` first and falls
back to Roboto and then the system sans. It is right about the colours, the inverted bar, the
heading weights and the pill chips, and wrong about the letterforms for anyone who does not already
have the face installed. That is the correct trade and it should not be quietly fixed with a
`<link>`.
