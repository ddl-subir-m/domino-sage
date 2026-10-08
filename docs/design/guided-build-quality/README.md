# Guided build quality: implementation handoff

Date: 2026-10-07. Baseline and evaluation control: `main` at
`f024b9a6aaa001336b6e4dee1b9c30ea7cdfdb61`, which already contains #680, #689-#692, #694 and #695.
Every comparison uses this revision as its control; there is no older baseline to compare against.
Status: accepted for implementation. Each slice is a ticket under the spec issue.

## Changes from the initial handoff

- #691 already accepts cross-file plain-script globals in FastAPI lint checks. Preserve it when
  adding screen files; it does not prove that the browser loaded a script in the correct order.
- #690 already refreshes the Build turn's own app, including an unselected app. Reuse that path
  for helper updates. Installing an absent helper still needs the separate opt-in path.
- #680 adds a checked `ALREADY_DONE` outcome and turn-tagged snapshots. Preserve that outcome
  and reuse its app-file ownership rules. The no-edit recovery packet now teaches the marker
  to older apps too. It is not proof of business correctness.
- #692 matches page acknowledgements and runtime faults to the validation's own app and preview
  generation. Preserve this when saving check evidence or adding interaction checks.
- #689 stops tests from installing packages into the repository's real template. Prepare required
  test dependencies explicitly and report skips. Tests must not install them as a side effect.

The release order and the decisions below remain unchanged.

## Objective

Improve the correctness and maintainability of apps that Sage builds. Reduce repeated code reads,
incorrect edits, and repair turns. Keep the time and cost of an ordinary Guided build controlled.

Guided is the product path. Direct was an experiment. This work does not make Direct the default,
add another harness, or replace OpenCode.

This package contains implementation decisions made to complete the handoff. Proposed API names,
limits, and test thresholds below are design choices, not claims about existing code or measured
performance. Keep them fixed during the first comparison; revise them from evidence.

## Read and implement in this order

| Work | Design and completion criteria |
|---|---|
| App structure, coding rules, queries, and shareable filters | [App design](app-design.md) |
| Durable build evidence and code navigation | [Context design](context-design.md) |
| Implementation slices, testing, rollout, and deferred experiments | [Delivery and experiments](delivery-and-experiments.md) |

The delivery document is the work sequence. Read the relevant design before each slice. Complete
one slice and its checks before starting the next. The slices are implementation units, not new
GitHub issues. Follow the repository's issue rules if the work is later assigned to tickets.

## Decisions

| Area | Decision |
|---|---|
| Normal execution | One approved plan, one continuing coding session by default. |
| New app structure | The entry file is the shell. Each screen has its own file from the first build. |
| Architecture | Short rules and executable examples. Add components and layers only when needed. |
| Queries | Reuse the existing query hook. Delay remote text search; page details; aggregate summaries in SQL. "All" is the `__all__` sentinel that `bound_schema.py` already teaches. |
| Context | Keep canonical intent authoritative. Save identity, digests, check results and changed paths for recovery; nothing else in v1. |
| View URLs | Save only explicitly shareable, applied view state. Use the app's hash, not Workbench routing. |
| Code map | v1 extracts the existing source note into a module and adds script order, literal `window.app` names, Python routes and query names. A JS/TS compiler parser waits for the evaluation. |
| Steering | A later capability probe. Keep queue behavior until the pinned harness can safely accept a correction. |
| Browser tests | An opt-in experiment after final code checks, with a time budget and honest verification states. |
| Phased builds | Remain off by default. Test value before exposing a control or adding automatic selection. |
| Model calls | No new classifier, summarizer, or architecture-review call on the default build path. |

## What exists today

These observations refer to the baseline above. Locate symbols again after merging current main.
`SAGE001` today reads only `static/app.js` (`feedback/runner.py`); moving the placeholder without
extending it lets an untouched starter pass. `SAGE002` proves a script tag exists, not its order.

