---
status: accepted
extends: ADR-0029 (a folder is the unit of the act — which turns the collision below from rare into
  routine), ADR-0020 (the working set is orientation, never context)
---

# A mention names one file, or says what else it matched

An `@` token is a basename. `mentionToken` takes `path.split('/').pop()`
(`backend/sage/workbench/js/util.js:248`), and `collectTurnRefs` tests every row against that token
and pushes each distinct path it matches (`backend/sage/workbench/js/store.js:1031`).

So with `raw/2025/data.csv` and `raw/2026/data.csv` both attached, `@data.csv` matches **both rows**,
and the server honours **both** — `_resolve_mentions` looks up exact manifest paths and finds each
one (`backend/sage/orchestrator/service.py:3590`). Two descriptors are inlined. One file was asked
for.

This was always possible and always rare, because attaching two files with one basename took two
deliberate clicks in two folders. [ADR-0029](0029-a-folder-is-the-unit-of-the-act-and-a-file-is-the-unit-of-the-record.md)
makes it routine: a partitioned folder is the thing a recursive attach is *for*, and identical
basenames under different date partitions are the normal shape of one.

## The token is generated, so the token can be made unique

When a basename is not unique among the app's Attachments, `mentionToken` falls back to the shortest
distinguishing path suffix — `@2026/data.csv`.

This costs no rule anybody has to learn, because the menu *inserts* the token; a person types `@`,
narrows, and picks. And it is safe in the matcher today: `mentionedIn` escapes the token and anchors
both ends (`util.js:257`), so a `/` and a `.` inside one match literally.

Uniqueness is computed against the app's own Attachment list, and `mentionToken` is used by both the
picker that inserts and the turn that reads back — which is why the fix lands in one function rather
than two that can drift.

## A stale token resolves to everything it matches, and says so

The uniqueness rule alone has a hole worth stating, because it is the failure this whole area was
built to prevent. Text already sitting in the composer keeps the token it was given. If `@data.csv`
was unique when it was typed and a later attach makes it ambiguous, `mentionToken` now computes
`@2026/data.csv` for both rows, neither matches what is in the box, and **the mention silently
carries nothing** — exactly what the comment at `util.js:246` exists to warn against.

So: unique tokens going forward, and an ambiguous token that no longer resolves carries **all** its
matches and reports that it did. The turn says the name matched three files and names them.

Silence is the one outcome ruled out. A mention that quietly carries nothing produces an answer about
the wrong thing, or an answer about nothing, with no error anywhere — the class of bug
`collectTurnRefs` was written to end.

Rejected: **refuse the ambiguous mention and carry none of them.** It turns an ordinary partitioned
Dataset into a dead end, and the person cannot see why: the two files are indistinguishable in the
menu, which is the next section.

## The picker has to be able to reach the file it is disambiguating

Two findings, and neither half works without the other.

The query never looks at the folder. `workingSetFirst`'s matcher tests `row.name` and
`path.split('/').pop()` — basename only (`util.js:291`). Typing `2026`, or `raw/2026`, matches nothing
for `raw/2026/data.csv`.

The row never shows the folder either. It renders the name and a kind caption
(`backend/sage/workbench/js/components/composer.js:595`). Two colliding files draw two identical rows
— same icon, same label, same caption — that insert the same text.

So both change. The query matches on the full relative path, and the row shows the distinguishing
parent folder in the caption slot it already has, with the full path in `title` the way `LeafRow`
already does it in the Dataset tree. Filter-without-path means typing `2026` returns two rows still
indistinguishable; path-without-filter means seeing the difference and being unable to narrow to it.

Widening the matcher is contained, which is worth recording since `workingSetFirst` is shared on
purpose. Its only other caller passes **no** query (`backend/sage/workbench/js/modes/builder.js:688`)
and the matcher returns `true` on an empty query, so the widening reaches the composer's picker and
nothing else.

This is the better half of the fix. Unique tokens treat the symptom; a searchable, legible picker
treats the cause, which is that the right file could not be seen or reached.

## Above the threshold, the folder is the row

The menu shows eight rows, deduplicated by id rather than by token (`workingSetFirst`, `limit: 8`).
After a 200-file attach it is a window onto a list that cannot be seen, and eight rows out of 200
reads as a complete list.

So the menu follows ADR-0029's threshold, by the same rule and for the same reason: above roughly ten
files the folder is the row, and a single file is reached by typing enough of its name — which now
works, because the query reads the whole path. One rule across both surfaces, so the block the agent
reads every turn and the menu the person picks from cannot come to disagree about what a Dataset
mention means.

A folder mention carries real server work, recorded here rather than discovered later:
`_resolve_mentions` honours exact manifest paths only, so a folder token must expand to its member
paths **and** collapse their descriptors the way the `AGENTS.md` block does — one folder summary, not
200 `detail` blocks. Without the collapse, the folder row re-introduces exactly the context bloat
ADR-0029 removed, through the other door.

Rejected: **folder rows and file rows together.** It doubles the rows in a menu that shows eight.
Rejected: **file rows only, as today.** The status quo, and it misrepresents itself.
