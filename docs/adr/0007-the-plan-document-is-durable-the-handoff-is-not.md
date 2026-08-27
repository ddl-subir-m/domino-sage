---
status: accepted
---

# The plan document is durable; the handoff copy is not

A plan turn now writes two things. `.sage/plan-docs/<id>/` holds the **plan document** — one
`vNNN.md` per edit plus a `meta.json` of versions, reviewers, comments and approvals. It is
durable, and it survives every build. `.sage/plan.md` is unchanged: the one-shot copy the
Implement turn consumes, archived to `.sage/plans/NNN.md` the moment a build reads it.

This does not reverse [SPEC.md's locked decision](../../SPEC.md) that `plan.md` is transient. It
splits the two jobs that decision was holding in one file.

## Why this is worth writing down

A reader who finds a directory of durable plans one level from a file the spec insists must never
persist will reasonably conclude the decision was overturned. It was not. Both halves are still
true, and the reason `plan.md` is archived is the reason the document lives somewhere else.

The workspace is the agent's cwd and it has filesystem read access. A stale `plan.md` reads like
*current intent* — it is the one `.sage/` file that looks like instructions rather than data. That
is why it is archived after consumption, and it is why the durable half could not simply be
"stop archiving `plan.md`".

## What the split buys

| | `.sage/plan.md` | `.sage/plan-docs/<id>/` |
|---|---|---|
| Read by | the Implement turn | people |
| Lifetime | one build | the project |
| After a build | archived to `.sage/plans/NNN.md` | still there, still openable |
| Shape | markdown | the same markdown, plus review state |

The same split already existed one level down: `.sage/architecture.md` is durable precisely
because it is a reference somebody re-reads, while `plan.md` is a handoff. This applies that
distinction to the plan itself rather than making a second exception for it.

## Considered options

**Make `plan.md` permanent** — rejected. It reverses the locked decision and reinstates the exact
trap that decision exists to prevent: a later turn reading last week's plan as this week's
instruction.

**Structured JSON as the source of truth, markdown rendered from it** — rejected. The plan is
edited by hand in two places (the approval card's `plan_edits`, and the plan page), so a JSON
source means the file the agent reads and the thing people edit can drift apart. Markdown stays
the truth; `plan_doc.parse_sections` reads sections out of it and `plan_doc.render` writes them
back, with `parse(render(x)) == x` as the module's first test.

**A second model call to extract the sections** — rejected, for the three reasons
`orchestrator/plan_steps.py` already gives for refusing one: anything extracted at plan time is
stale the moment somebody edits the plan; a model call directly under the Approve button is
latency plus a failure mode; and "weak model reads a plan and quietly drops a constraint" is the
failure the parsing was meant to avoid, so putting one on the critical path is self-defeating.

**Brief sections only, no `## Plan`** — rejected. `plan_steps.parse_steps` reads the numbered
steps under `## Plan`, and phased build is gated on that parse succeeding. Dropping the section
would have silently turned phased builds off. `## Plan` is kept verbatim as a section of the
document, so both readers get what they had.

## Consequences

- **An edit to a live document rewrites `plan.md`.** Otherwise the page shows one plan and the
  build runs another, and the rail keeps counting steps that are no longer there. Only while a
  handoff is live, and only from the document it belongs to — editing an older plan after its
  build must not resurrect it as the thing being built.
- **The `.sage/` rule in `template/react-vite/AGENTS.md` now names `plan-docs` explicitly.** The
  blanket rule already covered it, but a directory of durable plans is the largest
  looks-like-instructions surface in `.sage/`, and the belt-and-suspenders line is there for
  weaker sovereign models that ignore subtle cues.
- **Reset clears `.sage/plan-docs/`**, for the reason it already clears `plan.md` and
  `architecture.md`: the documents describe the app being replaced.
- **An architecture gets no document.** `.sage/architecture.md` is already durable and already
  nothing archives it; a second copy would be two places to edit one design.
- **Versions accumulate and nothing prunes them.** A plan edited fifty times keeps fifty files.
  They are small markdown and git holds them either way. Left open deliberately.
- **The review half needs a member directory.** Reviewers, approvals and comment attribution read
  `GET /api/members`, backed by Domino's `/v4/projects/{id}/collaborators`. Off Domino, and for a
  caller who cannot read the project record, that list is empty and the page shows ids where it
  would show names — worse than names, better than a page that will not load.
- **Not live-verified.** The collaborators call is built from
  `spikes/domino-probes/swagger.json` and tested against a stubbed server. It has never run
  against a real Domino deployment.
- Language: [CONTEXT.md](../../CONTEXT.md).
