---
status: accepted
---

# A viewer hides what Sage read, but never a read that fell short

An answer in Chat arrives with disclosure beside it. [ADR-0056](0056-an-investigation-is-a-grant-the-person-makes.md)
says when the grant to query a store opened and closed. `data_used` says what was read, how much of it
came back, which fields were selected, and which model the gateway served. Both are correct and both
are load-bearing. Neither was written for a reader who did not ask.

Two complaints arrived together. The first is volume and it is a defect: `putDataUsed`
(`store.js:1765`) mints one block per `operation_id`, so a turn that reads a table, calculates on it
and then analyses text draws **three** "Data used" dropdowns stacked. The card was designed to
describe one operation and a turn routinely runs several. The second is audience: a reader who is not
a data scientist sees table paths, request ids and decision stages, and reads machinery where we
intended proof.

The first is fixed by grouping on `turn_id`, which `DataUse.record` already stamps
(`liveread/data_use.py:67`) and the browser already receives and ignores. That is a bug fix and it
needs no decision record. The second is this one.

## The decision

**A viewer chooses whether disclosure is drawn, the choice is theirs alone, and it never reaches the
work.**

`dataAccessShown` — boolean, fallback `false` — joins the preferences in
`backend/sage/workbench/js/prefs.js`. It is per-viewer and it spans Projects, for the reasons #52
settled: each Project runs the viewer in its own Builder container, so nothing written inside one
survives a switch, and the one thing that does survive is the Project's git repo, which is where a
viewer preference must never go.

It governs exactly two things:

- `data_used`
- the investigation opened/closed line (`store.js:1959`)

It governs nothing else. In particular it does **not** govern `investigation_offer`, which
`message-blocks.js:1342` draws *instead of* an answer. A hidden offer is a question nobody is asked
and a turn that renders nothing.

## What the preference may never hide

**A read that fell short.** `data_used` carries `coverage` — excluded, failed, unfinished — and
`requests[].failure`. When any of those is set, the card is drawn whatever the preference says.

This is not a new mechanism. The crossing receipt is collapsed by default and force-opens when an
Upload did not cross (`message-blocks.js:774`,
[ADR-0023](0023-an-upload-crosses-by-becoming-an-attachment.md)), because that refusal must never be
silent. The same argument holds here and for the same reason: hidden, a read that half-worked is
indistinguishable from one that worked, and the viewer's own preference is what made it so.

**Whether an investigation is open right now.** Hiding the opened/closed line loses the record of
*when* the grant changed. It cannot lose *whether* it is open, because `modes/chat.js:77` draws an
open investigation in the composer, live, with a Close button, outside the transcript and outside
this preference. That is what makes the line safe to hide and the offer not.

## Where the control lives

Both in the Account settings drawer, beside Conversation view, and on the answer itself.

The drawer alone would not do. Conversation view can live there because both its values are visible
states: you see split, you wonder about unified, you go looking. A preference whose fallback is off
is invisible, and nobody goes looking for a thing they have never seen. So the answer carries **Show
data access**, and the first click both reveals the card and records the choice — the same shape as
`chipScopeHintDismissed`, where the viewer's own action retires the nudge.

## The store reads it, components are handed the answer

`message-blocks.js:800` states the house rule and `test_only_the_store_branches_on_the_preference`
holds it: the conversation-view preference has exactly one reader and it is the store. This
preference follows it. The filter belongs in `historyToMessages`, `buildHistoryToMessages` and
`mergedHistoryToMessages`, not in `MessageBlock`.

It filters on **where the row came from**, not on which pane draws it. Under
`conversationView: unified` one transcript shows both halves, and a rule written as "hide it in the
Chat pane" would quietly change what it hides the moment somebody switched views.

Filtering the investigation line needs one thing the store does not carry today: the originating
event type. Fifteen distinct meanings mint into `{ type: 'status' }` and the type is discarded
(`store.js:1804`, `2532` and `3238`), so no status can be told from another on the block alone. We carry the
event type at **one** mint site, `store.js:1959`, and leave the other fourteen alone. The full split
is a real cleanup and it is not this ticket.

