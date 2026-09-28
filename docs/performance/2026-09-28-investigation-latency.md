# Investigation latency, measured 2026-09-28 — the #600 question

Ticket: #606 (part of #600, feeds #457). Raw: `2026-09-28-investigation-latency.json`, six turns
straight from `/api/diag/timing?n=10&format=json`.

**Where the time goes: the model, by a wide margin. Snowflake and Sage are small.** On the one run
that finished (`gemini-3.7-flash` answering, `mimo-v2.6-pro` judging), model time was about 86% of
the 501s. Snowflake was 2%, and Sage's own setup and finalization under 1%. The `mimo-v2.6-pro` run
the ticket asked for **could not be measured**. On this build it stops within 30s, every time,
because its `live_read_query` arguments are not valid JSON (finding 1).

## Conditions

| | |
|---|---|
| `sage_rev` | **e8d6d83**, the `main` tip carrying #601 to #605, #607 and #608 |
| Host | cloud-dogfood workspace `sage-subir-mansukhani-66a821b1`, restarted just before |
| Binding | `Snowflake-Data-Warehouse`, as a conversation chip |
| Prompt | #600's, verbatim, with `@mimo-v2.6-pro` as a model chip |
| Investigation | accepted on the card ("Look across my data") every time |
| Ledger at start | empty, fresh boot |

**The #600 turn itself was not measurable.** The timing ring is in memory and the workspace had
been restarted, so ticket step 1 (find the #600 turn) returned nothing. Every run below is also on
the build *with* the #600 fixes, not the build the transcript ran on.

## The runs

| | `mimo-v2.6-pro`, run 1 | `mimo-v2.6-pro`, run 2 | `gemini-3.7-flash` |
|---|---|---|---|
| Outcome | broken tool call | broken tool call | **answered**, 9 customers with case and call ids |
| Total | 27.4s | 32.5s | **500.9s** |
| Turn-model calls | 2 | 2 | 44 |
| `live_read_query` that ran | 0 (2 invalid) | 0 (2 invalid) | 5 |
| Judging calls (`mimo`, delegated) | — | — | 4 |
| `bash` / `write` / `read` | — | — | 17 / 15 / 2 |
| Request size, first → last | 63 → 65KB | 63 → 65KB | 63 → 253KB |

The investigation offer before each run is its own 5.0s turn: the chat-intent classifier call
(`mimo-v2.6-pro`) timed out at its 5s cap on all three (finding 3).

## Where the `gemini-3.7-flash` run's 501s went

`scripts/turn-timing.py`'s partition, over that record:

| Bucket | Seconds | Share | What it is |
|---|---|---|---|
| model (turn) | 219.8 | 44% | 44 `gemini-3.7-flash` calls plus the classifier |
| model inside a tool | 208.2 | 42% | the 4 `delegated_model_call`s to `mimo-v2.6-pro` |
| polling, between steps | 57.4 | 11% | about 1.2s per step with nothing else measured |
| tools (real) | 13.2 | 3% | 5 `live_read_query` = 11.7s; 17 `bash` = 1.3s |
| gates + finalization | 2.2 | <1% | |

Per call:

- **`gemini-3.7-flash`**: TTFB median 2.2s, 4.9s mean per call. The request grew from 63KB to 253KB,
  and late calls took longer (8.7–14.0s from call 36 onward).
- **`mimo-v2.6-pro` judging**: 33.6s, 74.1s, 37.1s and 62.6s, TTFB 2.8–6.5s. So nearly all of that
  is generation.
- **`mimo-v2.6-pro` as the turn model** (4 calls, 63–65KB): 11.3s, 5.3s, 15.3s and 7.3s, TTFB
  3.7–9.1s. That's 2–4× `gemini-3.7-flash` at the same request size.
- **Snowflake**: 2.3s mean per statement, including the round trip.

## Verdict

The #600 transcript's ~36s per step is **model time**. `mimo-v2.6-pro` already takes 5–15s per step
on a 63KB request, where the investigation starts. By call 36 the same conversation is 200KB+,
roughly three times that. The one timing we have of `mimo-v2.6-pro` on a large request is judging,
at 34–74s a call. Snowflake at ~2.3s a statement and Sage at ~1.2s between steps can't make up 36s.

What the numbers point at, in order:

1. **Model choice for the turn.** `mimo-v2.6-pro` is 2–4× slower per call than `gemini-3.7-flash` at
   equal size. Before latency even matters, it currently can't make a Live read call (finding 1).
2. **Step count.** 30 of the `gemini-3.7-flash` run's 49 calls were a `write`/`bash` loop. Each step
   costs a full model round trip, 2–14s, for under 0.1s of tool work. The skill's route
   (`live_read_table`, `operation=analyze_text`, `sql` naming the candidates) is one call per
   source, and the model did not take it (finding 2).
3. **Judging cost.** Four delegated `mimo-v2.6-pro` generations were 42% of the turn. Batch size, or
   a faster judging alias, is the lever there, not Sage.

## Findings (each needs its own issue, per #457's rule)

1. **`mimo-v2.6-pro` can't call `live_read_query` on `e8d6d83`: its arguments are not JSON.** 4 of 4
   calls across two fresh runs, and `sage.shim` logged `tool arguments unparsed` for every one
   (lengths 391, 391, 396, 337). The mechanical repair declined, as designed: it fixes raw newlines
   and trailing commas, never a quote or an escape. OpenCode then rewrote each call to `invalid`, and
   the repeat brake stopped the turn. The arguments themselves aren't recorded anywhere (by design),
   so the bad character isn't known. The `live_read_query` args schema did not change since the
   #600 build; its description did (#603 added the Snowflake regex note), and so did the
   investigation skill (#605). On the #600 build the same model made 15 valid calls. **This blocks
   the controlled `mimo-v2.6-pro` re-run the ticket asked for.**
2. **The investigating model didn't take the `analyze_text` + `sql` route.** `gemini-3.7-flash` read
   candidates with `live_read_query`, then spent 30 calls in `write`/`bash` and judged through
   `delegated_model_call` instead. `analyze_text` batches are not on `timing.model_call` (#607
   review finding 3), so a run that does take that route will read shorter than it is.
3. **The chat-intent classifier times out on `mimo-v2.6-pro`.** 3 of 3 at the 5.0s cap, so every
   first turn with that model pays 5s for a fallback label.

## What is still owed

- The `mimo-v2.6-pro` controlled run: blocked on finding 1.
- The #600 turn's own record: lost to the restart. The ring doesn't survive one.
