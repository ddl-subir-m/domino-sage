---
status: accepted
supersedes: none (revises docs/workbench/handoff.md §1)
---

# Build is per conversation, the way Chat already is

Build ran one OpenCode session and one transcript per Project: `.sage/session.json` and every
entry in `.sage/history.jsonl`. Chat, by contrast, was already per Thread — its own session under
`.sage/threads/<id>/session.json`, its own `history.jsonl`.

The rail is shared by both modes, so both offer **New conversation**. In Build it did nothing a
person could see: the route changed, the transcript did not, because the transcript belonged to
the Project. Filtering the transcript alone would have been worse — the screen would empty while
the one session kept every earlier turn, so "make that button blue" would resolve against a turn
the person could no longer read.

## Decision

A Build turn belongs to a conversation.

- Its OpenCode session is `.sage/threads/<threadId>/build-session.json`, beside that Thread's Chat
  session. Deleting a Thread takes both.
- Its persisted events carry a `conversation` field in `.sage/history.jsonl`. The file stays one
  append-only log, so the stop-button revert stays positional and stays correct.
- `GET /api/project/history?conversation=<id>` returns that slice. Naming none returns the whole
  log.
- `.sage/history.md` still renders the whole log. That archive is the **agent's** memory across a
  Project, and cross-conversation memory is the point of it.

## Consequences

- **New conversation** in Build opens an empty transcript and a session with no memory of the
  earlier talk. It is not amnesia about the app: the session opens the same workspace, so the
  agent reads every file back. It forgets the conversation, not the code.
- Follow-ups that lean on earlier wording ("make that bigger") work inside a conversation and not
  across one. That is the same contract Chat has always had.
- Typing in Build opens a conversation, the way typing in Chat does. A Build turn is never
  untagged from the UI.
- Entries written before this change are untagged. The Project's **oldest** conversation adopts
  them on first read or write, so an upgraded Project keeps its transcript while a conversation
  created after the upgrade still opens empty. A Project with no Threads leaves them for the
  first conversation that builds.
- `docs/workbench/handoff.md` §1 said "Build uses the project's existing Build session". Revised
  there.

> Revised, not overturned, by [ADR-0008](0008-a-project-holds-many-built-apps.md). The split of
> session and transcript per conversation stands. What changed is the log's *home*: a Project holds
> many Built Apps now, so `history.jsonl` and `history.md` live under `apps/<appId>/.sage/` and the
> agent's memory is per app rather than project-wide. The reason given here for project-wide memory
> — that cross-conversation memory is the point — still holds, and now holds *within* an app. It
> also fixes a bug this ADR could not see: the stop-button baseline is a position in the log, so two
> Sage Builders appending to one file meant one viewer's stop could truncate the other's turns.
