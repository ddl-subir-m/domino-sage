# 2026-09-26: #558 model-reliability acceptance (#564), offline gate on the composed tree

Status of the acceptance ticket #564 after waves 1 to 3 of #558 landed on `main`. Every paid
journey and the real-Chromium pass are recorded as **not run**; the offline gate is recorded with
its numbers. An unrun step recorded as unrun is the point of this file.

## Tree under test

- Composed tree: `main` at `a7eb5dec2f472ff520656b43e31bfc810cb83237` (first-parent merges `8acd5ed3` #565, `60876310` #561,
  `bbbb11ee` #560, `66de9228` #567, `e136385d` #566, `2595c4ba` #569, `51429cf0` #570, then `a7eb5dec` a test-census fix for #570's card), tree
  `df3fb37cc9baf8d28de0af74d08530e33c86d650`. Every merge `--no-ff`; each equal to `git merge-tree --write-tree <main> <branch>`.
- Root dependencies linked into the throwaway worktree (absolute targets): `node_modules`,
  `template/react-vite/node_modules`, `backend/.env`. OpenCode pinned 1.18.4.
- Source hash (`backend/sage backend/tests template docs scripts`, py/js/mjs/ts/tsx/md/json)
  before and after the gate: `19f038d633e5f9c9794bff1b1ea18496f4bcd6082e6fd7016b0b92e83030c029`.

## Offline integration gate

| Item | Status | Evidence |
|---|---|---|
| Compose all children + current main, real root dependencies | done | tree identity above; `-rs` printed; no `BINARY.exists()` skip |
| Mixed malformed-call / answer / artifact / plan failures, shared budgets, duplicate events, Stop / refused stop | passed (deterministic) | `test_a_broken_tool_call_ends_the_build_out_loud.py`, `test_a_broken_tool_call_ends_the_chat_turn_out_loud.py`, `test_a_no_action_plan_retry_is_task_focused.py` |
| Switching model after exhaustion | passed (deterministic, scripted model) | `test_continue_with_another_model_resumes_a_failed_turn.py`, `test_the_failure_card_offers_another_model.py` |
| B's real-OpenCode fragmented / interleaved streams, sanitisation, cancellation | passed, ran (not skipped) | `test_fragmented_tool_arguments_reach_real_opencode.py` (8 collected / 8 ran) |
| Source clarification / offer / accept / Close, both stacks, #555 / #556 regressions | passed | `test_a_known_source_request_is_a_short_request.py`, `test_source_clarification_keeps_the_question.py`, `test_a_plan_document_captions_without_naming.py`, `test_a_refused_platform_read_reaches_the_turn.py` |
| E in a real browser | **not run** | route checks with scripted models only; see below |
| Full suite, marker ordering, process audit, collected count, skip reasons, tree identity, `make lint` | done | 9382 passed / 5 skipped / 9387 collected, exit 0, 15:47 wall, on tree `df3fb37c`; reconciled 9382 + 5 = 9387 = `--collect-only`. A first run on the pre-fix tree `ca40c850` gave 1 failed / 9381 passed / 5 skipped of 9387: a deterministic test-census pin that #570's card moved from eleven to twelve, fixed in `a7eb5dec` and re-run in full |

Full suite: `uv run --extra dev pytest -q -rs --durations=25` from `backend/`: 9382 passed / 5 skipped / 9387 collected, exit 0, 15:47 wall, on tree `df3fb37c`; reconciled 9382 + 5 = 9387 = `--collect-only`. A first run on the pre-fix tree `ca40c850` gave 1 failed / 9381 passed / 5 skipped of 9387: a deterministic test-census pin that #570's card moved from eleven to twelve, fixed in `a7eb5dec` and re-run in full.
Skips: CI-only ×2 (#166), Playwright ×2 (`SAGE_APP_BROWSER_MODULE` unset), ripgrep ×1. `make lint`
on the landed tree: `All checks passed!`. Slot claimed and released on #558 by `LANDING:` markers;
machine sweep by structure was empty before the run.

## Bounded live replay plan

**Not run.** No paid budget was recorded by the owner in this session, and the ticket forbids
guessing one. Nothing below was submitted to a paid model.

| Journey | Model | Status | Reason |
|---|---|---|---|
| ARM Chat journey (fuse SFDC cases + Gong transcripts; attach; accept; duplicate accept) | Xiaomi `mimo-v2.6-pro` | not run | no recorded budget |
| ARM Chat journey | Haiku | not run | no recorded budget |
| ARM Chat journey | control model | not run | no recorded budget |
| Build journey, `react-vite`, Sample Metrics + footer edit | Xiaomi / Haiku / control | not run | no recorded budget |
| Build journey, `fastapi-antd`, Sample Metrics + footer edit | Xiaomi / Haiku / control | not run | no recorded budget |
| Switch / resume, one Chat + one Build live continuation | any | not run | no recorded budget |
| C's concise retry vs same-input baseline (≥3 matched pairs) | any | not run | no recorded budget |

The deterministic equivalents of the switch/resume trigger (injected `invalid_tool_call` and
`model_no_action` failures, then the Continue action on Chat, on an approved `react-vite` build
and on an approved `fastapi-antd` build) pass with the scripted model. They prove the route and
the resume paths, not a real second model.

## Real-browser pass (#570)

**Not run.** Playwright is not installed in the root `node_modules`, and this session may not boot
Sage (the root checkout is not on `main`, and a worktree boot overwrites the shared OpenCode
config). The Workbench card was checked through its JS harness and through the route under
`TestClient` with FakeOpenCode. The owner's click path, from #570's report:

1. Boot Sage from the repo root, open the Workbench, open Chat in a Conversation.
2. Make a turn fail on `invalid_tool_call` twice (or use a saved Thread whose last `done` row carries `cause`). Expect, under the red status line: "This turn ended on tool calls that could not be read." then "Another model can pick the question up from here." and the button **Continue with another model**.
3. Click it. Expect a Model select (barred/not-serving rows closed, not hidden), a Reasoning effort select when the alias has levels, the line "Will run <alias> with reasoning effort <level>.", the scope line, and **Continue** (disabled until a model is picked) beside **Cancel**.
4. Cancel: the picker closes, the Network panel shows no POST. Open again, pick a model and effort, Continue: expect `POST /api/project/turn/continue` with `{turnId, conversation, app:"", model, effort}` and no `prompt`; a `Continue with another model.` bubble; the answer streams; the composer's model chip names the picked model afterwards.
5. Reload the page before clicking: expect one `GET /api/project/turn/continue?...` and the button back. Navigate to another Conversation and back: the button is still there.
6. Stale action: run any new turn in the Conversation from another tab, then click Continue in the first tab: expect the card to read "A newer turn ran in this conversation after this one." with no button. Denied save: pick a model, then revoke it (or use a name the gateway does not list via devtools) and click: expect "That model is not one you can run right now." and no new turn. Request error: stop the backend and click: expect "The request did not go through. ..." and the button still there.
7. Build: approve a plan, make the build fail on `invalid_tool_call` twice, then the same card under "Stopped — broken tool call" with "Another model can carry on building the approved plan from where this build stopped."; Continue resumes on the implementation path (no new plan card) and the Build picker's chip names the picked model. Repeat on a `fastapi-antd` app.
8. Restart Sage with a Conversation whose failed turn's `done` row was never written (kill it mid-turn): the card must say "This turn is not in this conversation's record."

## What the remaining limits are

- Xiaomi ARM malformed-call origin: unknown. #560's boundary evidence records where an argument
  crossed the relay; it does not say which side first damaged it. The failed trials retained no
  deltas. Stays open on #557 and #558.
- Xiaomi Build completion: not demonstrated. The 300 s runner cap and the production 120 s
  no-action timeout are different controls; neither was raised.
- Legacy raw-string invalid shape on Chat: not recovered (execution uncertain); the pre-existing
  empty-answer repair fires instead.
- Chat->Build handoff helper plan path writes no `done` row, so it can never be eligible for
  Continue; #569 answers `not_found` there.
- #568 stays `later`: #566 measured no safe deterministic phase boundary for investigation
  discovery. Closing it is the owner's call.

## Cleanup

Throwaway landing worktrees removed after each wave. Slice worktrees `.claude/worktrees/{565,560,561,567,566,569,570}-slice`
left on disk for the owner. No OpenCode host, turn lock or browser left running (machine sweep empty).
