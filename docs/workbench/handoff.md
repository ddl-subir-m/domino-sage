# Chat → Build handoff

Executable spec. Companion: [chat.md](chat.md). Locked decisions: [ADR-0003](../adr/0003-workbench-chat-and-untitled.md).

**Done when** a Conversation that has started to look like an app is offered a plan once, the user can confirm Open in Build, and Build then runs the existing `sage-plan` / `sage-implement` loop against files on disk — not against in-memory Chat state.

## 1. What crosses, and what does not

Handoff is a write to the project filesystem, then a mode switch. The payload is files:

| Always | Path | Source |
|--------|------|--------|
| Plan | `apps/<appId>/.sage/plan.md` | `sage-plan` on confirm, or the plan already drafted in this Conversation |
| Plan document | `.sage/plan-docs/<id>/` | Written alongside the plan above; durable, and it survives the build ([ADR-0007](../adr/0007-the-plan-document-is-durable-the-handoff-is-not.md)) |
| Digest | `apps/<appId>/.sage/handoff.md` | Short summary Sage writes at confirm; not the transcript |
| Artifacts the user kept | `examples/<threadId>/` (already there) | Chat. Listed in `.sage/threads/<id>/artifacts.json` |
| Session context that names a Resource | new rows in `apps/<appId>/.sage/bindings.json` | Chat `context.json` → Bindings |

| Default off | Path | Why |
|-------------|------|-----|
| Transcript | `apps/<appId>/.sage/handoff-transcript.md` | Half a Conversation is dead ends. Off unless the viewer turns it on in Account settings (§4). |

Do not copy OpenCode session state. Build opens **this Conversation's own** Build session (`.sage/threads/<threadId>/build-session.json`), which is a **new** OpenCode session the first time this Conversation builds. Continuity for the human is the Conversation still listed in the rail and reachable from `#/chat/<threadId>` — not one harness session spanning both modes.

> Revised. Build used to share one session and one transcript per project (`.sage/session.json`). Both are now per conversation, matching Chat, so **New conversation** in Build means something. See [ADR-0005](../adr/0005-build-is-per-conversation.md), and [ADR-0009](../adr/0009-one-conversation-build-is-a-view.md) for why matching Chat was not enough on its own.

A handoff needs a **target**: a new Built App, or one the Project already has. `<appId>` above is
whichever the person picked on the sheet (§4). A Project holds many Built Apps, so the target is
never assumed — see [ADR-0008](../adr/0008-a-project-holds-many-built-apps.md). `plan-docs/` stays
at the Project root: the plan is drafted before the app exists, and gains an `appId` when it binds.

Do not silently teleport. Detect, suggest, confirm.

## 2. Detect once

After each `sage-chat` turn, if this Conversation's `handoff.json` has no unresolved entry and no `suppressed`, run a classifier. A Conversation that already produced a Built App is eligible again — it may produce another (ADR-0008).

Reuse the shape of [backend/sage/orchestrator/scope.py](../../backend/sage/orchestrator/scope.py): one bounded gateway call, no tools, fail open (no suggestion) on timeout or error, fail safe (suggest) on an unreadable answer, breaker after three broken replies. Different question, different bias:

- **Question:** is the user now asking for a lasting UI that other people would open — more than one view, something that should keep working tomorrow — rather than a one-off answer?
- **Bias to NO.** A wrong suggestion every three messages is the failure the mock called intolerable. A missed suggestion still leaves **Open in Build** in the Conversation menu.
- **Timeout:** 8s. **Max prompt:** last user message + last assistant message + the Conversation title, not the whole history.
- **Output:** one word, `APP` or `CHAT`. Only `APP` counts as a hit.

On `APP`, write:

```json
{
  "suggestedAt": "2026-08-25T18:20:00Z",
  "suppressed": false,
  "status": "suggested"
}
```

Never suggest again *while this entry is unresolved*. Once it reaches `bound`, the Conversation is eligible for a fresh suggestion about a different app. `Not now` sets `suppressed: true` and `status: "suppressed"`, and that one is permanent — it is the person saying stop. The overflow **Open in Build** / **Turn into an app** still works.

Do not run the classifier when the user message is already an explicit build request ("build me a dashboard", "open this in the builder"). Treat that as confirm-intent and go to the sheet (§4) directly.

## 3. Suggest

The Chat pane shows the prototype callout once, inline after the assistant turn that triggered it:

```
This is starting to look like an app.
I can write a plan so you can review it and build from it.
[Write a plan]   [Not now]
```

**Write a plan** runs `sage-plan` in the **Build** OpenCode session against a prompt assembled from:

