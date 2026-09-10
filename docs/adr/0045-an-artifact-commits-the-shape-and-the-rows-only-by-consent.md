---
status: accepted
revises: ADR-0041 (the rows still reach the person and not the model; what changes is the claim
         that nothing leaves Domino, which was the reason no consent was owed), ADR-0006
         (Artifacts still live in git — their rows do not)
---

# An Artifact commits the shape, and the rows only by consent

[ADR-0041](0041-a-live-read-reaches-the-person-without-reaching-the-model.md) asks the person for
nothing, and says why in one sentence:

> "nothing leaves Domino on the way to the answer, so there is no decision to ask for."

That sentence is false whenever the Project's git remote is not Domino's. A Live read writes an
Artifact, `_flush_chat_save` commits it, and Sage pushes. The rows leave Domino by `git push`,
on the turn that read them, to a host nobody named.

Domino Projects use whatever git credential is present. The remote can be GitHub, GitLab, an
enterprise install, anything. So the reason ADR-0041 gave for skipping consent does not hold,
and it never held for external remotes.

We decided **an Artifact commits its shape — columns, row count, the statement, the timestamp —
and commits its rows only where the Project has said so.**

## What Sage did not know

`workspace/git.py:72`:

```python
def has_remote(path: Path) -> bool:
```

A boolean. That is the whole of Sage's knowledge of where it pushes. There is no `remote get-url`
call anywhere in `backend/sage`. Sage has been pushing data rows to an unnamed host since the
Live read shipped.

## Sage builds the audience mismatch itself

The argument that this matters does not need a hypothetical collaborator. Sage manufactures the
exact pair, in one modal, with no role choice:

- `add_collaborators` (`service.py:3901`) adds **everyone as `contributor`**. There is no role
  picker, deliberately — "roles differ in ways the creator cannot judge"
  (`COLLABORATORS-HANDOFF.md`).
- A `contributor` **gets Read files**. Domino's own permissions table,
  `DATASETS-VS-ARTIFACTS-RESEARCH.md:814`. There is no per-file or per-directory ACL.
- A `contributor` **gets `403` on the Project's own Dataset**. Probed live on two Projects,
  `PERMISSIONS-RESEARCH.md` Q5: `does not have 'list' permission on 'dataset:…'`. Every
  Dataset-facing listing returns `200 []` rather than a refusal, so the denial is silent.

Add a person to a Sage Project and they can read every row Sage ever committed out of a Dataset
Domino refuses to show them. That is not a risk this decision guards against. It is the default
path through the product.

## Sage cannot know the audience, so it does not try

Two ways to decide automatically were live, and both are dead on the same fact.

**Compare the audiences.** Read the repo's collaborator list, the Dataset's grants and the Data
Source's `permissions`, and commit only where the repo's audience is already covered. Three grant
lists per Artifact, an answer that goes stale the moment anyone is added — and, decisively, a
repo whose audience lives on GitHub and is not a Domino record at all.

**Branch on where the remote points.** Permissive for a Domino-hosted remote, shape-only for an
external one. **Sage cannot classify its own remote.** A self-hosted Domino runs on the
customer's domain; so does GitHub Enterprise. No hostname test separates them, and the failure
is asymmetric: an unrecognised Domino treated as external is an annoyance, an unrecognised
GitHub Enterprise treated as internal is the leak. One behaviour for all remotes.

So the comparison is impossible and the disclosure is cheap. Sage names the **destination** it
can read — `git remote get-url origin` — and the person, who knows their own repo, decides.

## What is committed

One rule over every writer. The kind of source does not change the answer, because deciding by
source kind would need the audience fact Sage was just shown it cannot obtain.

| | Without the opt-in | With the opt-in |
|---|---|---|
| Live read of a table (`liveread/result.record`) | Columns, row count, `cap`, `truncated`, the statement, the timestamp. **No `rows`.** | Up to `CAP_ROWS` rows, as today. |
| Live read of a file head in a Dataset mount | Same shape, no rows. | Rows, as today. |
| Live read listing a Dataset's files | **Unchanged.** `[[path, size]]` is filenames; a filename is not a row. | Unchanged. |
| A `.table.json` written by `sage-chat` | The `rows` array is **stripped by the shim** before the write lands. | Written as the agent composed it. |
| A `.png` chart | **Not committed.** The card renders the title and the date, with no image. | Committed, as today. |
| The `.sql` sibling | Committed. It carries no values: `sample_rows` takes a table and a limit, and cannot filter (ADR-0041). | Committed. |

The Chat half is enforced rather than asked for. `shim/chat_paths.py` already intercepts every
write and reads the path out of the tool arguments; it reads the body on the same hook and drops
the array. An instruction in the prompt would not do — and if one is added anyway, note that the
Chat prompt lives in **two** hand-synced places, `template/chat/AGENTS.md` and inline in
`opencode.json`, so a change to one alone is a change the model never sees.

## The PNG follows the opt-in, and this is the uncomfortable part

A chart of the rows **is** the rows. A bar per customer is a customer list; a scatter of 500
points is 500 records. Stripping the `rows` array and committing a picture of the same values, to
the same repo, on the same push, is a decision that contradicts itself inside one commit.

The alternative was to argue that a chart is an aggregate and therefore a conclusion rather than
a copy. It is a good line — "pictures of conclusions, never copies of rows" — and nothing
enforces it. No inspection of a PNG can tell an aggregate from a row-level plot, and nothing
stops an agent drawing the second.

