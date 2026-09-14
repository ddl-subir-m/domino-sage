---
status: accepted
extends: ADR-0047 (a named folder says which of four states it is in — the same rule, for a file
         rather than a folder, and reaching the reader rather than the agent)
---

# An unreadable file is its own state, carried beside what was read

Six issues, all filed on 2026-09-13, one question. A file Sage shows somebody will not decode,
and every one of them had to decide what the screen says next. They arrived together because they
were found together — one sweep, six files — which is the tell that they share a cause.

- **#303** — a non-UTF-8 `attachments.json` stopped the Project opening. The catch was narrow and
  `UnicodeDecodeError` is a `ValueError`, not an `OSError`.
- **#326** — six more narrow catches survived the #303 sweep. One bad `meta.json` 500'd the
  Threads list.
- **#325** — the open path was guarded and the writers were not.
- **#327** — the instructions panel draws an empty box while every build still receives the old
  text. Both halves are individually right; the pair is a lie.
- **#331** — `read_history` has no guard at all, and the obvious lenient fix writes an **empty
  transcript** into a handoff.
- **#341** — `_write_generated` reads a file back unguarded, so a Binding change answers
  `400 "invalid file path"` about a file the person never chose to edit.

Three of these are closed and three are open. They are not six bugs. They are one missing rule,
found six times, and the sixth found it by turning the fix for the first into data loss.

## The rule was already here, twice

`CatalogOverrides` (`backend/sage/workspace/manager.py:67`) says it plainly, and says it was
learned the hard way:

> `unreadable` and `whole_file` are separate FIELDS and not one set with a sentinel in it. They
> answer different questions for different callers — the panel needs both and says a different
> sentence for each … and folding the second into the first was wrong twice.

`table_shape.unreadable()` (`backend/sage/workspace/table_shape.py:136`) does the other half:

> Sage cannot read it, so it cannot promise it holds no values, and it cannot name a count it
> never read … keep what can be named — here, nothing — rather than push bytes nobody has looked
> at.

And `read_instructions(*, strict=False)` (`manager.py:952`) already splits the callers by what
they do with the answer. What none of them did was say so out loud where the next reader would
find it, so the next reader wrote the narrow catch again.

## Decision

**An unreadable file is a state, not a value.** It is named beside what was read, and the reader
picks its own sentence from it.

| Rule | What it forbids |
|---|---|
| **Unreadable is carried as its own field** | `""`, `[]`, `{}` or `None` standing in for "I could not read this". A value that means both "empty" and "unreadable" cannot be asked which one it is. |
| **A partial read says how much it lost** | Silently dropping 3 malformed lines from a 500-line transcript. The count is part of the answer; a transcript missing turns with no notice is a quiet lie. |
| **A caller that REWRITES from a read refuses** | Turning a failed read into a durable deletion. This is `strict=True`, and it is the difference between a shrug and data loss. |
| **A caller that DISPLAYS proceeds, and says so** | Refusing to render anything because one row was bad. Show what parsed, name what did not. |
| **A file Sage generated is repaired, not refused** | Bricking a valid act on damage to bytes Sage owns and can fully re-derive. |

The last row is narrow on purpose. It covers files Sage writes from state it holds —
`src/appLlm.config.ts`, the rendered schema, the samples file. It does **not** cover a file the
person wrote, and it does not cover a file Sage only reads. The read-back in `_write_generated` is
an optimisation to keep somebody's git history clean, not a guard on their content, and treating
an optimisation's failure as a veto is what produced #341.

**Every state says what is still true.** #327 is the shape: the panel says the file could not be
read *and* says the last good version is still what every build receives. An error that reports
only the failure leaves the person to guess whether their work survived.

## What this is not

It is not a promise that Sage repairs anything. Sage reads what it can, says what it found, and
leaves the bytes alone everywhere except the row above.

It is not a new error surface. `unreadable` is a field on an answer that already crosses the wire.
Nothing here asks for a second endpoint, and #327 records why: the panel needs to render a third
state, not fetch a different value.

It is not permission to widen a catch. `except Exception` around a read still hides the bug that
a decode error is not the only thing that can go wrong there. Catch `ValueError` and `OSError`,
the pair #303 settled, and let the rest raise.

It does not settle the route-level mistranslation. Seven routes in `orchestrator/app.py` answer
`400 "invalid <your input>"` for any `ValueError` raised anywhere below them, so a guarded reader
can still have its honest failure relabelled as the caller's fault on the way out. That is **#342**
and it is open.

## Consequences

Three open issues inherit their answer and stop being design work:

- **#331** carries a dropped-line count out of `read_history`, refuses on the rewriting callers —
  `service.py:8229` writes the handoff transcript from it — and renders on the displaying ones.
  There are two readers, not one: `workspace/threads.py:257` and `workspace/manager.py:1553`.
- **#341** overwrites the generated file it cannot read, because it owns it.
- **#327** adds the third state to the instructions GET rather than a second endpoint, and keeps
  `write_instructions` strict.

The count in rule two is a number somebody can act on only sometimes, and that is accepted. For a
hand-edited file it points at the repair; for a log Sage wrote itself it points at nothing the
person can do. Saying it anyway is still right — the alternative is a reader who cannot tell a
short conversation from a truncated one.

This does not sweep the remaining narrow catches. #303 and #326 swept what they could see, and
#325 and #341 were both found afterwards, one file over, by somebody bombing a file rather than
grepping for a spelling. A grep names the spelling you searched, not the class. The next one will
be found the same way.
