---
status: accepted
extends: ADR-0014 (text Sage did not write keeps its words — here, the gateway's refusal)
---

# Recovery from a refused Recall is chosen and lossy

A Domino LLM Gateway can be given guardrails. One of them, live on the dogfood instance, is called
"Block phone numbers", and its pattern is `(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?
\d{3,4}[\s.-]?\d{4}(?!\w)`. Every leading group is optional, so what it actually requires is seven
digits with a non-word character on each side, and it accepts a decimal point as one. A Conversation
attached a JSON file of sales forecasts, three of them ran over a million, and
`"prediction": 1911296.51` was refused as a phone number.

That much is not ours. What followed was.

The file's contents reached the gateway as a tool result, which means they were in the OpenCode
session — in **Recall** (`CONTEXT.md`), the part of a Conversation the person cannot see. Recall is
sent again on every turn. So the next question, which attached a different file containing nothing
that could match, was refused too. And the one after that. The Conversation was dead, everything on
screen looked fine, and the message on screen told the person to remove values from a file that did
not contain any. The same file in a new Conversation was answered on the first try.

Two things had to be decided: what recovery does, and who asks for it.

## The shim was the obvious place, and it is the wrong one

`sage/shim/enforcement.py` already rewrites every outgoing request. It drops denied tools (`:221`),
strips denied writes from the message list, strips image parts for models that cannot read them, and
now drops `reasoning_effort` from a tool-carrying turn. It knows which Conversation a request
belongs to, from `state.chat_thread_id`. Filtering tool-result messages out of every request for a
Conversation that has been refused is perhaps fifteen lines, in a file whose entire job is exactly
that shape of edit.

It would also be durable and invisible, and those are the same property.

The Conversation would work again, and every answer it gave afterwards would be quietly worse. The
model would go on calling read tools, and the results would go on not arriving, and nothing on any
screen would say so — while Chat's own live label said "Reading clickstream.csv" about a file whose
contents were being removed a layer below. There is no version of that the person can reason about,
because there is nothing to reason from. It fails outright, too, in the case where the refused value
sits in a typed message or in Sage's own answer prose, which the lossy path at least surfaces by
refusing again.

The narrower version — remove only the message that matched — is not available at any price. The
gateway does not say what it matched, only that something did, and the OpenCode driver has no
delete, no revert and no fork (`sage/driver/opencode.py`). There is nothing to cut and no knife.

So: **clearing Recall empties it.** The person loses what the model had been told, knowingly, and
gets a Conversation that works.

## Compaction cannot do it, though it is the same seam

`chat_compact.py` already shrinks exactly this thing, at 70% of the context window, via
`POST /api/session/{id}/summarize`. It is tempting to reuse — and it cannot be, because summarizing
is a model call carrying the poisoned session. The gateway refuses that too. The tool that shrinks
Recall stops working at the moment Recall becomes the problem.

What survives is `chat_summary`, which renders the transcript with no model call at all and drops
tool calls as "transcript furniture" — the exact class the refused value came from. That is what
seeds the fresh session, so clearing costs the model its detail but not the thread of what was said.

## Offered, never automatic, and re-offered every time

Sage does not clear Recall on the person's behalf. The transcript stays on screen, so a model that
had silently forgotten it would answer as a stranger to a conversation the person can still read,
with nothing accounting for the change. The offer appears on the **second identical refusal**: one
refusal may be a blip, and answering a blip by throwing away context is a worse failure than the one
being fixed.

Declining does not suppress it, which is where this parts company with the Build offer
(`handoff.should_classify`, "a person saying stop"). Declining a handoff is a preference about a
want. Declining this is a judgment about a moment, made before trying anything else, and the refusal
has no other exit — hiding the offer after one "not now" rebuilds the dead end. It cannot nag: it
appears only on a turn that has already failed.

Both facts it needs are read back out of the transcript rather than counted: refusals are already
recorded there, clearing writes a `recall-cleared` event of its own, and `service._turn_revert`
sets the house precedent for deriving rather than storing. A counter would be a second account of
the same ladder, and it would not survive a Sage Builder restart — while the poison would, since
`_ensure_chat_session` reads the session id back off disk.

The refusal's identity is the gateway's own sentence, not the one shown. The shown sentence names
the turn's Attachment, and the whole reason the ladder exists is that the *next* turn fails on a
different one; keyed on the rendered prose, the two live refusals never connect.

## Two rungs, then an honest stop

Sage's own answers are in the transcript, so `chat_summary` can carry a refused value straight back
into the fresh session. That is the likely case, not the exotic one. So clearing has a second rung
that seeds nothing, and after that it stops offering: with Recall empty and the refusal unchanged,
the value is in the message just typed or the file it names, and there is nothing left for clearing
to reach. Saying so is the correct answer at that point, and the only true one.

Alongside all of it, every guardrail refusal now names the Attachments the turn read. It is not a
repair and it does not need the ladder — but it is the only part that addresses the cause, it costs
one sentence, and it would have turned an afternoon of investigation into a glance.

## Consequences

- A cleared Conversation answers worse than one that never failed, on purpose and visibly.
- Sage never redacts a person's data to get it past a policy, and never will as a consequence of
  this decision.
- Build now has the same ladder. It was deferred here, and the deferral cost more than expected:
  `error` was not in `_PERSISTED_EVENTS`, so Build's transcript kept no refusals at all — a reload
  got back `done: gateway error` and no sentence, and a ladder that counts refusals could not have
  been added without fixing that first. Build's session is filed per (Conversation, app), so its
  ladder counts per that pair rather than per Thread.

  No seed was needed in the end. Chat carries a written summary into the fresh session because its
  Recall is the only copy of what was said; a Build agent opens the app's own directory, so the
  files and the plan (ADR-0007) come back by being read. The offer card says so on that side rather
  than promising a summary that does not exist.
- The Chat → Build handoff plans in the Thread's own session, so a refused Conversation refuses the
  handoff too — and that is the likeliest next click, because the offer card is on screen when the
  turn under it fails. It reported the raw transport nest and wrote nothing to the Thread, so
  `recall.offer` counted to one forever. It now says the guardrail's own sentence (ADR-0014) and
  records the refusal, which is what puts that click on the ladder.
- The last rung is wired. `recall.terminal` shipped with this decision, was documented as the end of
  the ladder, and nothing called it: a Conversation started over completely and refused again got a
  first blip's sentence and no offer under it. `offer` is right to decline a third clear; what was
  missing was saying why it had stopped coming. Build also withholds "say try again to build it"
  there, where trying again has already failed twice.
- The handoff opens the ladder on ONE refusal (`recall.offer_now`), alone among the callers. "One
  refusal is noise" holds where a person can shrug and retry having lost nothing. A failed handoff
  is not that: it is a deliberate click, on a card already on screen, into the Thread's own session.
  The second click would only reach a rung the first already earned, so requiring it spends a
  failure in front of somebody who has just had one. Opening earlier does not CLIMB faster — the
  window is shared with `offer`, so escalation is still bought by a clear that did not work.

  The cost, accepted: the plan prompt carries a digest of the person's own messages, rebuilt from
  the transcript on every click. If the guardrail matched something they typed rather than
  something a tool read, clearing Recall cannot help — the digest comes straight back. The ladder
  still converges (the next rung, then the last rung's sentence says where the value must be), but
  it spends a clear finding out. That case only arises when the Chat turns succeeded and only the
  plan was refused; when Chat was refused too, the poison is in Recall and the clear is right.
- The guardrail paragraph is said once per run of identical refusals. It is four sentences long and
  it earns that once; the handoff made twice-in-a-row the NORMAL case, because the click that fails
  is the one made straight after the turn that failed, on the same poison, naming the same file —
  and the offer card underneath then said it a third time in its own words. The second row carries
  the one fact the first could not instead: a DIFFERENT request was refused the same way, so what
  was matched is in the Conversation. That is the evidence for the card.

  Two limits. A refusal naming a different Attachment is still said in full, because that pair is
  the one this ladder exists to connect. And an ANSWER resets the run, not a question: every retry
  is a question, so stopping on one would collapse nothing outside the handoff.
- An offer is retired by the clear it asked for. The reduce picking the newest suggestion ignored
  everything after it, so the re-read that runs immediately after `clearRecall` drew the card again
  — asking someone to start over from a session they had just started over.
- `Clear recall` is not offered outside a refusal. Once the concept has a name, a general control
  is a small addition, and no one has asked for one; a permanently visible "make the model forget"
  on every Conversation is a loaded gun.
- None of this makes the guardrail correct. It flags any run of seven to fifteen digits and calls
  the result a phone number, so it will keep refusing forecasts, order numbers and timestamps until
  the pattern is fixed on the gateway, which is not ours to fix.
