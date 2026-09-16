---
status: accepted
---

# A gateway call carrying a Conversation is a turn

ADR-0043 locks a turn to an approved set of models once a declared Dataset is in scope, and keeps
the lock for the life of the Conversation because the transcript holds the rows from then on.
`_sensitivity_for_turn` (`backend/sage/orchestrator/service.py:15801`) is where that is decided, and
it takes `conversation` without a default on purpose. Its own docstring says why:

> Required rather than defaulted so that a harness added later has to answer it: a default would
> have made the hole reachable by omission, which is the shape the hole had in the first place.

That guard defends against a coder who forgets the argument. It cannot defend against a second
caller arriving at a handler that already answers the question for a different one, and by
2026-09-16 there were three callers and only one of them was the one the gate was written for.

**`_preview_approve_model`** (`backend/sage/orchestrator/app.py:4044`) passes `None`, correctly: the
previewed app's own model call carries the app's current Bindings and no transcript. But the route
it gates is mounted on the control app and answers anything with a socket inside the workspace —
demonstrated live on 2026-09-15, `POST /preview/api/llm/chat/completions` returning **HTTP 200**
from the Chat agent's own bash (#373). The shim gates tools by name only and `bash` is
unrestricted, so an agent reading `template/react-vite/src/appLlm.ts` finds a working recipe and
uses it. In the session that produced #370 the agent went looking for exactly this before anybody
suggested it. No adversary is needed.

