---
status: accepted
extends: ADR-0017 (the catalog is the door for Auto and Ask), bounded by ADR-0032 (a harness
  session holds one signing class, so the model a slot names is not always the model that runs)
---

# An effort belongs to an assignment, and follows the model that runs

Reasoning effort is modelled today as a Chat setting: `SessionState.reasoning_effort` is one
string, its comment says "for Chat", and `enforcement.py` sends the field only when
`state.chat_thread_id` is set, the turn carries no tools, and the resolved model is the one the
person picked in Chat. Build has no effort control at any layer — not in the model menu, not in
`setBuildModel`, not in `set_catalog`, not in session state. A Build turn has never sent the field,
on any model.

That is the wrong shape, and the right one is not "add a Build effort". **An effort is half of a
Model assignment**, carried per slot beside the model:

```
plan:      {model, effort}
implement: {model, effort}
ask:       {model, effort}
```

## Why per slot, and not one Build-wide effort

A single Build-wide effort cannot say _think hard while planning, cheaply while implementing_ —
which is the entire reason the slots are separate. ADR-0017 already refused the mirror of this
mistake for the same reason, quoting `PRD.md` R6.1: "otherwise 'plan strong / implement cheap' is a
story, not a behavior". An effort that spans both phases gives that split up on the one axis where
it costs the most, because effort is priced per token of thinking and planning is where the thinking
is wanted.

Per slot also costs no new surface. `model-assignments.js` already draws one row per assignable
slot, with the model select on the right of a label; an effort select is a second control on a row
that exists. One Build-wide effort would need a row that belongs to no slot, which is a new kind of
thing on that drawer.

## The part that goes wrong without this written down

**When the signing pin moves the model, the effort moves with it.**

An effort belongs to the model that actually runs the turn, not to the slot that was asked. A
session pinned to `implement`'s model (ADR-0032) runs `implement`'s effort through the plan phase
too. Anything else sends `plan`'s effort to a model that never advertised it — and on two of the
aliases that validate the field, that is a 400 rather than a shrug.

The same rule settles every other way the model can move, and it is one rule rather than four
because it is the same fact each time — the field is validated by the model, so it is chosen by
whatever chose the model:

| The model moved because | The effort comes from |
|---|---|
| the signing pin (ADR-0032) | the slot the pin resolved to |
| the signing veto (`resolve_unsigned`) | the slot the veto landed on |
| an in-session act (`pick`, `pick_chat`) | the act itself; the person picked a model, so they picked its effort |
| nothing — the standing assignment routes | that slot's own effort |

Which makes the effort a property of the router's decision, not something the shim reads off the
slot afterwards. `ModelDecision` already carries the model and the reason it was chosen, and the
shim already learned once (ADR-0032) that rebinding the decision beats editing the request: a
summary that names one model while the request carries another contradicts itself. An effort read
off `catalog.plan` while the request runs `catalog.implement`'s model is that same defect with a
second field.

The in-session override follows from this rather than needing its own rule. An override replaces the
model, so it replaces the effort. Falling back to the slot's effort there is the version anyone
writes first, and it applies a level picked for a model that may not accept it.

## Validity is per alias, and is measured

Probed live on `sage.gcp.cs.domino.tech`, 2026-09-12. A nonsense `reasoning_effort` value is the
discriminator: a 400 means the alias validates the field, a 200 means it silently discards it, and
a valid value alone cannot tell those two apart.

```
alias                      no field  effort=low  low+tools   nonsense value
gpt-5.4                    200       200         400         400  -> honours it, but not with tools
domino/gemini-3.7-flash    200       200         200         400  -> honours it, tools and all
sonnet, Opus-4.8, haiku,   200       200         200         200  -> discards it
  Gemma 4 31B, 26B A4B
gemma-4-31b                502       502         502          —    endpoint stopped
```

The two refusals, verbatim, because the numbers above are only readable against them:

```
gpt-5.4                  400 "Unsupported value: 'reasoning_effort' does not support ..."
domino/gemini-3.7-flash  400 "Expected 'reasoning_effort' to be one of: 'high', 'low', 'max',
                              'medium', 'minimal'"
```

Two facts fall out of the Gemini row that no name heuristic could have produced. The second
refusal above is where its enum comes from, and of those five values `minimal` 400s at Vertex
("Thinking level is unsupported: THINKING_LEVEL_MINIMAL"), while `max` is absent from
`provider._EFFORT_VALUES`, so `parse_reasoning_efforts` drops it. The gateway therefore advertises
one level the backing model rejects and one Sage silently discards. Publishing the advertised enum
verbatim offers the broken level and hides a working one.

