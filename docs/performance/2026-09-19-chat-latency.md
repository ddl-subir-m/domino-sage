# Chat latency, re-measured 2026-09-19 — question 1 only

Ticket: #457. Predecessor: #400 (measured 2026-09-17).

**This is a new baseline, not a comparison to #400.** #400 ran on `sonnet`. The Anthropic route was
out of budget on 2026-09-19, so this ran on `domino/gemini-3.7-flash`. Step count is model
behaviour and step count is what #400 measured, so every number below sits against a different
model than the one it is set beside. Read the deltas as "a different model, on a newer build", never
as "the build got slower". The comparison #457 asked for is still owed.

**Question 2 did not run.** See "What is still owed" at the bottom.

## Conditions

| | |
|---|---|
| `sage_rev` | **c68fad5** — the `main` tip that day |
| Carries | `2f165e23` (#436), `95ac136d` (#435), `832dda2f` (rec 3) — all three verified as ancestors |
| Host | cloud-dogfood workspace `sage-subir-mansukhani-66a821b1` |
| Answering model | `domino/gemini-3.7-flash`, picked in the composer |
| Internal models | `sonnet` for `chat-intent` and `handoff` — not overridable, see finding 2 |
| Binding | `Snowflake-Data-Warehouse`, table `DWH.MARTS.MIXPANEL__EVENT` |
| Ledger at start | empty — fresh boot, no prior turns |
| Raw | `2026-09-19-chat-latency-results.json`, both turns, straight from `/api/diag/timing` |

## Question 1 — "How many Mixpanel events in 30 days, and distinct users?"

#400's exact wording. Answered correctly, with both readings of "30 days":

> Total Mixpanel events: 6,126,870 · Distinct users: 49,429 · *(across the 30-day window leading up
> to the latest recorded event, 6,394,087 events and 51,135 users)*

| | #400, `sonnet` | this run, flash |
|---|---|---|
| Total | 45.9s | **68.2s** |
| Table gate before it | not recorded | **8.3s**, a separate failed turn |
| What a person waits | 45.9s | **76.5s** |
| Model calls | 10 | **11** |
| `bash` calls | **5** | **0** |
| `live_read_query` calls | 0 | **7** |
| Time to first byte | 1.3–2.3s per call | **2.4–5.3s per call; 39.2s total = 57%** |
| `req` growth | 5KB → 78KB | 9KB → 93KB |
| `poll.sleep_ms` | — | n=56, p50 1000ms, 56.0s summed |

### The waterfall

| call | model | phase | ttfb | total | req | tools |
|---|---|---|---|---|---|---|
| 1 | `sonnet` | chat-intent | 3.7s | 3.7s | — | — |
| 2 | flash | chat-override | 2.6s | 2.7s | 9KB | — |
| 3 | flash | chat-override | 3.8s | 12.6s | 80KB | `live_read_query` |
| 4 | flash | chat-override | 3.1s | 5.8s | 82KB | `live_read_query` |
| 5 | flash | chat-override | 3.0s | 5.4s | 84KB | `live_read_query` |
| 6 | flash | chat-override | 3.9s | 8.3s | 85KB | `live_read_query` |
| 7 | flash | chat-override | 3.9s | 6.9s | 87KB | `live_read_query` |
| 8 | flash | chat-override | 5.3s | 8.3s | 88KB | `live_read_query` |
| 9 | flash | chat-override | 4.1s | 6.9s | 91KB | `live_read_query` |
| 10 | flash | chat-override | 2.4s | 3.1s | 93KB | — |
| 11 | `sonnet` | handoff | 3.4s | 3.4s | — | — |

Gates cost 4.7s before the first inference (`turn.acquire` → `setup.dispatch`). `after.handoff`
costs a further 3.4s after the answer is written.

## The sequential / independent split, with the working

#457 asks for the judgement per step, not a count. These are the seven queries in order, named by
their first result column as `sage.liveread` logged them.

| # | query | verdict | why |
|---|---|---|---|
| 1 | `TOTAL_EVENTS` (all-time totals) | **batchable** | depends on nothing; was not asked for either |
| 2 | `COLUMN_NAME` (which date columns exist) | **sequential root** | nothing windowed can run before this |
| 3 | `COLUMN_NAME` again, grouped | **redundant** | same discovery, read twice |
| 4 | `MIN_DATE` / `MAX_DATE` + 30-day count | sequential, needs 2 | |
| 5 | `DAYS_SINCE_MAX_DATE` | **redundant** | derivable from 4's own result, no new data |
| 6 | `EVENTS_LAST_30D` | **batchable into 4** | needs only 2, same shape as 4 |
| 7 | `EVENTS_30D_FROM_MAX` | sequential, needs 4 | earned it — produced the parenthetical reading |

**Genuine sequential depth is 3**, not 7: discover the date column, read the date range, window from
the range. Two queries are duplicates, two more fit inside an existing round trip. The ceiling here
is **7 round trips → 3**.

The table has four date columns (`DATE_PART`, `EVENT_OCCURRED_AT`, `MIXPANEL_PROCESSED_AT`,
`MIXPANEL_ARCHIVED_AT`) and the newest event is months old, so the orientation work is not the
model being careless. Queries 3 and 5 are.

## Verdict per #400 recommendation

**Rec 1 — "let the skill batch independent measurements". Still unquantified.** #400's number was
about the 41-step investigation, and the investigation did not run. What question 1 shows is that
the same shape exists at the small scale: 7 round trips with a sequential depth of 3. It does not
license a number for the investigation.

**Rec 2 — "find why a one-query question needs five scripts". Survived, and moved.** #436 worked:
zero `bash` calls, `live_read_query` seven times. The prompt rule ("do the whole job in one script")
is still not holding — the tool changed, the round trips did not. Rec 2 should be reworded off
`bash` and onto round trips, or it will read as fixed when it is not.

**Rec 3 — "strip `todowrite` on unbounded data turns". Confirmed shipped.** No `todowrite` in any of
the 11 calls. `832dda2f` is an ancestor of the measured build.

## Found while measuring — neither is #457's to fix

1. **A failed turn is recorded as a success.** Question 2 was killed by
   `gateway returned an error frame inside a 200 stream: Resource exhausted`, logged at 23:40:11
   with `gateway ended the stream with no finish_reason after 4.0s and 1 chunk(s)`. The ledger
   recorded `ok: true`, `decision: "answered"`, and **every call `ok: true`**, including the one the
   gateway killed. Any latency read off the ledger silently counts that as a clean turn.

2. **The ask slot degraded silently, and the composer cannot route around it.** `chat-intent`
   (call 1) and `handoff` (call 11) ran on `sonnet` — 7.1s of this turn, 10% of the clock —
   whatever the composer said. That pinning is **deliberate and documented**: `_model_for` at
   `scope.py:245` returns `catalog.ask`, and `table_rank.py:3` names itself "the third read-only
   classifier on the ask slot". Not a defect.

   What is a defect: on a capped ask slot all three fell back **silently**, and the turn read as
   healthy —

       chat intent: label=- confidence=0.00 context=yes fallback=invalid-json
       handoff: classifier returned an empty body — no suggestion
       table rank: names stage returned an empty body

   Each fallback is correct by its own design (`scope` fails OPEN, `table_rank` fails CLOSED to a
   layer heuristic). Nothing says the slot is down. Because the pin is deliberate, picking another
   model in the composer does not route around it, so a person keeps getting a turn that looks fine
   while the intent label, the model-ranked table shortlist and the Build offer are all gone.

## What is still owed

- **Question 2, the DMM investigation.** It ran 27.0s and 5 model calls — loaded the skill, ran one
  `bash` — before the Gemini route hit its spend limit. Not a measurement. #400's 598.6s / 49 calls
  / 41 `bash` stands unchallenged, and rec 1 stays ungated on a number.
- **The comparison to #400 on `sonnet`.** Nothing here is set against the same model. When the
  Anthropic budget returns, both questions want re-running on `sonnet` against this same `sage_rev`.