**The handoff classifier** (`backend/sage/orchestrator/handoff.py:208`) calls `GatewayClient`
directly on `_model_for(catalog)` — the ask slot — carrying a digest of the Conversation, and never
consults the gate at all (#318).

**A Delegated model call** is the third, and it is why this ADR is being written now rather than as
a fix: #370 asks for a Chat turn to be able to use an LLM Alias the person bound, which is a
legitimate and frequently wanted thing, and which would have been a fourth way past the lock if it
had been built the way both tickets originally proposed.

## Decision

**One. Any gateway call Sage makes that carries a Conversation's content is a turn for the
sensitivity lock, whichever door it came through.** The door is not the question. Rows reaching a
vendor model is what ADR-0043 stops, and a call made on a person's behalf by the assistant is not
outside that because the assistant made it.

The alternative was to let a deliberately bound Alias ride outside the lock, on the argument that
binding it is the person's own declared intent. Rejected: it makes a Binding a documented way past a
data-protection promise, and the person binding an Alias is choosing a model to use, not waiving a
rule they may not know exists.

**Two. Three callers, and `None` stays legal for exactly one of them.**

| Caller | Names | Why |
|---|---|---|
| Previewed app's own call | `None` | No transcript. The app's current Bindings are the whole question. |
| Delegated model call | the Chat Thread | It is a turn of that Conversation. |
| Handoff classifier | the Conversation its digest came from | Same. |

A caller that cannot name a Conversation is refused rather than defaulted to `None`. The preview
mount keeps `None` because it is the one caller for which the answer is genuinely "no conversation",
not "nobody said".

**Three. Two fail modes, and they are not symmetrical.** When Sage cannot read its own gate:

- the **preview door fails open**, and keeps the reasoning already at `app.py:4050` — a preview call
  is not a turn, the creator is watching their own app, and the publish guard still refuses, so
  nothing ships on that path;
- a **Delegated model call and the classifier fail closed**. Both are turns. Failing open there
  moves a person's rows to an unapproved model on the strength of a read that failed.

Do not tidy these into one answer. Three doors, two answers, each with its reason written beside it.

## What a Delegated model call is allowed to do

The capability #370 asks for, bounded by the rule above.

- **It is a tool, not a route.** A custom tool plus MCP server, on the `live_read` precedent
  (`backend/sage/liveread/`). The gate then runs in Sage's own code rather than depending on the
  agent finding and correctly remembering a recipe, `GatewayClient` supplies the cost tags, and
  `data_use.py` already has a carrier vocabulary to record what the model received. Verified
  reachable: v1 `POST /session/{id}/prompt_async` sends custom and MCP tools, measured and recorded
  at `backend/sage/driver/opencode.py:264-271`. The v2 prompt path does not, which is why Live read
  could once never arrive; a tool built on v2 would load, list healthy, and never reach the model.
- **Only Aliases bound to this Conversation.** The bind is the consent, and it is an act the person
  took on this Conversation.
- **Outside the approved set it is refused, and the refusal names the set.** Never substituted
  silently. A person who asked for `opus` and got an answer from something else with no sentence
  saying so is the defect #293 and #317 are both open about.
- **Capped per turn**, at a named constant beside `_CHAT_TURN_MAX_S`, currently 25 calls. Refused
  loudly when reached. Without a cap a delegated loop can spend the whole 600s turn ceiling and
  produce nothing.
- **The transcript keeps a receipt, not the exchanges.** The agent's own answer persists as ordinary
  turn content. The individual call and response pairs do not. A classification pass over several
  hundred cases would otherwise put several hundred model answers into a transcript that is re-sent
  on every later turn, which is a cost problem and — by this ADR's own rule — a scope problem.
- **The person sees a step line** naming the Alias and the call count. Not a card: this may fire many
  times in one turn and a card each time is not proportionate. Not silence either: it is spend.
- **Cost tag `X-LLM-Tag-sage-component: chat-delegated`**, so `built-app` goes on meaning the
  previewed or published app and this spend is separable in the gateway usage dashboard.

## What this is not

**It is not a reason to hand the agent a token or a gateway base URL.** ADR-0052 makes the LLM
Gateway the trusted enforcement point and Sage's job is to reach it through a path that can be
gated. An ungated direct call is the thing `_forward_llm` exists to avoid, and
`backend/sage/resources/gateway_bypass.py` already scans app source for creators writing one.

**It does not make the path property load-bearing.** #373 offers "refuse the route unless the
request looks like preview traffic" as its cheapest option and is honest that it *"keeps out
accidents rather than intent"* — it is a property of the path, not an authenticated claim. Keep it
as a secondary signal. The gate being correct is what carries the promise.

**It does not add `/preview/` to `_UNPROXIED`.** Both #370 and #373 required that, and both were
right for a fix in which Chat reached the gateway over loopback HTTP. Under a tool, no Sage
component calls `/preview` over loopback at all, so the only loopback callers left are the ones this
ADR refuses — and an allowlist entry would suppress the prefix warning for exactly the traffic worth
noticing. `app.py:570` is left alone.

## What retires this

A gate that takes its answer from something other than the caller. Today `conversation` is passed in
by each caller and the rule is enforced by every caller passing the right thing, which is a
convention held by three call sites and a required parameter. If the sensitivity decision ever moves
to a place that can see the request's own provenance — the gateway itself, under ADR-0052 — then
"whichever door it came through" stops being a rule callers keep and becomes a property of the
system, and this ADR is worth re-reading rather than extending.

## Consequences

- #373 closes the hole and lands first, alone if #370 slips. A hole in a data-protection promise does
  not wait on a feature.
- #318 stops being a verify ticket. The decision its own comment deferred to the owner — whether to
  route the classifier through the lock or skip it under one — is made here, for all three callers at
  once.
- `CONTEXT.md` gains **Delegated model call**, and the `LLM Alias` entry points at it. The "one
  exception" clause there is unchanged and still correct: it is about the naming rule, and a
  delegated call is not an exception to it, because the call goes through an Alias and the Alias is
  still what it is called by. What the new term settles is the separate problem that three things in
  this codebase say "model".
- The fail-open at `app.py:4050` becomes load-bearing rather than incidental, and needs the comment
  that says so. A later reader tidying the three doors into one answer would reopen this.