So "which efforts does this alias accept" is a measured table (#280), not
`reasoning_efforts_for()`'s `gpt-5`-in-the-name match, and not the gateway's `inference_params` —
which is `{}` for every alias, gpt-5.4 included, read straight off `/api/aliases` rather than
through Sage (#284). The data-driven path has never had input.

Nor is validity global. `provider._EFFORT_VALUES` is one union of every value any alias might take,
and it is the wrong shape for the question a picker asks — a person offered `xhigh` because some
alias somewhere accepts it gets a 400 from the alias they are actually on. One table per alias
(#280) is the shape; the union stays only as the parser's sanity filter, which is all it was ever
sound for.

## What changes on disk

`.sage/model_overrides.json` stops holding a bare model id per slot and starts holding
`{model, effort}`. That is why this is a decision rather than an implementation detail: the file is
committed, it is shared with everyone in the Project, and it is read on every resolve of that
Project's catalog.

Migration is a read-side concern only. A bare string where an object is now expected is an
assignment made before this shipped, and it means **this model, no effort** — which is exactly the
behaviour that assignment has had all along, so the migration preserves it rather than guessing at
it. Nothing rewrites the file on read; the new shape lands the next time the person saves that row.
`set_catalog`'s existing refusal of an unknown slot key stays for the reason it was written — the
write lands before the catalog is rebuilt, so a bad key is a durable brick rather than a transient
400 — and the same argument now covers a malformed value, and an effort the alias does not
accept: both are refused on the way in, never tolerated on the way out. #281 puts them on that
path.

## Considered and rejected

**One Build-wide effort.** Rejected; see above. It deletes the per-phase cost split that Auto
exists for, on the axis where that split is worth the most.

**Leave it on the override chip only, as Chat has it.** Rejected. An override dies with the Sage
Builder and is not shared (ADR-0017), so "plan hard" would have to be re-picked by every person in
the Project on every restart. Effort is a cost-and-quality decision about the Project's work, which
is the definition of the thing the drawer holds.

**Keep effort a Chat property and give Build nothing.** Rejected, and it is the status quo. Chat is
the one surface where the field is _least_ useful: `enforcement.py` drops it whenever the turn
carries tools, and Chat turns always carry tools. A Build plan phase is a tool-carrying turn too,
which is a real limit on gpt-5.4 — but it is not a limit on Gemini, which takes the field with tools
and all.

**Derive the effort from the model name.** Rejected. It is what `reasoning_efforts_for` does for the
_set_ of valid values, and the probe above is the evidence against extending it: the name cannot
predict that gpt-5.4 refuses the field alongside tools, nor that Gemini's own advertised enum
contains a level its backing model rejects.

## Consequences

- `SessionState.reasoning_effort` stops meaning "the Chat pick" and starts meaning "the effort for
  the model this turn resolved to". The `state.chat_model == request["model"]` guard in
  `enforcement.py` was already doing the job this ADR generalises — never send an effort to a model
  nobody chose it for — so it survives, with the decision as its subject rather than the Chat pick.
  What it must not become is one such comparison per slot, which is the same rule written three
  times and drifting twice. The send path is #282's to write.
- The hardcoded `"low"` floor for Chat-on-Auto stays, and an effort on the `ask` assignment beats
  it. The floor exists because no pick meant no field, and a data question was answered at the
  alias's own default; an assignment that names an effort is a pick, so the floor has nothing left
  to do on that turn. Where that precedence lands in the code is #282's.
- The Chat picker is unchanged. Its effort was always the in-session-act row of the table above,
  and it keeps behaving as it does today.
- `preflight` gains nothing. It answers "can this slot's model be reached", and an effort cannot
  make a reachable alias unreachable. An effort the alias would refuse is refused on save
  (#281), against the per-alias table (#280) — not at turn time.
- `CONTEXT.md` is deliberately not touched yet. Its **Model assignment** entry opens "The model a
  Build mode runs on, chosen once and kept", which will be wrong once it carries an effort, and
  the glossary will gain **Effort** — but the glossary describes the product as it is, and neither
  is true until #281 and #283 land. They move with the code, not with this record.
- Blocks #281 (persist per slot), #282 (send on Build turns) and #283 (the two surfaces). Does not
  block #280 (the per-alias table), which is measurable today and useful on its own.
