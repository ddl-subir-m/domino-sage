# Build this again is the one way a built plan can still be edited

[ADR-0007](0007-the-plan-document-is-durable-the-handoff-is-not.md) says an old Plan must never
come back to life as build instructions for a later, unrelated turn. That rule stands. This ADR
carves out one narrow exception: a person editing the Plan that produced a Built App's current
state, and asking Sage to build from that edit right away, in the same action.

**Decision**: add "Build this again" — an action on the Plan page, offered only on the Plan that
is a Built App's current state. Clicking it writes a new version of that same Plan document and
starts a build from it immediately. It reuses the existing approve-and-build path exactly as a
first build does: no new diff engine, no partial-apply logic. The person's own edit is both the
write and the read; nothing is left on disk for a future, unrelated turn to pick up.

**Why this doesn't reopen ADR-0007**: the risk ADR-0007 guards against is a *stale* `plan.md`,
written once and misread much later by a turn that has nothing to do with the edit. "Build this
again" never leaves that gap open — the write and the read happen in the same click, by the same
person, for the one purpose the button states. The moment any other turn changes the Built App,
the button stops working. It shows disabled, with a tooltip explaining why, rather than silently
reusing a Plan the app has already outgrown.

**Consequences**:
- Editing a Plan's text has never reset its status or reviewer sign-offs — only the explicit
  review actions do that. "Build this again" changes that, but only for itself: clicking it resets
  the document to draft and clears prior approvals, because those approvals were for words that no
  longer exist. A plain section edit with no rebuild still leaves status and reviewers untouched,
  exactly as it does today.
- The button never asks for a second confirmation — the click already is the approval, same as
  the first build. Where it would silently destroy other people's sign-off, it says so on the
  button itself, rather than in a dialog someone has to click through.
