---
status: accepted
revises: ADR-0014 (the noun-key rule reads names, not tokens; `Hosted GenAI Endpoint` was wrongly listed as needing no key), one consequence of ADR-0025 (the gap it recorded, and its estimate of what closing it would cost)
---

# The glossary holds names and words, and only names owe a key

[ADR-0014](0014-the-overlay-renames-prose-not-identifiers.md) says a `CONTEXT.md` term gets a
noun key **if and only if it appears in a user-visible string**, and has the lint compute that
rather than a person maintaining a list. The lint implemented the narrower half: it read a
marked position's *tokens*, so `{binding}` without a key failed and a bare `Binding` written
out in prose passed. [ADR-0025](0025-the-app-section-groups-by-relationship-never-by-type.md)
met that gap from the other side — two of its four label sites were already inside
`SW.brand.text` and said `Bindings` anyway — and recorded it open.

Closing it needs one thing ADR-0014 does not have: a way to tell which glossary entries the
rule is even about.

## The rule cannot be applied to the glossary as it stands

`CONTEXT.md` is one list doing two jobs.

Most entries name a thing a partner may rename. `Dataset`, `LLM Gateway`, `Project`, `Scope`:
each is a word on screen that a pack must be able to replace, and each is exactly what
ADR-0014's rule is for.

Six are not that at all. `Remove`, `Try again`, `Build this again`, `Use in this chat`,
`Stop using here`, `Sovereign`. These are in the glossary because the project had to settle
what they mean and which near-synonyms are banned — `Remove` carries a whole `_Avoid_` list
about not borrowing the verb for a cheaper act — but they are ordinary English doing ordinary
work. A pack that renamed `Remove` would not translate the sentence *"Remove it, or bind
another"*; it would break it.

Read *iff* over the whole file and it demands a `remove` noun key. Read it over none of the
file and it demands nothing, which is where the lint already was.

**Decision: each `CONTEXT.md` entry carries a `_Kind_:` line saying `name` or `word`, and only
a `name` owes a noun key when copy says it.** 50 entries today: 44 names, 6 words.

## Why the marker lives in the glossary, per entry

The alternatives were a list in the lint and a convention over the pack.

A list in the lint is the thing ADR-0014 spent a paragraph refusing. The whole reason the
noun-key set is computed is that a maintained list drifts from the copy; a maintained
*exemption* list drifts the same way and is worse, because drift there fails silently open.

The pack cannot answer it either. Whether `Remove` is a renameable name is a fact about the
domain, and the pack is a partner's answer sheet, not the question. A term that is a name owes
a key whether any pack has grown one yet — that is the debt the lint exists to name.

So it goes next to the meaning, in the file whose only job is meaning, one line per entry
beside the `_Avoid_:` block it already carries. `_Kind_: name` is a claim about the term, in
the place a reader is already looking to find out what the term is.

## An unmarked entry is read as a name, and reported

The two ways of being wrong are not symmetric. Guess `name` for something that is a word and
the cost is one token somebody writes and a sentence that reads slightly stiffly. Guess `word`
for something that is a name and the cost is a brand name shipped bare to a partner's
customer — the exact failure ADR-0014 exists to prevent.

**So an unmarked entry is held to the stricter rule.** It is also reported, as
`unmarked-term`, because a default nobody is told about is a default people come to rely on.
The glossary carries no unmarked entry today, and the test that says so is what keeps the
fail-closed path a safety net rather than the way the file is kept.

## What the widened lint costs, measured

ADR-0025 guessed that closing this "would make the lint markedly harder to live with". **That
was wrong, and this is the correction.** Two sites needed anything but a mechanical token
swap, and both were the same word.

Run over the tree, the widened rule named 7 names across 26 marked positions. Each was already
reaching a person as a bare word, so each already owed a key under ADR-0014's own rule; the
widening made the debt visible rather than creating it. All seven got keys —
`llmGateway`, `hostedGenaiEndpoint`, `project`, `resource`, `scope`, `chat`, `turn` — taking
the pack from 6 nouns to 13.

**`Hosted GenAI Endpoint` is one of them, and ADR-0014 names it as a term that never gets a
key.** It sat in five strings a person reads, in `resources/preflight.py`. Being named nowhere
in copy is what earns a term that exemption, and that is a fact about the copy rather than
about the term, so it is read off the copy now. `AI Gateway` and `Domino Artifacts` still
qualify.

`sharedCredential` was added and then taken out again. Every phrasing that kept the noun in
`publish_guard.py:210` needed the article `a`, and ADR-0014 refuses an article engine and
rewords instead; so the copy says *"whose credential is shared"*, the term reaches no screen,
and it owes nothing.

## The plural is naive on purpose

