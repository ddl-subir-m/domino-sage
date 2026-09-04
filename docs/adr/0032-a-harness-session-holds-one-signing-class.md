---
status: accepted
---

# A harness session holds one signing class

[ADR-0031](0031-the-provider-options-are-named-for-the-key-the-sdk-reads.md) made the Gemini
thought-signature round-trip work, and closed on the note that an OpenCode upgrade must re-verify
it. It missed something nearer to home: Sage breaks the round-trip itself, and no upgrade is
involved.

Gemini attaches a `thought_signature` to each tool call it makes and rejects the next request that
does not hand it back. That is a property of the *model*. But the transcript is a property of the
*harness session* — the OpenCode session that holds it — and OpenCode replays the whole transcript
on every request, not just the last turn. `EnforcementShim.handle` then overwrites
`request["model"]` with the router's decision on every single request, so one harness session runs
several models while OpenCode believes it runs one.

Put those together and a normal Auto build kills itself:

```
turn 1  plan       -> gpt-5.4  -> tool calls, no signatures   (written into history for good)
turn 2  implement  -> gemini   -> replays turn 1              -> 400
```

The 400 is verbatim: `Function call is missing thought_signature in functionCall parts`. Nothing
removes turn 1's tool calls, so every later Gemini turn in that session dies the same way.
Reproduced live on 2026-09-04 (#155). The asymmetry is one-directional and verified: `sonnet` and
`gpt-5.4` both accept Gemini's `extra_content` back unchanged, so only the Gemini-bound direction
needs anything.

The mismatch is not the override. It is that **signing is a session-level contract and the router
treats the model as a per-request choice.** So the model choice becomes sticky per session, by two
rules that do different jobs:

**The pin, in the router.** If any slot this session could reach signs, every Build turn resolves
to that model. `ASSIGNABLE_SLOTS` is the set — `plan`, `implement` and `ask` — because all three
share one harness session and the user may switch mode between any two turns. This is pure: the
catalog is already an argument to `resolve`, so the pin needs no state, no session key and no
history, and it is covered by a unit test with no mocks. `SIGNS_TOOL_CALLS` and `signs()` sit in
`router/models.py` beside `BEDROCK_SERVED` and `is_bedrock`, for the same reason: the concept is a
class of aliases, not one alias.

**The veto, in the shim.** A Gemini-bound request whose history already holds a tool-call message
with an unsigned first call does not go to Gemini. The predicate is
`keepalive.unsigned_tool_messages`, which already existed as a warning; it is now the gate, and the
warning is gone because the shape it warned about can no longer be sent. The veto runs immediately
after `request["model"]` is written and **before** `_strip_images` and
`split_parallel_tool_calls`, both of which read that field to decide whether to rewrite history.

Only the evidence is read in the shim. Where the turn goes instead is routing policy, so it lives
in the router as `resolve_unsigned`, and it takes two candidates in order: the model this turn
would have had if no signing model were assigned at all, then any other assignable slot that does
not sign. The second candidate is not a nicety. In the shape that reached a user the signing model
**is** the implement slot, so falling back to "the resolution without the pin" would hand back the
very model being refused — the first rule anyone writes here is wrong, and it fails on the only
case that has ever happened. The user's in-session pick is dropped along with the pin, because
picking a signing model on a session that cannot take one is a request that cannot be served.

When every assignable slot signs, there is nowhere safe to go. The request is sent as-is and the
gateway answers, which is exactly today's behaviour for a configuration the user built themselves.
A hard-coded safe default would be new machinery serving one alias, and a silent success on a
forged history would be worse than the 400.

Precedence, highest first: **veto > in-session act > pin > standing assignment.**

- The **veto** wins everything. It is physics, not policy — the alternative to obeying it is a 400.
- An **in-session act** (`pick`, `pick_chat`: the user moves the picker while the session is live)
  beats the pin, and forfeits Gemini for the rest of that session. The veto then keeps the
  aftermath correct.
- The **pin** beats a **standing assignment**, which is the whole point of it.

Two scope lines. The pin is Build-only: a Chat turn returns `_resolve_chat` untouched, because Chat
has no phases for a pin to hold still and because Chat compaction re-enters `resolve` through the
shim, where a pin would overrule `chat_compact.summarize_model_id` — and an unlisted alias fails
the entire request (ADR-0031). The veto does apply to Chat, since changing `chat_model` mid-thread
is an in-session act that poisons a thread exactly the same way.

Rescue escalation is a **one-way exit**. A rescued turn may break the pin to reach a stronger
model. That model's tool calls land unsigned, so the veto keeps every later turn in the session off
Gemini, permanently. A turn that is already failing is the wrong moment to defend a model
preference.

## Why not the alternatives

**Scrubbing the history** — rewriting unsigned tool-call exchanges into plain assistant text before
a Gemini-bound request — keeps the user's model choice in every case, which is its real appeal. It
also rewrites the transcript the agent reasons from, on the hot path, forever, and trades a loud
400 for silently degraded tool fidelity. The failure it creates is unfalsifiable from a log line.
Refusing to send is a smaller lie than sending a forged history.

**Remembering what we resolved**, instead of reading the history, is cheaper and needs no message
walk. It is also unsound on the exact path this bug lives on: `_recover_session` resurrects a
session from `opencode.db` after a restart, where the memory is empty and the poison is not. It can
also never unlock, and it latches on a text-only turn that left no tool call to reject. Evidence is
sound across restarts and strictly more accurate.

**A harness session per signing class** — add the class to the session key and open a fresh session
when it changes — is correct and loses the transcript at the plan/implement boundary, which is the
one handoff Auto exists to make.

**Making a signing model ineligible for a slot**, refused at the Model panel instead, keeps Auto's
phase switch intact and honest. It also means Gemini can never run an Auto build at all. Between an
explicit human assignment and an automatic optimisation, the human act wins.

**A partial slot set** (`plan` and `implement`, leaving `ask` out) is not a smaller version of this
fix. It is this fix with a hole on a path that ships today: read tools survive `READ_ONLY_DENIED`,
so an Ask turn makes tool calls, and `catalog.ask` feeds both Ask mode and the Chat default.

## Consequences

**One signing assignment in any slot makes the whole Build session single-model.** Auto with Gemini
in one slot is single-model Auto, not broken Auto, but the per-phase switch and the strong-model
escalation are both restricted. That is the price, and it is why the resolved model has to become
visible where the user picked one — the shim log already names it, and a guarantee a user cannot
see is a guarantee they file as a bug.

**Sessions already poisoned when this ships need no migration.** Their first Gemini-bound turn is
vetoed and falls back, so they stop 400ing. They also never get Gemini again, because nothing in a
live session removes a tool-call message. The escape already exists and is not new machinery: a
session id is keyed per conversation and per app (`_recover_session`), so a new conversation is a
fresh session. OpenCode's own auto-compaction can clear the poison too, at around 95% of the
window — the evidence gate then unlocks Gemini for free. That is a bonus. Do not plan around it.

**`SIGNS_TOOL_CALLS` and `BEDROCK_SERVED` must not intersect.** `split_parallel_tool_calls` takes a
parallel batch apart across messages, and a signed batch carries its one signature on the first
call — so splitting a signing model's batch would manufacture the rejected shape itself. This is
the second live cause named in the `unsigned_tool_messages` docstring. The sets are disjoint today.
A test holds them apart.

**The pin is a routing rule, and routing is restated in two other places.** `signing_slot` is the
one copy of its input: `llm_router` routes by it and `preflight.turn_slots` preflights by it. That
second reader is not tidiness — `turn_slots` refuses a turn whose model will not answer, so a copy
without the pin would both miss a dead signing alias and refuse a turn because a slot it can never
reach is dead. Its own docstring warned about that failure two paragraphs before this rule existed.

The third reader is the Build picker, which restates the precedence in JavaScript from a raw
catalog. It cannot compute the pin, so `status()["model"]["signing_slot"]` sends the slot name and
the picker marks it. Until it does, the panel names the slot's own model while the turn runs on the
signing one — the log line is correct, the panel is not.

**The predicate refuses more than the gateway would.** Probed live on 2026-09-04: unsigned history
is accepted when the last message is a user turn, and rejected only when the model must continue
from a tool result. So a turn's first request would pass and every request after it would fail.
Matching the gateway exactly would buy one Gemini reply and then break the turn mid-flight, with
the model changing under the agent. Refusing for the whole session is both the conservative
direction and the stable one.

**The round-trip is verified at the request level, not yet as a build.** The router and shim rules are covered by unit tests, and
and on 2026-09-04 both halves were driven through the shim against the live `cloud-dogfood`
gateway: the three unsigned shapes that return 400 straight at the gateway all answer 200 through
the shim on the fallback model, and a clean tool-calling turn resolves `signing-pin` to Gemini and
comes back **signed**. What that does not cover is a whole multi-turn `opencode run` build, which is
the last step — `~/.local/share/opencode` is global, so never two harnesses at once.
