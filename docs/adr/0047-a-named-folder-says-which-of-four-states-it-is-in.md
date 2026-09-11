---
status: accepted
extends: ADR-0029 (a folder is the unit of the act, a file the unit of the record — the collapse
         this qualifies), ADR-0039 (a Dataset is found the same way and declared by attaching —
         same failure, reached through the chip rather than the Binding)
---

# A named folder says which of four states it is in

Sage names folders to the agent. The Dataset chip says *files at
`public/data/<slug>/uploads`*. The managed AGENTS.md block says *14 files in
`public/data/<slug>/uploads` — fetch `data/<slug>/uploads/<name>`*. Neither named a file.

Naming a folder and no files was survivable while three other rules did not exist. They do:

- **Do not grep.** `public/data/` is gitignored and every attachment is a symlink, so a search
  returns no matches even when the value is there — a wrong answer, not an error.
- **Do not search elsewhere for a substitute.** An agent that grepped the repo for a similarly
  named folder once answered about the wrong data.
- **Do not list directories.** Written for the Artifact output folder; read as a blanket ban,
  because nothing in the sentence carried its scope.

Each rule is a scar and each is correct. Together they close the box. The agent is told to read
files it was never named, and every way of learning the names is shut. Listing is the one door
left, so it lists — and when the folder is missing or empty, nothing it is allowed to do next can
help. It lists again. Observed live: five identical `ls -R` calls in one turn, to the 600s ceiling,
on a Project whose Chat working directory still pointed at the user's first app.

## Decision

**Every folder Sage names to the agent says which of four states it is in.** The sentence is never
silent, and the state is never inferred from absence.

| State | What the line says |
|---|---|
| Holds files | Name them |
| Holds many | Name the first `FOLDER_COLLAPSE_THRESHOLD`, then the count, then the one listing that is wanted |
| Holds none | Say so, and say not to read anything else in its place |
| Cannot be read | Say so, name the path, and say not to read anything else in its place |

A name with nothing behind it is its own state, not a fourth way of saying empty: a rehydrate can
leave the symlinks dangling, and reporting that as "no files" sends the person looking for an
attachment they already made.

**Every ban carries its exit.** A rule that forbids a move must say what to do when its own
assumption fails — here, that reporting the failure is the correct answer to the turn. A ban with
no exit does not prevent the wrong move; it prevents the turn from ending.

## What this is not

It is not a relaxation of the three rules. All three stand. The grep ban is still absolute, and
the listing ban is unchanged where it was always aimed — at hunting for the output folder.

It is not a promise that a folder is readable. Sage reads what it can, from where the agent
actually stands, and says what it found. Being wrong out loud is the point.

## Consequences

The two surfaces read their state from different sources and that is deliberate. The Chat chip has
no manifest, so it reads the folder from the chat working directory — the only place every path in
the prompt resolves. The build block already holds the names in `project.attached`, so it reads the
record and never touches disk. One rule, two sources, no shared helper: a helper over two callers
with different sources would have to be told which one it was serving.

The collapse threshold now governs both surfaces. That is one number and one rule, which is the
argument the constant already makes for itself.

This does not close the loop on its own. A model can still repeat a call that fails for a reason
Sage did not predict, and no turn loop counts repeats today — the quiet timers never fire, because
a turn calling `ls` every few seconds is never quiet. A repeat brake is the follow-up.