| Existing component | Role and implementation seam |
|---|---|
| `template/fastapi-antd/AGENTS.md`, `static/app.js`, `static/index.html` | The UI starts in `app.js`; components use `window.app` and ordered script tags. |
| `template/react-vite/AGENTS.md`, `src/App.tsx` | Typed React stack with imports. Keep its stack conventions. |
| `backend/sage/resources/bound_schema.py` | Generates the data/query instructions supplied for bound sources. |
| `static/sage/appQuery.js` and `src/appQuery.ts` in the two templates | `runQuery` and `useQuery` already provide named queries, query state, page-lifetime caching, refresh, and request cancellation. |
| `backend/sage/feedback/runner.py` | Code checks, placeholder detection (`SAGE001`), missing loaded-script detection (`SAGE002`), and cross-file plain-script globals. |
| `backend/sage/workspace/stack.py`, `manager.py` | Template seeding, stack metadata, and refresh of Sage-owned files. |
| `Orchestrator._prepare_turn_app`, `WorkspaceManager.for_app` | Refresh the app pinned to the Build turn without changing the selected app. |
| `Orchestrator._previous_turn_did_it`, `TurnSnapshot.turn_changed_app`, `Workspace.sage_owned_paths` | Evidence for an unchanged turn's `ALREADY_DONE` claim; exclude Sage-owned file updates from prior model progress. |
| `Orchestrator._pre_edit_recovery_packet` | Teaches `ALREADY_DONE` at no-edit recovery, including apps with older instructions. |
| `Orchestrator._active_validation`, `_await_runtime_error` | Match page reports and runtime faults to the validation's own app preview, independent of the selected app. |
| `backend/sage/build_intent.py` | Canonical per-turn intent, approved-plan precedence, and protocol-specific request installation. |
| `backend/sage/context_rollover.py` | Existing context limit and continuation state machines. A continuation token is process-local authority. |
| `Orchestrator._context_rollover_packet` in `backend/sage/orchestrator/service.py` | Reconstructs recovery orientation from canonical intent and current disk. |
| `Orchestrator._source_paths`, `_top_level_names`, `_build_source_note` | Existing stack-aware map of up to 60 source paths plus bounded names, supplied at turn entry and recovery. Extend this feature rather than creating a duplicate map. |
| `Orchestrator._approve_locked`, `_phased_approve`, `_run_step` | Normal approval reuses the planning session. Phased approval creates sequential fresh sessions. |
| `backend/sage/build_diagnostics.py`, `tool_timing.py`, `scripts/build-scorecard.py` | Bounded diagnostic capture and existing comparison support. |

Sage already has skills, MCP extensions, chat findings, context compression, checks, and repair
loops. Reuse these. The source-file/name map was found during the detailed handoff inspection;
it corrects the earlier conversation's incomplete assessment of code navigation. Sage has a
basic map today, but the inspected map is not a parsed dependency graph. A source graph is not
required to introduce good app structure.

## Product flow

```text
Request and selected resources
    -> Guided plan and necessary questions
    -> user approval
    -> implementation in the existing session
    -> code checks and bounded repair
    -> existing preview and data-access observations
    -> saved app, with verified and unverified outcomes stated separately
```

The builder can retrieve a compact code map during implementation. If it must recover a session,
it receives the same canonical intent plus current, checked progress evidence. Neither operation
adds a user approval. Neither grants access to a new resource.

## Scope and compatibility

- Apply the structure change first to new FastAPI apps. Then implement the equivalent React layout.
- Existing app code remains intact. Extract a screen only when a requested change needs that screen.
- Preserve the LLM Gateway, disclosure rules, resource bindings, file ownership, and turn locks.
- A model-written note is an observation, not a new requirement or permission.
- A source map describes current code. It does not prove the code ran or fulfilled the plan.
- Keep `done.ok` distinct from verification success. See
  [Build verification](../../workbench/build-verification.md).
- Do not enable phased execution as a side effect of the context work.
- Do not silently rewrite old apps' full `AGENTS.md` files. Follow the migration rules in app design.

## Sources and reasoning

The design follows the inspected Sage code and the decisions in this conversation. External
examples support mechanisms, not claims of a performance gain for Sage:

- [Aider repository map](https://aider.chat/docs/repomap.html): compact symbols and relevant code relationships.
- [Cursor harness development](https://cursor.com/blog/continually-improving-agent-harness): context retrieved during work and measured changes.
- [Cursor browser tools](https://cursor.com/docs/agent/tools/browser): interaction checks beyond page startup.
- [Claude Code context management](https://code.claude.com/docs/en/how-claude-code-works): context and tool use in a continuing agent loop.
- [Codex App Server](https://learn.chatgpt.com/docs/app-server): explicit turn lifecycle and steering. This is a reference, not an API that Sage currently exposes.

## Start here

Capture the current baseline, then implement slice A in the delivery document: seed a real screen
boundary and adapt the checks. Do not start with a source-graph service or a phased-build classifier.
