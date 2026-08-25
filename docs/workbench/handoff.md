# Chat → Build handoff

Executable spec. Companion: [chat.md](chat.md). Locked decisions: [ADR-0003](../adr/0003-workbench-chat-and-untitled.md).

**Done when** a Thread that has started to look like an app is offered a plan once, the user can confirm Open in Build, and Build then runs the existing `sage-plan` / `sage-implement` loop against files on disk — not against in-memory Chat state.

## 1. What crosses, and what does not

Handoff is a write to the project filesystem, then a mode switch. The payload is files:

| Always | Path | Source |
|--------|------|--------|
| Plan | `.sage/plan.md` | `sage-plan` on confirm, or the plan already drafted in this Thread |
| Digest | `.sage/handoff.md` | Short summary Sage writes at confirm; not the transcript |
| Artifacts the user kept | `examples/<threadId>/` (already there) | Chat. Listed in `.sage/threads/<id>/artifacts.json` |
| Session context that names a Resource | new rows in `.sage/bindings.json` | Chat `context.json` → Bindings |

| Default off | Path | Why |
|-------------|------|-----|
| Transcript | `.sage/handoff-transcript.md` | Half a Thread is dead ends. Off unless the user checks it on the sheet. |

Do not copy OpenCode session state. Build uses the project's existing Build session (`.sage/session.json`), which is a **new** OpenCode session if this project has never built. Continuity for the human is the Thread still listed in the rail and reachable from `#/chat/<threadId>` — not one harness session spanning both modes.

Do not silently teleport. Detect, suggest, confirm.

## 2. Detect once

After each `sage-chat` turn, if this Thread's `handoff.json` has no `suggestedAt` and no `suppressed`, run a classifier.

Reuse the shape of [backend/sage/orchestrator/scope.py](../../backend/sage/orchestrator/scope.py): one bounded gateway call, no tools, fail open (no suggestion) on timeout or error, fail safe (suggest) on an unreadable answer, breaker after three broken replies. Different question, different bias:

- **Question:** is the user now asking for a lasting UI that other people would open — more than one view, something that should keep working tomorrow — rather than a one-off answer?
- **Bias to NO.** A wrong suggestion every three messages is the failure the mock called intolerable. A missed suggestion still leaves **Open in Build** in the Thread menu.
- **Timeout:** 8s. **Max prompt:** last user message + last assistant message + the Thread title, not the whole history.
- **Output:** one word, `APP` or `CHAT`. Only `APP` counts as a hit.

On `APP`, write:

```json
{
  "suggestedAt": "2026-08-25T18:20:00Z",
  "suppressed": false,
  "status": "suggested"
}
```

Never suggest again for this Thread. `Not now` sets `suppressed: true` and `status: "suppressed"`. The overflow **Open in Build** / **Turn into an app** still works.

Do not run the classifier when the user message is already an explicit build request ("build me a dashboard", "open this in the builder"). Treat that as confirm-intent and go to the sheet (§4) directly.

## 3. Suggest

The Chat pane shows the prototype callout once, inline after the assistant turn that triggered it:

```
This is starting to look like an app.
I can write a plan so you can review it and build from it.
[Write a plan]   [Not now]
```

**Write a plan** runs `sage-plan` in the **Build** OpenCode session against a prompt assembled from:

- `.sage/handoff.md` draft (orchestrator writes a one-paragraph digest of the Thread before this call: what was asked, which files/Resources were in context, which Artifacts exist)
- the Artifact manifest (paths + titles, not the PNG bytes)
- Session context names

The plan body is the existing `sage-plan` shape (one sentence, `## Plan`, `## Open questions`) and is written to `.sage/plan.md` the way Build already does. Then open the sheet with the plan included.

**Not now** suppresses. **Open in Build** on a Thread that has no plan yet is the same as Write a plan, then the sheet.

The callout is not a plan card. The plan card is Build's existing approval UI, shown after the user lands in Build.

## 4. The sheet (confirm)

Modal. Title: **Build from this plan**. Primary: **Open Builder**.

Checkboxes, matching the prototype:

| Row | Default | Locked |
|-----|---------|--------|
| The plan (title + version) | on | yes — handoff without a plan is not this flow |
| What is in this conversation → becomes what the app needs | on | no |
| Artifacts → stay in `examples/` | on | no — unchecking does not delete files; it omits them from `.sage/handoff.md` so implement does not treat them as required examples |
| Transcript → `.sage/handoff-transcript.md` | off | no |

