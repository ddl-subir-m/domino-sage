---
status: accepted
extends: ADR-0038 (Sage finds, the person binds — this gives the assistant the tool that one
  withholds, and says why the two cases differ), ADR-0020 (the working set is orientation, never
  context), ADR-0021 (each scope's door lives on the surface that owns it), ADR-0029 (a truncated
  listing says so), ADR-0006 (Artifacts stay in git)
---

# A Live read reaches the person without reaching the model

Observed live. A person with `Snowflake-Data-Warehouse` bound asked Sage, in Build, **"show me 1
sample conversation"**. Sage answered:

> I can't pull a live row from the Data Source directly here — only the running app can query it.
> Open the Built App preview and look at the conversations table.

They asked again, "show me 1 sample conversation here", and got:

> I don't have a real conversation row in the project text to quote here, so I can't truthfully
> show one inline.

Both replies are dead ends, and the second one narrates the mechanism, which Chat's own `AGENTS.md`
forbids in as many words. Auto and Ask gave the same answer because both take the same path.

The cause is one line, `router/phase_classifier.py`:

```python
READ_ONLY_DENIED = WRITE_TOOLS | SHELL_TOOLS
```

A read-only turn loses the shell, and the shell was the only road to a Data Source. Sage was honest.
It genuinely could not read that row.

## What read-only protects

Two readings were live: that the turn changes no **Built App**, or that it writes nothing and
reaches nothing outside the repo. The code enforced the second by accident, having stripped the
shell for the first reason.

**Read-only means the Built App does not change.** A read-only turn may read the world. Three things
say so. The promise on screen is about the app. Chat reads Data Sources every day and nobody calls
Chat unsafe. And ADR-0038 already has Sage reading a Data Source's catalog *inside a Build turn* to
rank Candidates — a Build turn touches Data Sources today, and the only question was ever who holds
the handle.

## The rows go to the card, not into Recall

This is the decision the rest hangs off.

A live row read into an answer enters **Recall**, and Recall goes to whatever model the session runs
on. Where that is a Vendor-backed Alias, the row leaves Domino. The creator never agreed to that.
What they agreed to, or declined, is **Sample rows**, and `SampleRows` says why in its own
docstring: putting rows in a model's context "belongs to the person who knows what is in the table
— never to a rule that inferred it would be helpful". The consent machinery exists. An on-demand
read walked past it.

So the read does not hand its rows to the assistant. **The tool writes the Artifact itself**, and
the assistant is handed a receipt: the columns, the row count, and the path. The person sees the
real rows in the card the Workbench renders. The model never does.

"Show me one sample conversation" therefore needs no consent at all, because nothing leaves.

An assistant that must read the values — "does this column look clean?" — gets them only where the
creator has already shared **that** table, and the record of that is `.sage/samples.json`, keyed by
Binding and table name. No second door is opened beside it (ADR-0021), and it fails closed: an
absent or unreadable record is no shared tables, which is what `parse_samples` already does.

Note for a later reader: the `sensitive` flag this ADR was first drafted against is **gone** from
that file — `render_samples` says leftover keys "are ignored on read". The live fact is the share
itself, which is a better gate anyway, because it is per table rather than per session.

This also makes the Artifact load-bearing rather than decorative. It is not where the answer is
filed; it is the channel that keeps the rows out of the model.

## Why the assistant gets a tool here, and not in ADR-0038

ADR-0038 is emphatic that the assistant gets **no new tool** for finding a table, because an
assistant holding `find_and_bind_table` would infer a Binding, which ADR-0010 forbids.

That reasoning does not carry, because the two acts differ in what they leave behind. Choosing a
table **declares** something a published app then depends on, so it must be a person's click. A
Live read declares nothing. It reads, it answers, and the Binding it read through is one the person
already made. There is no inference to protect against, so the argument for withholding the tool
has nothing to bite on.

Two tools, not one and not five: one over tabular stores and one over files (list and read). The
shapes genuinely differ, and a file needs a listing verb SQL has no use for. `assets.walk_files`
already returns the truncation-aware listing the second one wants.

The tabular one takes **a table and a limit, not SQL**. `provider.sample_rows` already exists — it
is described there as "the only method that reads any [rows]" — and it builds the statement through
the connector's own dialect with every identifier quoted. So the assistant names the target and
Sage writes the statement, which removes a SQL surface rather than adding one. The cost is that
this slice cannot filter: "conversations from March" needs a predicate nothing here composes. That
is a known limit, not an oversight.