- `.sage/handoff.md` draft (orchestrator writes a one-paragraph digest of the Conversation before this call: what was asked, which files/Resources were in context, which Artifacts exist)
- the Artifact manifest (paths + titles, not the PNG bytes)
- Session context names

The plan body is the existing `sage-plan` shape and is written to `.sage/plan.md` the way Build already does: one sentence, then the brief sections a colleague reads (`## Problem & outcome`, `## Who uses this`, `## What it does`, `## Screens`, `## Not doing`, `## Done when`), then `## Plan` and `## Open questions`. A plan document is created from the same text, carrying this Conversation as its origin so the plan page can offer the way back to the conversation. Then open the sheet with the plan included.

**Not now** suppresses. **Open in Build** on a Conversation that has no plan yet is the same as Write a plan, then the sheet.

The plan document carries the Conversation that produced it. So does a plan drafted by the gate inside Build, so a plan behaves the same however it started ([ADR-0009](../adr/0009-one-conversation-build-is-a-view.md)).

The callout is not a plan card. The plan card is Build's existing approval UI, and it is the receipt: it names what crossed, and carries the controls to change that or to undo the handoff. It is a block in the Conversation's transcript, so where it is read depends on the arm — in Build in either arm, and in Chat as well once the transcript is merged (ADR-0009). Undo cancels the plan through the existing cancel path and archives it; the plan document, the digest, the Bindings and the Artifacts under `examples/` are all left alone. Undo means "I am not building this", not "erase what happened".

The receipt rides on the `plan-proposed` row the confirm writes, as `crossed` — which Built App the plan went into, whether that app was new or one the Project already had, and the charts, context names and file paths that went with it. One row, because one handoff has one card: a receipt written as a block of its own would be a second card for the same crossing. Collapsed it says how much crossed; expanded it names the charts, the context and the files, because everything that crosses is written to the Project as a real file precisely so it can be inspected (§1).

**Change** redoes the crossing with different answers and offers to keep them as the viewer's preference. It POSTs `/api/threads/{id}/handoff/recross`, which rewrites what crosses and touches neither the plan nor the app, and appends `handoff-recrossed` for the card to fold onto the receipt it already has. It is deliberately not a second confirm: confirming writes a plan card. **There is no `target` on that route and there will not be one** — which Built App a handoff lands in is a per-handoff decision (§4, ADR-0008), so Change neither re-targets nor offers to remember a target. The `planId` it does send is not a target: it says which of this Conversation's handoffs the card belongs to, since one Conversation may have made several into several apps, and the newest answering for all of them would rewrite the wrong app's crossing. Files are rewritten; a Binding already upserted is not withdrawn, because taking a Resource away from an app that may be reading it is a deliberate act of its own — so `.sage/bindings.json` stays in the receipt whenever it is on disk, even when Resources are turned off.

**Undo** is the existing `/api/project/plan/cancel`, which now also takes the Conversation that pressed it and the plan the card is showing. It records `plan-cancelled` in that Conversation's transcript, so an Undo still reads as undone after a reload rather than for as long as the tab lives. A card naming a plan the app is no longer holding cancels nothing: a tab left open on a plan a second Conversation superseded (§ ADR-0009, #59) would otherwise archive the newer plan on behalf of a card that was already out of date. It archives only when there is a live plan to archive, so pressing twice leaves one archived plan and one row. A Built App the handoff minted **stays**, and the card says so: removing one is a deliberate action, never a side effect of deciding not to build.

There is no second form behind the callout. **Write a plan** drafts the plan and opens the sheet, and the sheet asks one question (§4).

## 4. The sheet (confirm)

Modal. Title: **Build from this plan**. Primary: **Open Builder**.

One question: **which app**. Default **New app**. Below it, the Project's existing Built Apps with
their last-built date. **Never preselect an existing app** — building over an app the person did
not choose is the silent overwrite ADR-0008 exists to close. Choosing one raises a warning that the
build replaces that app's plan, which appears nowhere else in the Workbench.

The sheet then lists the files this confirm writes, and that is all it does. The target is asked
every time and is never remembered.