`Resources` was the shape of the whole count: five hits, not one of them singular. The
detector matches a name in the glossary's own spelling and that spelling plus `s`.

**There is still no pluralisation engine, and this is not the beginning of one.** ADR-0014
refused one and put two forms in the pack instead. This is a detector: it decides whether to
raise a finding, and the moment the key exists the real plural lives in the pack and
`brand.text` is what reads it. A naive `+s` that over-matches costs a false finding somebody
reads and dismisses; under-matching costs a shipped leak.

## A term is classified, an occurrence is not

`_Kind_` is a line on a glossary entry, so it says what the *term* is. The check matches a
whole word wherever it appears, and cannot tell a noun from a verb spelled the same way.

Two real collisions turned up. `Turn` is a name — a Turn is a thing Sage runs — and
`shell.js` said *"Turn a plan into a working app"*, the English verb. `Scope` is a name, and
`template/react-vite/AGENTS.md` said *"Scope these to the screens…"*.

**Both times the copy moved, not the rule.** *"Go from a plan to a working app"* and *"Limit
these to…"*. Exempting an occurrence would need a per-site marker, which is a comment nobody
maintains next to a string somebody else later edits; and both rewrites read at least as well
as what they replaced. If a collision ever turns up where the rewrite is genuinely worse, that
is the report that earns the mechanism.

## Two entries had to be split before the rule could be stated

**`Asset` was doing two jobs.** It was defined as *"a Domino Dataset mounted into the project
container"* — a concrete thing — while the rest of the glossary used it as the category a
Dataset belongs to, as against a Resource. One entry cannot be marked once for both: the
category word is near-internal, and `Dataset` is a Domino name a pack must rename. So there
are two entries now. The category keeps `Asset` and says outright that copy a person reads
says `Dataset`; `Dataset` is the thing, and has the noun key.

**`Use in this chat` and `Stop using here` shared one heading.** They were written
`**Use in this chat** / **Stop using here**:`, and the glossary parser's `.+?` ran straight
across the ` / ` and produced one junk term out of two real ones. They are now two headings.
The parser was not changed: one heading per entry is what makes an entry addressable at all,
and every other entry in the file already obeyed it.

## Consequences

- **The lint gains two rules.** `unkeyed-name` fires when a marked position spells out a
  glossary name the pack cannot rename; `unmarked-term` fires on a glossary entry with no
  `_Kind_:` line. Both are in `sage/tools/brand_lint.py`, both are tested in
  `tests/test_the_lint_over_marked_positions.py`.

- **The advice offers three exits, and the order matters.** Rewording leads, because it is
  what the glossary usually already asks for: ADR-0025 settled that `Binding` and `Attachment`
  are named on screen by what they do for the app and never by the word, so for those two a
  key would be the wrong fix rather than the missing one. Then a noun key, then marking the
  entry a word. A refusal with one exit gets the wrong fix applied.

- **Both rules run in one pass over one alternation.** `Resource Browser` has no key and
  `Resource` has one. Two passes in either order would let the shorter phrase match first and
  advise somebody to write `{resource} Browser`. The longest-first ordering the existing lint
  already relied on only holds if every phrase is in the same regex.

- **Seven keys mean seven more words on the paranoid pack's forbidden list.** Adding a key
  without adding it to `tests/brand_coverage.toml` would leave a token that nothing proves ever
  resolves, which is the one thing that file is for. Adding them failed four surfaces on six
  hits: four were real leaks — an agent prompt, two `AGENTS.md` templates, and the shell's own
  mode-tab and dock-tab DOM — and two were the verb collisions above.

- **A key lives in two places, and the second one is easy to miss.** `store.js` carries
  `BRAND_DEFAULT`, the client-side copy of the pack's documented defaults, because the shell paints
  before `GET /api/brand` answers. A key added on the server and not there renders as the literal
  `{project}` for the width of that window. Nothing catches it — the paranoid pack boots on a
  sentinel pack, so the fallback table is never the one in use — and it was two rail labels that
  said so. The two tables are one fact in two files by design (the alternative is the shell
  painting unbranded), so the only guard is remembering, and this bullet is the reminder.

- **Four of those leaks were on surfaces the lint still cannot see**, which is ADR-0025's
  other gap and it stays open. `shell.js` holds bare literals like `label: 'Build'` that reach
  a person through a render the lint reads nothing of. The paranoid pack caught these because
  it reads the rendered tree; a name that is bare *and* on an unwatched surface is still
  invisible to the lint, and the fix for that is a wider set of marked positions, not a wider
  set of rules.

- **`CONTEXT.md` stays a glossary.** `_Kind_:` says what kind of word an entry is, which is
  the same class of statement as `_Avoid_:` and no more of an implementation detail. Nothing
  about the pack, the lint or a key appears in the file.