## The rule is a table, not a list

Every one of the 33 `block.type` values in the switch at `message-blocks.js:2110` gets a row, and
there is **no default**. A JS harness under `tests/js/` drives the real switch and fails when it holds
a type the table does not.

A hide-list would have drifted silently: a card added next year shows, and nobody finds out. A
keep-list drifts the other way and buries a new disclosure. Neither failure announces itself, and a
Python test grepping the literal would pin the string rather than the behaviour. The table is the
only shape where the population cannot go quietly short.

## Considered options

**Hide the tool cards instead.** Rejected on a fact: Chat has no tool cards. `historyToMessages`
skips every tool event (`store.js:1825`) and `_CHAT_SHOWN_TOOLS` is empty
(`orchestrator/service.py:3481`). The detail that annoys a reader in Chat was never the steps Sage
ran.

**Hide the table receipt.** Rejected three times over. It carries a Read again button, so it is an
offer. It is an [ADR-0045](0045-an-artifact-commits-the-shape-and-the-rows-only-by-consent.md)
disclosure, and its comment at `message-blocks.js:243` is explicit that a row count over a capped
read falsely claims the whole table. And it is drawn *instead of* the grid, so hiding it leaves an
answer referring to a table that is not on screen.

**Hide `data_used` unconditionally.** Rejected. It is the only surface reporting a partial read. The
quiet default is worth having; it is not worth a failure that looks like a success.

**A three-value ladder — concise, standard, full.** Rejected for now. `prefs.js` warns that a value
dropped from `values` reads back as the fallback, which is how the `dockTab: null` bug worked, so
removing a level later is a defect while adding one is free. Two values now, a third when it is
earned.

**Let the preference change what Sage does.** Rejected firmly. The same question would return
different answers depending on a display setting, and every bug report would be unanswerable without
knowing a value nobody thinks to mention.

**Per-conversation rather than per-viewer.** Rejected, narrowly. It is the better answer for dressing
a conversation for an audience, which was the secondary motivation. It costs a new storage location,
because state that must survive reaching another person cannot live in `localStorage`. Revisit it
when the demo case is the primary one.

**Name it "Data used", reusing the card's own label.** Rejected, narrowly. Reusing a shipped term is
normally right, and this one is already on screen. But an investigation notice is permission
*granted*, not data *used*, and a viewer who unticks "Data used" and then stops seeing investigation
notices has been surprised by their own setting. The group is named **Data access**; the card keeps
its own label.

## Pin refresh, 2026-09-19