So charts of a Data Source or Dataset read do not survive a restart unless the Project opted in.
That is #222 reopened, deliberately, for exactly the charts that matter. The
opt-in is what buys them back, and the sentence on it says so.

## Where the opt-in lives

**Once per Project, off by default, in the Add-people modal**, beside the destination:

> Keep data rows in this Project's files? They are committed and pushed to
> `github.com/acme/analytics`.

Three things about that placement. The audience is a Project-level fact, so a per-Artifact prompt
would ask the person to re-answer "who can read this?" about a set they configured once, and
prompt fatigue turns that into a reflexive yes. The modal is where they are already thinking
about who gets in (ADR-0021 — each scope's door lives on the surface that owns it). And the host
name in the sentence is the thing that makes the choice informed; a toggle without it is a
setting nobody can weigh.

Off by default because the default must be the safe one when the destination is unknown, and it
is unknown until someone looks.

## Read again, and why the rows must not touch disk

The card carries a **Read again** button (its stamp reads *"Read 10 September 2026"*, so the
person can see the age of what they are looking at). Pressing it runs a Live read as **the
viewer** — each viewer gets their own Builder (`docs/workbench/chat.md`, acceptance criterion 1),
running as their own identity. A collaborator therefore re-reads as themselves and sees what they
are entitled to see. The elevation closes structurally rather than by policy.

Those rows reach the browser in the response and are **never written to disk**. If the button
wrote them into the `.table.json`, one click would defeat the opt-out permanently and the next
save would commit it.

This costs new plumbing. The render path is file-based today — `workbench/js/store.js:1228` reads
the artifact file — so a response path that carries rows to the card has to be added. The
alternative, a gitignored scratch file, reuses the existing render for free and was rejected: it
re-creates the sink one directory over, and ADR-0006 records an unresolved contradiction about
whether ignored files survive a restart at all. *"Rows never touch disk"* is a sentence that can
be tested. *"Rows touch disk but in an ignored file"* needs a footnote and an open question to
back it.

Re-reading on demand rather than on open is also what keeps the cost sane. ADR-0041 fixed that a
Live read is not cached across turns, on purpose. Re-rendering on open would mean a warehouse
query every time a card scrolls into view.

## Considered options

**Keep committing, and tell the person.** Rejected as the whole answer, kept as the opt-in. It is
honest and it is cheap, but as a default it makes the safe path the one nobody chooses.

**Move Artifacts to a durable store outside the repo.** Rejected on mechanism, twice over. Nothing
suitable exists: OpenCode's own store sits on the container overlay at `/home/ubuntu` and dies
with the workspace, and a Dataset is outside the `TurnSnapshot` work-tree, which ADR-0006 already
ruled out because a stopped turn would leave its charts behind. It would also have solved the
wrong problem — the rows would still have been readable by a wider audience than the source's,
just from a different place.

**Reuse `.sage/samples.json` as the consent record.** Rejected, and it was the first shape drafted.
That record answers a different question — *may these values enter the model's prompt?* — and it
is keyed by `(Binding, table)` and gitignored (`template/react-vite/.gitignore:35`). Reusing it
would silently re-purpose a consent given about a model into a consent about publication, and it
says nothing whatever about who can clone the repo.

**Commit the shape and re-render every Artifact from a Live read on open.** Rejected as a
universal answer, and its good half is kept. It cannot cover a `.table.json` written by
`sage-chat`, whose source is a pandas frame in a session that no longer exists, and it cannot
cover a PNG, for which no re-render path exists at all. Re-rendering on open would also swap
today's rows in under yesterday's prose, silently — the assistant's sentence describes the row it
read in September while the card shows a March one.

**Lower `CAP_ROWS` from 500.** Rejected. Under this decision the cap governs only what an
opted-in Project commits and what Read again holds in memory, and `result.py:21` gives the reason
it should not move: one Live read and one Chat table disagreeing about how much "all of it" is
"would be a difference nobody could see and everybody would trip on". `VALUES_BUDGET_CHARS` is
untouched — that budget is about the model, which this decision never reaches.

## Consequences

- **A stale transcript is a thin card.** Columns, a count and a date. Every look at real rows is a
  click and a query. This is the price of the decision, not a graceful degradation, and the ADR
  says so rather than dressing it up.
- **Two people see different tables.** Read again runs as the viewer, so it must. A card that
  rendered the same rows for everyone would be the elevation, restored.
- **A predicate would put values back in the `.sql`.** The statement is safe today only because
  `sample_rows` cannot filter. If filtering ever lands, the statement starts carrying literals and
  joins the rows on the wrong side of this decision.
- **`credentialType` on the bound Data Sources no longer needs probing.** It would have decided a
  split by source kind, and there is no such split. Worth knowing — an `isEveryone: true` Data
  Source with `Individual` credentials gates rows in the warehouse rather than in Domino, so
  committing its rows can elevate even where Domino permits everyone — but it changes nothing
  here.
- **Some collaborators cannot read the repo at all.** `Launcher User` and `Project Importer` are
  absent from Domino's "Read files" list. "Collaborator" is not one audience, which is a further
  reason Sage does not try to name it.
- **What a delete can promise changes with the opt-in**, and that is
  [ADR-0046](0046-a-delete-reaches-the-rows-only-where-they-never-entered-git.md).
- Language: [CONTEXT.md](../../CONTEXT.md).
