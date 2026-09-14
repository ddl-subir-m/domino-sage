# Earnings dashboard diagnosis — 14 September 2026

Two Sage defects are reproduced and fixed locally. The changes are not deployed.

## Evidence

The source project is `ddl-subir-m/sage-subir-mansukhani-6aa15760`.
The app is `app_1a0a0b34050dafc817c39`.
The local Sage baseline is `f9130b05eed362f10ee99280686d3193cbd6ca02`.

- In app commit `2bf8f6d`, `src/App.tsx` still renders the shipped starter screen.
  `DashboardTable.tsx`, `DetailPanel.tsx`, and `data.ts` exist but are not used by that entry.
  The history records a clean build at 16:19:33 UTC. This proves the missing screen;
  it does not prove the agent's later claim of a synchronization fault.
- Commit `a000b6f` connects the dashboard after the user reports the missing UI.
- Errors at 16:51:03 and 16:54:55 UTC both say `Upstream idle timeout exceeded`.
  Their stored reasons have no guardrail name or PII finding. The second error triggers
  `recall-suggest`. Repeated timeouts alone are sufficient to produce the false content-refusal card.
- The successful build records name `Gemma 4 31B`. The earlier Chat records name
  `Gemma 4 26B A4B`. A failed retry's final record names `Opus-4.8` in planning;
  these are Sage's routing records, not independent proof of the provider that served each call.
- The repo includes a later successful watchlist build, `c4317de`, at 16:59:26 UTC.

## Checks and results

Run from `backend/`:

```sh
uv run --extra dev pytest -q tests/test_timeouts_do_not_offer_to_clear_recall.py tests/test_a_placeholder_is_not_a_finished_build.py
```

Before the changes: **6 failed in 3.28 seconds**. The checks exercise the real
recovery functions, Build event stream, feedback runner, and repair loop. Model responses
and the compiler's clean verdict are simulated. Files and git snapshots are real.

The reduced cases need only two timeout errors, or the shipped entry plus an unused component.
A repeated HTTP 500 and a missing-model error also produced the Recall offer. No old
conversation state was needed. This isolates error classification from stale state.

After the changes, the two new files and four existing feedback/refusal test files
report **74 passed in 10.76 seconds**. The existing named-guardrail recovery cases still pass.
Ruff and `git diff --check` pass. The full suite was not run; no commit or landing is claimed.

The original saved events were also replayed through `recall.offer`: neither timeout
now produces an offer. The entry from `2bf8f6d`, paired with its recorded clean compiler
verdict, now produces `SAGE001` instead of a successful feedback result.

The current live preview was inspected through the signed-in browser. It shows 16 table
rows, VLTA selected, two charts with nonzero width and height, and a five-stock watchlist.
Clicking OVLD changes the detail heading. Clicking VLTA restores it. This is the later
app version; it is not evidence that the local Sage fixes are deployed.

## Changes and scoped review

- `recall.py`: only a named guardrail reason enters the Recall recovery ladder.
  Filtering on read also covers old timeout records. Error text remains available.
- `service.py`: only guardrail refusals use the shortened repeated-refusal sentence.
  Repeated timeouts keep their actual error message and the approved plan's retry advice.
- `feedback/runner.py`: the shipped starter `main` in `src/App.tsx` produces repair
  feedback even when TypeScript passes. The existing bounded repair loop handles it.
- Two regression files cover the reproduced failures and successful entry repair.

The review covered these five changed code/test paths. The starter check is deliberately
narrow: it identifies the shipped `sage-placeholder` markup. It is not a React parser,
a visual acceptance test, or a check of every requested feature. A different incomplete
screen can still pass. No general requirement-completion claim is made.

## Still unconfirmed

- Why the original patch did not replace the entry. The saved tool events omit patch
  arguments and results. The model's claim about `Move to` needs those records to verify.
- Why the upstream model call timed out. The gateway/provider request logs around
  16:51 and 16:55 UTC are needed. A successful retry after clearing Recall does not prove
  that content triggered a policy.
- Whether the three full source files contain PII. They are mounted Dataset files and
  are not in git. No PII finding appears in the two errors under investigation.
- Full dashboard acceptance, including data accuracy and the earnings-date chart marker.

No gateway policy, Recall, production source, or deployment was changed in this investigation.