If the current project is still Untitled, the sheet says the app will live in this project and the display name will become the plan title (slugified). Confirm does that rename (`untitled: false`). Do not block on "save to a project" — Untitled already is a project (ADR-0003).

On confirm, in order:

1. Write `.sage/handoff.md` (digest + list of Artifact paths that stayed included + list of context names).
2. Optionally write `.sage/handoff-transcript.md`.
3. For each Session context row with `kind` in `data_source | model_api | llm_alias` that is still included, upsert a Binding via `Workspace.update_bindings` (existing path). Files in context that are already Attachments stay Attachments.
4. Set `handoff.json` `status: "bound"`, `boundAt`, `planPath: ".sage/plan.md"`.
5. Route to `#/build/<threadId>`. The Build pane is the existing builder: plan approval card if `has_built` is false (it will be), preview on the right, resource panel showing the new Bindings as IN THIS APP.

The Thread id stays in the URL so Chat ↔ Build is turning your head, not starting over. Build's composer is the existing one; its history is the Build `history.jsonl`, which at this moment is empty except what the plan-approval card needs. The origin Thread is one click away in the rail, tagged with the app once `has_built` is true.

## 5. Build after landing

Do not invent a builder agent. Map onto what exists:

| Step | Agent | Already |
|------|-------|---------|
| Draft / show plan | `sage-plan` | yes, plan gate writes `.sage/plan.md` |
| User approves | UI | existing Approve & build |
| Implement | `sage-implement` | yes, reads `.sage/plan.md`, edits `src/`, typecheck loop |

Extra prompt context on the first implement turn (and on the plan turn if plan was not drafted yet): the contents of `.sage/handoff.md` and a directory listing of `examples/<threadId>/`. Implement is still bound by `template/react-vite/AGENTS.md` — it builds the app in `src/`, and may copy an example into the app if the plan says so. It must not treat Chat history as a spec; `AGENTS.md` already says `.sage/history.md` is a past record and `.sage/plan.md` is live intent. Add one line to the implement prompt (only for a turn that has `handoff.md`): "A Chat Thread produced the files under `examples/` and the digest in `.sage/handoff.md`. The plan is what to build. The digest is background."

Phased builds (`plan_steps.py`) stay as they are. First-build gate stays as it is. Sensitivity lock stays as it is: if a Binding recorded `sensitive: true` (or samples were marked sensitive), ModelControl already locks.

## 6. `handoff.json` states

```
absent → suggested → planned → bound
                 ↘ suppressed
```

| status | meaning |
|--------|---------|
| `suggested` | callout visible; classifier will not run again |
| `suppressed` | callout gone; Open in Build still available |
| `planned` | `.sage/plan.md` written from this Thread; sheet not yet confirmed |
| `bound` | sheet confirmed; Bindings upserted; user is in Build |

There is no `status: built`. `Workspace.has_built()` remains the source of truth that code exists.

## 7. Reverse direction

Out of scope. "Ask about this app" from Build can wait. A user who wants Chat after a build opens the origin Thread or hits New conversation in the same project.

## 8. Acceptance

1. Three analysis turns that never mention an app produce **no** callout. A fourth that says "put this on a dashboard colleagues can open" produces exactly one callout. Reload does not show it again as a new suggestion (`suggestedAt` is set).
2. **Not now** hides the callout permanently for that Thread. Overflow **Open in Build** still opens the sheet (and drafts a plan if none exists).
3. Confirm with transcript unchecked writes `plan.md` and `handoff.md`, does not write `handoff-transcript.md`, upserts Bindings for Data Sources that were chips, leaves `src/` still untouched until Approve & build.
4. After confirm, `#/build/<threadId>` shows the existing plan approval card, the preview pane, and IN THIS APP containing those Bindings. The Chat rail still has the Thread. Switching to Chat shows the same Thread, not a blank greeting.
5. Approve & build on that card runs `sage-implement`, which reads `plan.md`, sees `handoff.md`, and edits `src/`. Typecheck loop runs. Chat's `examples/` files are still there.
6. Untitled project is renamed to the plan title on confirm; `untitled` is false; same Domino project id.
7. Classifier timeout or 5xx → no callout, turn otherwise succeeds. Three consecutive garbage verdicts trip a breaker and Chat stops calling it for this process (same pattern as `scope.py`).
8. Tests: classifier bias (APP only on app-shaped last turn); sheet confirm file set; Bindings upsert from `context.json`; Thread URL survives the mode switch; `sage-chat` history.jsonl is not the file implement greps as `.sage/history.md`.
