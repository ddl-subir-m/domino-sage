---
status: accepted
supersedes: the runtime half of #494 (the shim withdrew `apply_patch` after two corroborated
  refusals so the model would fall back on `edit`)
extends: ADR-0057 (a gateway call carrying a conversation is a turn — the window this detector
  measures is that turn), ADR-0066 (native reasoning stays with the harness — the same principle,
  that what the harness decides structurally is not re-decided per request in the shim)
---

# A model's edit tool is chosen by its handle, not recovered from at runtime

OpenCode picks a model's edit tools from the model id it is handed, and picks exactly one of two
sets. An id containing `gpt-` (and not `oss`, not `gpt-4`) is offered `apply_patch` and neither
`edit` nor `write`. Every other id is offered `edit` and `write` and no `apply_patch`. This is
`ToolRegistry.tools` in the pinned 1.18.4 binary, and Sage's `_gives_patch` is a copy of that test.

Sage names that handle per prompt (`_tool_handle`, #539): `gpt-5.4` when the turn will run on GPT,
the neutral `sage-model` otherwise. So the tool set is decided once, before the turn starts, by
the one fact that determines it.

## The decision

**The tool a model edits with is chosen at the handle, and nothing downstream takes it away.**

The shim does not withdraw an edit tool from a request, and no future mechanism should. A turn
always keeps the edit tool OpenCode offered it.

**The refusal detector stays, and only measures.** `patch_refusals` is still counted in
`phase_classifier.assess()` and still printed on the `model policy: rescue …` line. It changes
nothing about the request.

## Why the runtime recovery went

#494 measured a real failure on 2026-09-21: 32 `apply_patch` calls, 25 refused by OpenCode's
parser, thirteen minutes, and a model rescue that resolved to the same model every time because
both slots held it. Its finding was right — *the model cannot write the envelope, so change the
tool, not the model* — and the fix it shipped was a runtime detector: after two corroborated
refusals, strip `apply_patch` so the model falls back on `edit`.

#539 then solved the same problem one layer earlier and structurally. The model that could not
write an envelope is now handed `edit`/`write` before it is asked to do anything, because that
follows from its handle. The refusal that the detector waited for does not happen.

And on the requests that can still refuse a patch, the recovery was not merely redundant — it was
the wrong act. It required `edit` on the same request as `apply_patch`, which OpenCode never emits.
So it could not fire; and had it fired, it would have taken away the turn's only edit tool and left
it unable to edit at all. That is worse than a refused patch. It stayed green for months on a test
fixture listing `apply_patch`, `edit` and `write` together — a request no producer can send.

**A runtime detector that strips a tool after two failures is a worse version of a problem now
solved before the turn starts. Do not re-add one.**

## What was considered and refused

Acting on the *handle* rather than the tool list: on two corroborated refusals, stop sending the
`gpt-` handle for the rest of the turn, so OpenCode offers `edit`/`write` on the next call. This is
the only route that gives a GPT turn a real escape, and it does not leave anything toolless.

It was refused as speculative. Nobody has observed a GPT model repeatedly refusing its own
`apply_patch` envelope — the #494 measurement was a Gemini model that had been given `apply_patch`
because Sage's single handle was `gpt-5.4`, which is the condition #539 removed. Keeping the
detector means that if it ever does happen, it appears in the log with a count, and the fix gets
built on evidence rather than on this paragraph.

## What this constrains

- Nothing may remove an edit tool from a request. A guard that wants to change how a model edits
  changes the **handle**, which changes the offer, and does so before the turn.
- `PATCH_TOOL` and `PATCH_REFUSAL_MARKERS` keep their current meaning: they name the tool and
  recognise its refusals, for counting.
- A test that exercises patch refusal uses a request OpenCode can emit — `apply_patch` with no
  `edit`/`write`, or `edit`/`write` with no `apply_patch`, never both.
- Prompt text follows the same rule: the instructions a model reads name only the tools its handle
  was offered (#541, #551), so the patch envelope rides the turn prompt of a GPT turn and the
  static prompts describe `edit`/`write`.
