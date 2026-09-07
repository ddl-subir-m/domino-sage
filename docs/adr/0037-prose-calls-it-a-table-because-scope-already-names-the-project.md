---
status: accepted
extends: ADR-0014 (the overlay renames prose, never identifiers — this is that mechanism used on
  one word), ADR-0021 (the door rule; the door itself does not move, only its label)
---

# Prose calls it a Table, because "Scope" already names the Project

A person added a Snowflake Data Source, asked for a dashboard, and was told the Built App had
"no scope chosen yet". They did not know what that meant. Asked where they had seen the word, they
named exactly one place: the three-dot menu in App dependencies, reading "not scoped yet".

They were right that it is nearly invisible. It renders from one string, `NO_SCOPE_YET` in
`workbench/js/util.js`. The two other user-facing strings sit in flows they had not opened.

But the word is not only invisible. It is **overloaded three ways in our own code**:

| Where | What "scope" means there |
|---|---|
| `orchestrator/scope.py` | whether a request is big enough to deserve a plan |
| this glossary, `resources/bound_schema.py` | the database, schema and table a Binding reads at |
| `workbench/js/components/scope-picker.js`, `store.js` `Switched scope to <project>` | **the Project you are in** |

The picker literally named `scope-picker.js` is the Project switcher. So the word a person did not
understand on the App dependencies row is also the word for the thing they switch projects with.
No amount of better copy around it fixes a term that names three things.

## The prose word is Table; the identifiers do not move

[ADR-0014](0014-the-overlay-renames-prose-not-identifiers.md) already draws this line, and this is
that mechanism applied to one entry. `binding.scope`, `saveScope`, `bind_data_source`,
`/bindings/data_source/{id}/scope` and the manifest keys all keep the name they have. Only what a
person reads changes:

| Was | Is |
|---|---|
| Scope: not scoped yet | Table: not chosen yet |
| Choose a Scope beside its name | Choose the table beside its name |
| No Scope chosen yet, and no query the app writes can run | (see ADR-0038 — this sentence stops refusing) |

## Why not one abstract word for both kinds

The first attempt looked for a single word covering a Data Source's table and a Dataset's folder,
and produced a "Folder:" label for Dataset rows. That was wrong on the facts: a Dataset Binding has
no such record at all. `app.py` gates the scope route on `KIND_DATA_SOURCE`, `bindings.py` says a
Data Source Binding records scope *as well as* the Resource, and this glossary already said a
Binding names the Dataset and not a file, because a Dataset is not a file.

So there was never one concept needing one name. There is one concept, belonging to one kind, and
the plainest available word for it is the word the person already used when they asked the question:
**table**.

## What it costs

A Binding may name a database and schema and no table, meaning "any table in that schema". Under
the label "Table" that state would read as a lie, so it is drawn as **"any in PUBLIC"** rather than
as a table name. The panel can still produce that state deliberately; the [[Candidate]] flow never
does, because a search that ends on a schema has not answered the question it was asked.

We considered keeping "Scope" and fixing only the empty state. Rejected: the failure was not that
the word was unfamiliar, it was that three surfaces use it for three things, and a person who learns
it in one place is then misled in the other two. We also considered renaming to "Reads", which the
reader rejected as no clearer, and "Location", which is honest for both kinds but teaches nothing.