What crosses is not asked. It is the viewer's saved answer, held in the Workbench's preferences
next to the conversation view (#52, `js/prefs.js`) and edited in Account settings:

| Preference | Default | Effect |
|------------|---------|--------|
| `handoffResources` | on | Session context becomes Bindings — step 3 below |
| `handoffArtifacts` | on | Artifacts stay in `examples/` and are named in `.sage/handoff.md`. Off does not delete files; it omits them, so implement does not treat them as required examples |
| `handoffTranscript` | off | Writes `.sage/handoff-transcript.md` — step 2 below |

The plan itself is not among them: a handoff without a plan is not this flow.

These were four checkboxes rebuilt from the same hardcoded defaults every time the sheet opened, so
one person answered the same questions on every handoff (#58). The defaults above are those
defaults exactly, so what a handoff writes for someone who never opens the drawer is unchanged. A
preference lives in the browser rather than the Project, for the reasons `js/prefs.js` gives. The
server keeps the same three defaults for a confirm that sends no `include` at all, and the two sets
have to stay in step.

**The target app must never join that table.** Persist what crosses, never where it lands.

The Project keeps its name, and Default stays Default. The **plan title names the Built App**, not
the Project. The old rule here renamed the Project on confirm and set `default: false`; it was a
Project-per-app rule that worked exactly once, and it is deleted — a Project holds many apps now,
and two of them cannot share one name (ADR-0008). Default is already a git-based project, so
Publish is available in Sage Builder either way. Do not PATCH Domino.

On confirm, in order:

0. Resolve the target. **New app**: mint `appId` with `new_id("app")`, seed `apps/<appId>/` from the template, set its display name to the plan title. **Existing app**: use its `appId`. The id and the directory never change afterwards — Domino fixes an App's `entryPoint` at creation.
1. Write `apps/<appId>/.sage/handoff.md` (digest + list of Artifact paths that stayed included + list of context names).
2. Optionally write `.sage/handoff-transcript.md`.
3. For each Session context row with `kind` in `data_source | model_api | llm_alias` that is still included, upsert a Binding via `Workspace.update_bindings` (existing path). Files in context that are already Attachments stay Attachments.
4. Set this entry's `status: "bound"`, `boundAt`, `appId`, `planPath: "apps/<appId>/.sage/plan.md"`, and `planId` (set when the plan was drafted, and kept through binding — `planPath` stops resolving once a build archives that copy, `planId` does not).
5. Route to `#/build/<threadId>?app=<appId>` — the existing route grammar (`SW.appRoute`), because Build is rooted on the Conversation and the app is the view parameter. The Build pane is the existing builder: plan approval card if that app's `has_built` is false (it will be, for a new app), preview on the right, resource panel showing the new Bindings as IN THIS APP. Build's header names that app.

The origin Conversation is recorded on the handoff entry, and it is the row you are already in: the rail lists Conversations in both modes, so Chat ↔ Build is turning your head, not starting over. Selecting a Built App happens in Build's header, not in the rail (#82, [ADR-0009](../adr/0009-one-conversation-build-is-a-view.md)). The Build URL still names the **app**, because one Conversation may have produced several (ADR-0008), and a link with no `?app=` resolves to the Conversation's newest `bound` entry rather than to whatever happens to be selected. The Conversation's row stays in the rail, tagged with the app once `has_built` is true. Build's composer is the existing one. What the pane shows is the whole Conversation — under the merged arm that includes the Chat turns this handoff came out of, so landing in Build is not a blank greeting; under the split arm it is this Conversation's slice of the Build `history.jsonl` alone, empty at this moment except what the plan-approval card needs.

## 5. Build after landing

Do not invent a builder agent. Map onto what exists:

| Step | Agent | Already |
|------|-------|---------|
| Draft / show plan | `sage-plan` | yes, plan gate writes `.sage/plan.md` |
| User approves | UI | existing Approve & build |
| Implement | `sage-implement` | yes, reads `.sage/plan.md`, edits `src/`, typecheck loop |

Extra prompt context on the first implement turn (and on the plan turn if plan was not drafted yet): the contents of `.sage/handoff.md`. Not a directory listing of `examples/<threadId>/` — that was removed. It repeated the digest's own "Artifacts to treat as examples" list back to the model, and because it walked the directory rather than reading the digest, unchecking Artifacts on the sheet listed the files anyway. The checkbox is supposed to omit them (§4); the digest honours it, so reading the digest honours it too. Implement is still bound by `template/react-vite/AGENTS.md` — it builds the app in `src/`, and may copy an example into the app if the plan says so, which works because the app directory carries a link to `examples/` (the agent's cwd is `apps/<appId>/`, so without it those paths resolve to nothing). It must not treat Chat history as a spec; `AGENTS.md` already says `.sage/history.md` is a past record and `.sage/plan.md` is live intent. Add one line to the implement prompt (only for a turn that has `handoff.md`): "A Chat Conversation produced the files under `examples/` and the digest in `.sage/handoff.md`. The plan is what to build. The digest is background."

`.sage/handoff.md` is written once, at the crossing, so it says nothing about a chart discussed a minute after it. **Every** Build turn therefore also carries the tail of its own Conversation's Chat transcript, rebuilt on each turn by `chat_compact.chat_summary` from `.sage/threads/<threadId>/history.jsonl` — what the person and Sage said, newest turns first into a fixed character budget, so the cost stays flat as the Conversation grows. It is framed as background rather than as a backlog: the turn builds what the request asks for, and the transcript is there so that "that chart" resolves. A Conversation with no Chat turns adds no section at all.

The summary is keyed on the Conversation that drove the turn (`build_conversation`), never on the Built App. Two Conversations can drive one app (#73); keyed on the app, the second would be handed the first one's Chat.

Phased builds (`plan_steps.py`) stay as they are. First-build gate stays as it is.

## 6. `handoff.json` states

`handoff.json` is a **list**, one entry per handoff. A Conversation may hand off more than once, to a
different Built App each time (ADR-0008), so a single status per Conversation cannot hold it. Each entry
carries its own `planId`, `appId` and status.

```
absent → suggested → planned → bound
                 ↘ suppressed
```

| status | meaning |
|--------|---------|
| `suggested` | callout visible; classifier will not run again while this entry is unresolved |
| `suppressed` | callout gone for this Conversation, permanently; Open in Build still available |
| `planned` | `apps/<appId>/.sage/plan.md` and its plan document written from this Conversation; `planId` recorded; sheet not yet confirmed |
| `bound` | sheet confirmed; `appId` set; Bindings upserted; user is in Build. **The Conversation is eligible for a new suggestion from here.** |

`suggested` and `planned` are unresolved; `bound` is resolved. `suppressed` is the only permanent
one, because it is the person saying stop rather than a step finishing.

There is no `status: built`. `has_built()` remains the source of truth that code exists, read per
Built App rather than per Project.

## 7. Reverse direction

Out of scope. "Ask about this app" from Build can wait. A user who wants Chat after a build opens the origin Conversation or hits New conversation in the same project.

## 8. Acceptance

1. Three analysis turns that never mention an app produce **no** callout. A fourth that says "put this on a dashboard colleagues can open" produces exactly one callout. Reload does not show it again as a new suggestion (`suggestedAt` is set).
2. **Not now** hides the callout permanently for that Conversation. Overflow **Open in Build** still opens the sheet (and drafts a plan if none exists).
3. Confirm with the transcript preference off writes `plan.md` and `handoff.md`, does not write `handoff-transcript.md`, upserts Bindings for Data Sources that were chips, leaves `src/` still untouched until Approve & build.
4. After confirm, `#/build/<threadId>` shows the existing plan approval card, the preview pane, and IN THIS APP containing those Bindings. The rail still has the Conversation. Switching to Chat shows the same Conversation, not a blank greeting. — This last clause is the one the split UI could never satisfy, in either direction. It is delivered by the merged transcript, in Chat by #56 and in Build by #57 ([ADR-0009](../adr/0009-one-conversation-build-is-a-view.md)); under the split arm it stays unmet by design, until the comparison ends and #61 removes the loser.
5. Approve & build on that card runs `sage-implement`, which reads `plan.md`, sees `handoff.md`, and edits `src/`. Typecheck loop runs. Chat's `examples/` files are still there.
6. The Project's name is unchanged by confirm, and Default is still Default. The new Built App's display name is the plan title.
9. A second Conversation in the same Project confirms a handoff with **New app**. It gets a second `apps/<appId>/`, and the first app's `src/`, plan document and Bindings are untouched. Both appear in Build's header app control.
10. A Conversation that has already reached `bound` produces a fresh callout when it drifts toward a different app. A Conversation that was **Not now**'d never does.
11. The sheet never preselects an existing app.
7. Classifier timeout or 5xx → no callout, turn otherwise succeeds. Three consecutive garbage verdicts trip a breaker and Chat stops calling it for this process (same pattern as `scope.py`).
8. Tests: classifier bias (APP only on app-shaped last turn); sheet confirm file set; Bindings upsert from `context.json`; Conversation URL survives the mode switch; `sage-chat` history.jsonl is not the file implement greps as `.sage/history.md`.
12. After confirm the plan card names the Built App the plan went into and whether it was new, and expands to the charts, context and files that crossed. Change redoes the crossing without minting a second card and without asking which app. Undo leaves the plan document, the digest, the Bindings, the Artifacts and the Built App where they are, says the app stays, and is safe to press twice. The card says nothing about publishing or the preview — that is the build-run row's job (#56).
