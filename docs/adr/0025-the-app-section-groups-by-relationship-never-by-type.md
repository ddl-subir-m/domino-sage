---
status: accepted
---

# The app section groups by relationship, never by type

Build shows two lists, one above the other. `In <App name>` holds what one Built App records;
`In this project` holds the [[Working set]], already divided into Domino's own kinds — Data,
Language models, Predictive models, Agents, Skills, MCPs. Until now the upper list was headed
`Bindings` and `Attachments`: the only place either identifier reached a person, and against
`CONTEXT.md`'s own rule that a Binding is named on screen by what it does for the app.

**Decision**: the app's section is headed **"Needs to run"** and **"Files it carries"**, and it is
grouped by the app's *relationship* to a thing — never by the thing's kind.

> **The section moved in [ADR-0032](0032-the-panel-is-the-projects-one-list.md)**, from the resource
> panel to the App dependencies modal. Both labels come across intact, `— none` included. One thing
> is added there and is worth reading against "why not by kind" below: the rows carry the kind as an
> **icon**. The argument here is against type as an *axis* — a heading, a second list answering the
> same question the rail already answers — and it holds. It was never an argument for a row that
> cannot say what it is, and off the rail there is no rail below to say it for them.

**Why not by kind**: the rail below already answers *what kind is it*. Repeating that axis one
section up would make two lists that look the same and answer the same question, and it scales
backwards — an app holding four things would get four type headings over one row each, where the
row's own icon already carried its kind. Two axes, asked once each: the rail asks what a thing is,
the app section asks what this app does with it.

**Why these two words**:

- **"Connections"** was the obvious replacement and is refused. `connection` is
  [ADR-0001](0001-connection-over-extract-for-resources.md)'s word for how a Resource is reached
  rather than extracted, so the label would name a different idea than the list holds. It is on
  `Binding`'s `_Avoid_` list for that reason.
- **"Needs to run"** says what the app cannot do without them, which is the same fact the row
  already states from the other side as `Required by <app>`. It also survives its own empty state:
  `Needs to run — none` reads as *needs nothing*, which is true and useful.
- **"Files it carries"** keeps the word *file* where the other head drops the word *binding*,
  because the lists are not the same shape. One holds several kinds; this one holds exactly one, so
  naming it costs nothing and buys the answer someone arrives for —
  [ADR-0011](0011-removal-lives-with-the-list-that-owns-the-scope.md) already made
  `— none` load-bearing, and *"Files it carries — none"* answers *is my file in this app?* directly.
  `Bundled files` was refused: an Attachment is copied in, not built in.

The labels are deliberately not parallel. A pair that read as symmetric would claim the two lists
are the same shape, and they are not.

**Consequences**:

- **Neither term gets a brand-pack noun key, and that is the point.**
  [ADR-0014](0014-the-overlay-renames-prose-not-identifiers.md) gives a `CONTEXT.md` term a noun
  key *iff* a marked position names it. `Binding` and `Attachment` now name none, so they stay out
  of the pack — the same class as `AI Gateway` and `Domino Artifacts`. The lint's own
  `Binding` example keeps working precisely because the key never appears.
  (`Hosted GenAI Endpoint` stood in that list until
  [ADR-0026](0026-the-glossary-holds-names-and-words-and-only-names-owe-a-key.md) found it in five
  strings a person reads and gave it a key.)
- **The labels move to a marked position they were never at.** Four of them were bare literals
  passed to `appGroup` and `kindRow`, so the lint over marked positions could not see them at all:
  the words were user-visible and entirely unwatched, which is why they survived the v1 sweep.
  Neither new label carries a token, and both are routed through `SW.brand.text` anyway, so the
  position is what a later editor inherits.
- **Two more sites said the word, and one of them shows why the sweep missed it.** The `Use in
  <app>` tooltip and the delete-app confirmation both named `Bindings`, and the delete copy was
  already inside `SW.brand.text`. Being at a marked position did not save it, because **the lint
  owes a noun key only to a term written as a token** — `{binding}` fails, a bare `Bindings` in
  prose passes. ADR-0014 states the rule more broadly than that ("no bare … unmapped glossary noun
  may appear"), so the implementation is narrower than the decision it implements. That gap is
  **not closed here**: closing it means deciding whether every glossary term in prose — `Recall`,
  `Plan`, `Control`, `Scope` — is owed a pack key, which is a bigger question than this rename.
  Recorded so the next person to trust the lint knows what it does and does not check.

  > **Closed by [ADR-0026](0026-the-glossary-holds-names-and-words-and-only-names-owe-a-key.md).**
  > The bigger question had an answer: the glossary holds *names* and *words*, and only a name owes
  > a key. This paragraph also guessed that closing the gap "would make the lint markedly harder to
  > live with", and that was wrong — measured, it was 2 sites, both the word `Turns`.
- **The delete copy also stopped overclaiming.** It said the app's `Bindings` "are removed", which
  reads as though the Resources go too. They do not — a Binding is a grant, and ADR-0011 keeps the
  Resource in the Project to be picked again. It now says its *record* of what it needs to run.
- **The Build header and the panel must keep saying the same two words.** ADR-0011 already paid
  once for those two surfaces drifting into two answers to one question. They now share the labels
  as well as the empty sentence, and nothing but review enforces it.
