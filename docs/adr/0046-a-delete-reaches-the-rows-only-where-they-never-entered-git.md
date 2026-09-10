---
status: accepted
revises: ADR-0036 (a deleted Conversation takes its transcript with it — the act is unchanged;
         what it can promise is stated precisely, and it now depends on ADR-0045's opt-in)
---

# A delete reaches the rows only where they never entered git

[ADR-0036](0036-a-deleted-conversation-takes-its-transcript-with-it.md) is titled *A deleted
Conversation takes its transcript with it*. Its body is honest about the limit — "Git history
keeps every earlier copy. Nothing short of a history rewrite changes that" — but the title is
not, and the title is what a reader carries away.

`delete_thread` purges the Artifacts (`service.py:5941`, `purge_artifacts=not spoken_for`), which
removes them from HEAD. Anything that was ever committed stays in history, on whatever host the
remote points at, and Sage pushes. No delete path Sage can write will reach it.

We decided **not to rewrite ADR-0036, and to state the promise in two halves instead**, because
[ADR-0045](0045-an-artifact-commits-the-shape-and-the-rows-only-by-consent.md) changed what the
promise can honestly be.

## The promise

**Without the opt-in.** A delete takes the rows, because there were never any. What survives in
git history is a table name, a column list, a row count and a timestamp. That is a promise Sage
keeps, and ADR-0045 is what makes it keepable — the honest fix to a delete that could not reach
its data was to stop writing the data, not to write a better delete.

**With the opt-in.** A delete removes the file from HEAD. **The rows stay in git history, on the
remote, and nothing Sage does will remove them.** A history rewrite is not on the table for a repo
that two Builders and a published app all clone.

## Where the second half is said

In the **opt-in dialog**, not only here. A person turning rows on is accepting a consequence for
every Conversation they will later delete, and the delete dialog is too late to tell them: by then
the rows are already pushed, and saying it there would explain a loss rather than offer a choice.

The delete dialog keeps the sentence ADR-0036 already gave it. It is accurate under both halves —
under the first it is merely no longer the interesting part.

## Considered options

**Rewrite ADR-0036, or retitle it.** Rejected. The decision it records is sound and unchanged: a
delete removes the talk, the record shrinks to a tombstone, the act commits and pushes on its own.
Only its scope needed narrowing, and a `revises` pointer does that without rewriting a document
whose reasoning still stands. Retitling would also break every inbound reference to it for a
cosmetic gain.

**Fold this into ADR-0045.** Rejected, narrowly. The retention promise is downstream of that
decision and has no independent trade-off, which usually argues against a separate ADR. But
revising an accepted decision is itself an act worth recording where a reader will look for it —
beside ADR-0036, under a title that answers the question they arrived with.

**Promise nothing, and say only that git history is permanent.** Rejected. True, and it discards
the good half. Without the opt-in a delete genuinely does reach the rows, and a person who is
anxious about what is in a transcript deserves to be told which of the two situations they are in.

## Consequences

- **The promise is now a property of the Project, not of the act.** Two Projects, the same button,
  different guarantees. The opt-in state has to be legible wherever the promise is made.
- **ADR-0036's sweep still applies.** Conversations deleted before that change still have their
  transcripts on disk and on the remote, and the bounded sweep at Project open still repairs them.
  This decision does not touch it.
- **A Project that opts in and later opts out is not repaired.** Turning rows off stops new rows
  and does nothing about the ones already pushed. The dialog must not imply otherwise.
- Language: [CONTEXT.md](../../CONTEXT.md).