The coordinates above were taken before this record landed, and `store.js` moved under them
(#447, `c06dfb2f`, shifted the region +30). Refreshed against `6388e9b8`:

| was | now | what is there |
|---|---|---|
| `store.js:1929` | **`store.js:1959`** | the `ev.type === 'investigation-state'` branch |
| `store.js:1841`–`2941` | **`store.js:1804`, `2532`, `3238`** | `historyToMessages`, `buildHistoryToMessages`, `mergedHistoryToMessages` |
| `message-blocks.js:2092` | **`message-blocks.js:2110`** | `switch (block.type)` |

The first one mattered more than line drift usually does. `store.js:1929` today sits inside the
investigation **offer** branch (it opens at `:1921` and mints `investigation_offer` at `:1931`) —
the one block this record says the preference must never hide. Left unrefreshed, this ADR pointed
its own implementer at the counter-example.

The old range also understated the work: it covered the first two history functions but stopped
short of `mergedHistoryToMessages`, which is the one that matters under `conversationView: unified`
— the case this record singles out. All three are named explicitly now.

`message-blocks.js:1342` (`investigation_offer`) and `message-blocks.js:774` (ADR-0023's
force-open) were checked and needed no change.

## Amendment, 2026-09-19: where the filter actually sits, and the default it has

Recorded at implementation (#448). Two things above are now wrong as written, and both were found
by review rather than by the build, so they are corrected here instead of left for the next reader
to rediscover.

**The filter is one level below the three functions named above.** This record says it belongs in
`historyToMessages`, `buildHistoryToMessages` and `mergedHistoryToMessages`. It is in `pushBlock`,
which those three reach through the two mint helpers — `putDataUsed` and the `investigation-state`
branch.

The reason is the question this record left open. `putDataUsed` has three callers, and the third is
the SSE reducer, which pushes straight into `state.messages` without passing through any history
function. A filter in the three would have drawn a card live that the same turn hides after a
reload: the viewer's preference honoured on the second look and not the first. Filtering in the
shared helper covers all three reads *and* the live turn, with one copy of the rule rather than
three that can drift. `mergedHistoryToMessages` is covered by delegating to `historyToMessages`,
not by a filter of its own.

This does not weaken the house rule the section above is really about. The store is still the only
reader of the preference, and a component is still handed the answer.

**"No default" describes the table, not the lookup.** `hiddenByDataAccess` ends
`return row ? !!row(block) : false` — a type with no row is DRAWN. That is deliberate and it is the
safe direction for a disclosure, but it means a missing row fails silently rather than loudly, so
the harness is the only thing standing between a new card and a quiet hole. Two further lessons
from getting that wrong, both worth more than the rule they came from:

- The harness derives the population with a pattern, and a pattern bounds what it can see. A
  narrow character class hid `case 'chartV2':` from the comparison entirely — neither counted
  against the table nor reported missing from it. Every field read clean. It now matches any quoted
  label and cross-checks the count against the dispatcher's own `case` keywords, which needs no
  pattern at all.
- "It governs nothing else" was spelled by thirty-one rows sharing one `shown` constant, and
  pinned by nothing. Flipping any of them to hide was a one-word edit that took a viewer's table
  receipts away and reddened no test. The governed set is now derived from the real table and
  asserted to equal exactly the two things this record names.

**Withheld disclosure is MARKED, not removed.** A block the preference hides stays in
`message.blocks`, in the position the read gave it, carrying `hiddenDisclosure`; the answer draws
every block that is not marked. This is `build_plan`'s `folded` shape, and it is a correction: the
first implementation moved withheld blocks into a side list with the index they would have had, and
spliced them back on reveal. Two defects followed, and both were found by review rather than by the
build.

The index went stale, because `dropTableCard` and `dropWithholdCard` remove shown blocks from a
message after a park and the reducer replaces its streamed blocks wholesale — so revealed
disclosure landed below the answer it belonged above, which is the one thing the ordering
bookkeeping existed to keep.

The second is the serious one. Restoring REBUILT `message.blocks`, and the SSE reducer caches a
position into that array and writes through it. A re-persist that force-showed a failed read
shifted the array under that cached index, and the next flush overwrote the card with streamed
text. **A read whose gateway request failed was deleted from the transcript**, with the count
cleared so no nudge said it had ever been there — the outcome this record forbids absolutely,
produced by the machinery meant to prevent it. Marking has no index to go stale and never reassigns
the array, so neither defect is reachable.

**A request that did not settle is read from `state`, not only `failure`.** This record names
`coverage` and `requests[].failure`. That is short: `data_use.py`'s `finally` writes
`state: 'interrupted'` without touching `failure`, so a response cut off mid-stream — a Stop, a
dropped connection — carried `{ state: 'interrupted', failure: null }` and was put away. A response
that visibly did not finish is a read that fell short, so `interrupted` now counts. `attempted` does
not, and deliberately: it is the state every request is persisted with the moment it opens, so
counting it would force-show every card until its requests settled and the preference would not
work during a live turn at all.

**A refused write still honours the click.** `prefs.set` refuses when storage is blocked or full,
and when the viewer's identity has not landed — which `prefs.js` itself calls a real window. On a
refusal the stored value does not move, so re-reading it answered with the value from before the
click: the re-partition was a no-op, the transcript kept hiding while the drawer's box sat ticked,
and the warning promised "it won't persist next time" when the truth was that it had not happened
at all. For a viewer in that state the nudge on the answer is the only way in, and it was inert.
The choice is now held for the session first and filed second.