## What it may reach

**Anything the Conversation or the current Built App already names** — a Binding, or a Session
context chip. One rule over five kinds: Data Source, Dataset, Attachment, Upload, Artifact.

Never the **Working set**, which ADR-0020 fixed as orientation and never context. Never a **Model
API** or an **LLM Alias**: those are called rather than read, a call costs money and need not
repeat, and reading is what this is for.

## Where it stops

A Data Source read works because the store does the arithmetic. A file read returns bytes, and the
assistant must do the arithmetic itself — fine for one row of a CSV, hopeless for a 200MB parquet.

So the line is drawn at reading. A Live read answers what a thing **is** and **holds**: columns,
types, a few rows, the file list, sizes. A question that needs the numbers computed belongs to
**Chat**, which exists for exactly that, and Sage offers the switch rather than stopping.

This keeps the guarantee structural. No shell means no hole to police — and there is a hole to
police: `shim/chat_paths.py` gates **write and edit tool names only**, so bash is already ungated
in Chat today. Extending that shape to Build would have spread it.

Accept the cost with open eyes: Ask will still sometimes say it cannot answer here. The difference
from the transcript above is that it names the next step and can take it for you.

## The transport, verified

Not inferred from the published schema. The pinned `opencode-ai@1.18.4` was installed and pointed at
this handler: it reports `sage-live-read connected`, and the exchange is `initialize` (it asks for
`2025-11-25`, accepts `2025-06-18`), `notifications/initialized`, then `tools/list`, which returns
both tools. It also opens a `GET` for the optional server-to-client stream and takes a 405 for it
without complaint.

## What lands, and where

The same directory as every other Artifact, `examples/<conversationId>/`, and **committed**, like
every other Artifact.

The obvious objection is `.sage/samples.json`, which is gitignored with the comment *"Real rows from
a Data Source... Never committed"*. That rule does not transfer. Its stated reason is that the rest
of `.sage/` rides into the published app's container, and Artifacts never reach the container — the
react-vite template ignores the `/examples` symlink for exactly that reason. ADR-0006 names
`*.table.json` explicitly, and Chat writes live-derived rows there today, so ignoring only these
would fork one term into two behaviours for no gain.

The statement is saved beside the result as `<slug>.sql`. It makes the read reproducible, and it
gives a person who liked that read something to hand to Build — the honest road from a Live read to
a **Named query**.

## Caps, and everywhere

Every result carries a `truncated` flag and the cap it hit, on ADR-0029's pattern, where truncation
is a fact the caller reads rather than a silence. 500 rows for a table, matching Chat. The card says
it in words — "500 of 12,431 rows" — and the assistant is told, so it never calls a slice "the data".

Within a turn the read is cached; across turns it is not. A person asking twice is asking whether
something changed, and a cache there would make the word *Live* a lie.

It is available in **all four Build modes and in Chat**. This is not a read-only-mode feature: it is
how Sage reads, and the read-only modes are only where it was missing. Implement has a shell, but
gains the same thing everything else gains — rows that reach the card without passing through
Recall. Chat gains speed: it answers `LIMIT 1` today by starting Python, importing pandas, writing a
file and reading it back. Chat keeps its shell for the analysis the tool cannot do.

## Refusals

Three cases now refuse: nothing in range, a question that needs compute, and a store or file the
platform will not open. Values simply do not appear where the creator never shared them, which is a
quieter thing than a refusal and needs no sentence of its own. **Every refusal names the missing
thing and offers the act that fixes it** — nothing in range hands over the Candidate picker from ADR-0038, compute offers the switch to
Chat, and sensitivity names the model rather than the rule.

None of them names the mechanism. Both templates already forbid saying "blocked" or "unable" or
naming a tool, so the transcript at the top of this document was **not** a rule that was missing —
it was the agent obeying one. The Build template lets it name something the user would supply: "a
table or file that isn't in the project is a fact about the project, not a capability you lack."
With no way to read, it reached for that escape and applied it to data that was present.

So the rule that was actually missing is the opposite one, and it is now in the template: a bound
Data Source or Dataset is **not** missing, and an agent that can read one must read it rather than
describe it as absent.
