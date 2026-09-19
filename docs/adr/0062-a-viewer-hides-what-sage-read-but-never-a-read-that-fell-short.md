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
