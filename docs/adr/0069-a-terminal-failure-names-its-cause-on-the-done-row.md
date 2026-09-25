---
status: accepted
extends: ADR-0049 (an effort follows the model that runs), ADR-0066 (OpenCode owns tool lifecycle
  and native reasoning state)
---

# A terminal failure names its cause on the `done` row

Settled 2026-09-25 for the children of #558, before any of them started. Five tickets touch the
same records: #559 and #561 publish what a failed turn was and whether Sage already retried it,
#563 reads that to offer **Continue with another model**, and #560 and #561 both add to the Build
diagnostics record. Without one shape agreed first, each worker would have invented its own field
and E would have had to read three.

## The records this extends

A live event and its saved history row are the same dict: on both halves `persist(ev)` appends
the row and then yields it. Build stamping adds `app`, `at`, `conversation` and, only while a
diagnostics capture is active, `turnId`. Chat stamping adds `at` alone, so a Chat row carried no
turn id at all. Every terminal path on both halves emits one `done` row; some emit no `error` row.
The Build diagnostics record (`build_diagnostics.snapshot`, schema 1) is Build-only, lives on the
app path, and already holds per-call no-action facts and a one-slot `planningRecovery` decision.

## The contract

### On the `done` row, both halves

| Field | Type | Values | Written by | Read by |
|---|---|---|---|---|
| `turnId` | string | the turn ticket id | every `done` writer, always (A on Chat and Build; C on the plan path) | E, F |
| `cause` | string, closed | `invalid_tool_call` \| `model_no_action` | A for `invalid_tool_call`; C for `model_no_action` | E, F |
| `stage` | string, closed | `chat` \| `planning` \| `implementation` | whoever writes `cause` | E |
| `recoveries` | integer | `0` or `1` under today's allowances | A on Chat and Build; C on the plan path | E, F |

**`turnId` is on every `done` row**, ok or not, so a reader never has to know whether a capture
was active. It replaces the config-dependent absence Build had.

**`cause` is the eligibility promise.** It is written only when the turn is terminal AND the old
session is confirmed idle. A wedged session, an unconfirmed Stop, a user Stop, a provider terminal
error, a permission refusal, or an unrelated preview error never writes `cause`. "cause present"
therefore means "E may offer the action"; there is no second flag that could disagree with it.
Absent `cause` on a failed `done` means not eligible. The test that guards this promise plants a
refused stop and asserts that `cause` is absent.

**`stage`** says which resume path E takes: `chat` resolves the original request from the failed
turn's saved user row and `pendingTask`; `planning` replays the canonical plan request; and
`implementation` resumes the approved saved plan through the existing implementation path. It is
written beside `cause`, on failed rows only. It retires `_failed_plan_request`'s guess, which
today infers "plan turn" from a decision plus an error message that starts "Planning stopped".

**`recoveries`** counts the Sage corrections spent on this turn under its existing allowance:
Chat's shared answer/artifact/table allowance, Build's broken-call allowance, or the
`PlanRecoveryBudget`. It is written on a successful `done` too when it is above zero, so an
"invalid then valid, one correction" turn can be told from a clean one. OpenCode's own internal
repairs are not counted.

**`decision`** is unchanged. Its spellings (`"broken tool call"`, `"model_no_action_timeout"`,
`"empty answer"`, ...) are what the Workbench store keys its lookup tables on, and rewriting them
is not backward compatible.

### In the Build diagnostics record

| Field | Type | Written by | Read by |
|---|---|---|---|
| `planningRecovery` | the existing one-slot `{attempt, trigger, action}` | C (unchanged) | old readers |
| `planningRecoveries` | list of `{attempt, trigger, action}`, same vocabulary, capped at the budget plus one | C | F |
| per-call tool-argument boundary facts | B's section, named by B | B | F |

`planningRecoveries` exists because the slot is overwritten on the second `choose()`: a no-action
recover followed by an invalid-plan stop keeps only the stop. The slot stays for readers that
already have it.

C lands its diagnostics edit before B. C's is one reader and one list; B's is a new section with
fixtures, and the large one merges onto the settled file.

**Chat gets no diagnostics capture.** A's Chat facts live on the `done` row only. The diagnostics
store is named for Build, is keyed by app, and a Chat turn has no app to store under.

### Vocabulary shared across the tickets

Keys are camelCase. Closed values are snake_case. `invalid_tool_call` and `model_no_action` are
the same spellings the record already uses for `preEditGuard.trigger` and
`planningRecovery.trigger`. `stage` reuses the record's `PHASES` and `errorStage` values.
**Attempt** enters `CONTEXT.md`: one send of a turn's request into a session, `initial` or
`recovery`; the integer on `build-recovery` is a count of recoveries, not an Attempt.

## Considered and rejected

**Put the fields on the `error` row.** Rejected. Not every terminal path emits one, and
`_failed_plan_request` already walks history by `done`.

**Rewrite `decision` to a closed vocabulary.** Rejected. Four Workbench lookup tables key on the
current spellings, and the change is not backward compatible.

**A `recoverable` boolean beside `cause`.** Rejected. Two fields that can disagree are the
two-reads defect this repo keeps recording; the writer's discipline is the flag.

**Copy C's per-attempt detail (effort, route, first-action time) onto the history row.**
Rejected. It already exists per call in the diagnostics record, and a transcript row that carries
timings is the generic recovery framework the parent forbids.

**Reuse the process-local `ContextContinuation` registry as E's only action identity.**
Rejected. It dies on restart, and #563 must show why an action is unavailable after one rather
than pretend the token survived. E may still use a claim mechanism for duplicate and stale clicks;
the durable identity is `turnId` plus conversation and app on the saved row.

**Persist a recovery row per retry on Chat.** Rejected. Chat has never shown one, and it is on
no child's list. The count on `done` is what E and F need.

**Extend `build_diagnostics.begin` to Chat turns.** Rejected. A new capture surface, outside
every child's ownership table, with no app path to store under.
