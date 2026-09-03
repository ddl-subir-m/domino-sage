---
status: accepted
revises: ADR-0011 (the never-a-toast rule, which held for content and is now stated as such)
---

# A Problem informs, and the chip holds what the toast points at

Sage already knows almost everything that is wrong with its own deployment. It tells the log and
`/api/diag`, and it tells the person nothing. `orchestrator/app.py` claimed a builder banner covered
the boot slot check; there was no banner, and `store.js` fetched `/healthz` only to read
`open_weight_models` off it and drop `preflight_slots` on the floor. `/api/preflight` documented
itself as "called by the UI just after the project view is live" and had no caller at all. So a
build could fail five tool calls in on a model slot that was already known-dead before the person
typed anything.

**Decision: a [[Problem]] is a condition Sage knows will make the person's next act fail — or
silently do something other than what it says — and it informs, never blocks. One chip in the
deployment-scoped chrome holds every Problem. One toast per Problem per session points at the chip.**

Three earlier rules appear to forbid parts of this. Each is narrower than it reads, and this is
where that gets written down.

## Inform, never block

The codebase already refuses acts in two places and informs in a third, and nothing said why they
differ. `_turn_slot_refusal` stops a turn whose slot is dead. `publish_problems` refuses a Publish
it cannot verify. `publishNotice` warns that an app's queries will fail after publishing, then lets
the person publish anyway — its own comment states the asymmetry: "an unverified credential is a
hole, an unwritten notice is not."

The line those three draw: **block when proceeding is unsafe or certainly wasted; inform when the
person may legitimately proceed.** That line is right and it is kept. Both existing refusals stay.

Nothing new blocks, for two reasons. A Problem can be wrong — a permission-cache blip reads as a
missing Alias, which `resources/preflight.py` already refuses to treat as evidence — and an informer
that is wrong costs attention where a blocker that is wrong costs the session. And a chip that also
locked controls would turn one dead service into a jail; the boot failure this work started from was
exactly that shape, where a dead Project listing gave a full-page "The workspace could not load" to
somebody who could still have built.

## Why a toast is allowed here, when ADR-0011 said never

ADR-0011 said "never a toast: five seconds is not long enough to read a file list and decide", and
`components/platform-error.js` said the same of a platform error body: "a toast is a corner that
auto-dismisses, which is the wrong placement for output somebody has to read."

Both rules are about **content**. They still hold, unchanged, and this decision adds nothing to a
toast that has to be read: the chip and its drawer hold every sentence, every remedy and every
quoted platform body, and they stay on screen for as long as the Problem is true. The toast carries
a count and points. An attention pull toward content that stays put is not the thing ADR-0011
rejected. Stated as a rule: **a toast may point at content; it may never be the content.**

The chip cannot be dismissed and the toast can. Dismissal is for repetition, and the chip does not
repeat — it is one element, lit or silent, and it goes silent by itself when the Problem clears. A
dismissable chip would let somebody hide a dead model slot and then report the failed build as a
bug.

## Where the "we could not check" rule ends

`/api/preflight` states it, and `resources/preflight.py` turns on it: "we could not check" is a
state, not a failure, and reporting an unknown as broken is the one thing it forbids. Kept — and it
collides with reporting an outage, because an unreachable gateway reaches the code as the same
exception as a listing that failed.

**The line is what you failed to reach.** Fail to reach the dependency itself — the LLM Gateway, the
token sidecar, the Domino API — and that is a Problem, because there the failure to check *is* the
fault. Reach the dependency but lose a sub-listing behind it, such as the endpoint listing behind an
Alias that resolved, and the old silence holds. These are different call sites, so the line is
implementable rather than a matter of judgement at runtime.

`preflight_slots` already returns `{"state": "unreachable", "error": ...}`, so the gateway-down
Problem needs no new probe. Neither does any of the other five. All six are wiring.

## What keeps the count low

The Workbench is crowded, so the rule matters more than the list. A condition is a Problem only when
all three hold:

1. It will make the next act **fail**, or make it **silently do something other than what it says**.
2. Sage knows **before** the act.
3. The sentence can name a **remedy, or the person who owns one**.

Part 1's second clause exists for one of the six and describes the worst of them: if OpenCode does
not resolve the `sage-*` agents, every mode falls back to an agent that denies nothing, so Ask mode
can write files and the build looks fine. A loud failure wastes a turn. That wastes trust.

Consequences of the test, recorded because they are the no-s: `last_gateway_error` is history and
fails part 2. A missing git credential fails part 1 until somebody presses Publish, which makes it
**act-scoped** rather than standing — a different placement, beside the button, and out of scope
here; `publish_problems` already owns that act. A sidecar outage is detected but attributed to the
gateway, and that is accepted: the creator's remedy is "tell your administrator" either way, and the
platform's own error text rides along for whoever debugs it. A separate sidecar probe would buy
precision nobody acts on.

## Owner, and why the payload carries a composed sentence

Sage composes every sentence server-side through `brand.text()` and the client renders it. A client
that composed them would put un-branded nouns on an OEM screen, which ADR-0014 forbids. Each Problem
carries `{ id, message, fix, owner, body }`: `id` stable, because the toast fires once per Problem
per session and a Problem must survive two consecutive Preflights before it is said; `body` the
platform's own words, quoted through the existing `SW.PlatformError` and never rewritten.

`owner` is `you` or `admin`, and it exists because most of the six are not the creator's to fix. A
port mismatch, unresolved agents, an unreadable `domino_data` and an unreachable gateway are all
somebody else's — and a creator still has to know, because those failures land on their build. So
the sentence names who owns the remedy and the drawer groups by it. A dead model slot has two owners
in truth: the creator can pick a different model, an administrator can register the Alias. It sorts
as `you`, because a Problem the reader can act on belongs in the reader's own group, and the message
keeps the administrator's half.

## Considered and rejected

**A status board over all ten services Sage depends on.** The three-part test cuts it to six, and
the discarded four were the ones no reader could act on. A board is also a standing cost in a
crowded UI for a state that is almost always fine; the chip renders nothing when clear.

**A second, maintainer-facing surface.** `/api/diag` already is one and it is good. The reader here
is whoever opened the Workbench, and that is a creator.

**Reporting a Problem in the transcript**, the way a turn refusal appears. A deployment fault
rendered in a Conversation reads as the assistant's answer. Wrong provenance.

**A background poll.** One gateway listing multiplied by every open Workbench, forever, to learn
what the next turn reports for free. Preflight runs at boot and after a failed turn, and reads the
TTL cache `_slot_listings_now` already fills.

**Reporting on first detection.** Domino reports a workspace running before its proxy serves, so a
boot Preflight sees a fault that clears itself in seconds. Preflight retries with backoff at boot,
and after boot a Problem must appear twice consecutively. A chip that lights for a blip becomes
furniture people stop seeing.
