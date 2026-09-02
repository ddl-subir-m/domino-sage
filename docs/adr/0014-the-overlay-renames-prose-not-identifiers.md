---
status: accepted
---

# The overlay renames prose, never identifiers

Sage v1 shipped a brand pack that replaces the chrome and the speaking name (`backend/sage/orchestrator/brand.py`, `docs/workbench/brand.md`). It covers the logo, the theme colours, the document title and about fourteen strings. It leaves roughly 137 user-visible strings that still say **Sage** or **Domino**, most of them in the backend, where the Workbench renders them as `err.message` and `detail` — so a partner meets the real names the first time anything fails.

This decision extends the pack to a true OEM overlay: a partner replaces every name and image a person sees, **Domino included**. One rule decides each case, and it is three-way rather than two:

**Prose re-brands. Identifiers and paths stay. Prose that cannot be per-pack is de-branded once.**

The third arm is the one that is easy to miss. A git commit message is prose a person reads, but history is immutable and a pack can change, so `sage: ` cannot be re-branded — it is stripped of the name instead, permanently, for every pack including Domino's. The same is true of the template's `src/sage*.ts` file names, which are imported by code Sage does not own.

## "Domino" is four different words

The 207 Python lines and 24 JS/HTML lines that name the platform do not share a treatment, because the word is playing four different parts:

| Role | Example | Treatment |
|---|---|---|
| **Actor** — the platform did something | `assets/provider.py:274` *"The Domino API answered 500 at /api/datasetrw/v2/datasets."* | `platformName` |
| **Destination** — go to a page Sage does not own | `components/composer.js:258` `'Browse Domino…'`; `provision/domino.py:516` *"Manage settings in Domino"* | `platformName`, but see below |
| **Noun** — a thing the platform provisions | `components/resource-catalog.js:13` `'Datasets'` | the noun map |
| **Literal** — load-bearing | `DOMINO_API_HOST`, `/api/datasetrw/v2/datasets`, `/u/{owner}/{proj}/apps/…`, `github.com` | never |

The single sentence at `provider.py:274` contains three of the four. A treatment that cannot tell them apart within one string is not a treatment.

## Destination strings presuppose the platform's own whitelabel

About twenty strings send a person out of Sage and into a Domino page: *"Manage settings in Domino"*, *"Start it in Domino"*, `'Search everything in Domino…'`, *"no HTTPS Git credential for github.com in your Domino account"*, plus real deep links built by `provision/domino.py:544`. Sage renames the word; it cannot rename the page.

**`platformName` governs actor and destination strings together, and the pack documents the precondition: set the platform's own `/admin/whitelabel` first, or leave `platformName` as Domino.** A partner who renames only Sage builds a dead end — copy that says "Acme" pointing at a page that says "Domino" — which the design system classes as a High-severity failure.

Splitting this into two keys, one for actor strings and one for destination strings, was rejected. It takes a single fact — *is the platform rebranded?* — and stores it twice, so the two copies drift and no one can reason about which is right.

## Substitution happens where the string is written

**Every user-visible string is a template resolved at read time.** A new string is branded because whoever wrote it wrote it that way, not because a pipeline caught it.

A response-time filter over outgoing `detail` and JSON payloads was rejected, and not for cost. **By the time a filter sees the bytes, provenance is gone.** It cannot distinguish Sage's word *Domino* from a Dataset a user named `domino-demo`, a column called `domino_id`, a Data Source named after the company, or a line of the user's own SQL echoed back inside an error. `brand.md` v1 already forbade rewriting Resource names; a response-time filter is structurally incapable of obeying that rule. A build-time sweep shares the blindness and adds staleness.

The objection to author-time substitution is that someone will forget. That is what the two tests below are for.

## Nouns live in the pack, and the lint decides which

Domino's whitelabel renames its own nouns, and Sage must agree with it or the rail and the page it links to will disagree. Sage cannot read those names — **no API exposes the platform's nomenclature to a Workspace tool, and nothing is injected into the frame** — so the nouns go in the pack, as `{singular, plural}` pairs.

The nouns are woven into sentences, not confined to labels: `resource-panel.js:688` *"Dataset contents live under the Dataset."*, `resource-tree.js:147` *"No files in this Dataset."*, `resource-catalog.js:13` `'Datasets'`. Hence two forms per noun and no pluralisation engine. **There is no article engine either**: where copy needs `a`/`an`, the copy is reworded. Grammar machinery for six nouns is the wrong trade.

**A `CONTEXT.md` term gets a noun key if and only if it appears in a user-visible string**, and the lint computes that rather than a person maintaining a list.

> **Revised by [ADR-0026](0026-the-glossary-holds-names-and-words-and-only-names-owe-a-key.md).** The rule stands; the reading of "appears in" was too narrow. The lint read a marked position's tokens, so a bare glossary noun written out in prose passed. `CONTEXT.md` now marks each entry `_Kind_: name` or `_Kind_: word`, and only a name is held to the rule — because six entries here (`Remove`, `Try again`, and four more) are ordinary English a pack must not touch. `Hosted GenAI Endpoint`, named below as a term that never gets a key, was in five strings a person reads and has one now. This is the only version of "derived from the glossary" that stays true: a new glossary term used in the UI fails the lint until it has a key, a glossary term that exists only to disambiguate — `AI Gateway`, `Domino Artifacts`, `Hosted GenAI Endpoint` — never gets one, and the list cannot drift from what the UI actually says. It also settles a question the alternative leaves open: Sage's own coinages, `Built App` and `Gallery` among them, are names a person sees, so they are replaceable too.

**Text Sage did not write keeps its words.** A Domino error body passed through, a Resource name the user chose, a URL path segment: none of these are rewritten. A screen will therefore sometimes show both vocabularies at once. That is correct, and it is made to read as correct by rendering a passed-through platform error **as a quotation** — its own visually marked block, following the design system's existing split between system errors and raw output.

## What a partner's customer reads

The Built App repo is a surface. ADR-0006 commits `.sage/history.jsonl`, `.sage/threads/…` and the rendered `.sage/history.md` into it, and every build commit reads `sage: <the user's prompt>` (`orchestrator/service.py:6219`, `:6301`, `:6328`, and `provision/seed.py:63`).

**`AGENTS.md` re-brands.** It is generated per project, so it is tokenised like every other prompt, through `apply_agent_voice()`.

**The template's `src/sage*.ts` names de-brand, in the template only.** New apps get neutral names. Existing apps keep what they have, and Sage resolves which name it is looking at through **one** resolver rather than 49 scattered conditionals — the names are load-bearing in 49 places, including `resources/pinned_model.py:30-31` and `resources/pinned_model_api.py:27-28`, which write to them by literal path, and generated instruction text such as `resources/bound_schema.py:312`. Migrating existing apps was rejected: the imports live in code the agent wrote, in files Sage does not own, and rewriting a user's source to fix a cosmetic name is the worst trade available.

**The commit prefix becomes `build: `.** Nothing parses `sage: ` — there is no reader anywhere in the tree — and the prefix does real work marking a machine commit in a repo a person also commits to, so it is kept and de-named. `seed.py:63` becomes `"Initial commit"`.

**Built App chrome stays out.** That is the user's product, not ours.

## What stays

`.sage/` as a path, the agent ids `sage-chat` and `sage-plan`, and the `sageBuilder` key in `environment/pluggable-tools.yaml` are **identifiers**, in the same class as `DOMINO_API_HOST`. They are not renamed. The `title: "Sage"` on that pluggable tool is prose and re-brands on the next Environment rebuild. Per-organisation packs remain out: one pack per process.

## Proving nothing escapes

A grep over the source is the wrong test — it fails whenever somebody writes a code comment. Two tests replace it, with different jobs, and **both block CI**:

**A lint over marked positions.** Not a scan of files but of call sites: `detail=` on `HTTPException`, the Python substitution helper, `SW.brand.*` in JS. No bare `Sage`, `Domino`, `ML Studio` or unmapped glossary noun may appear in a string literal at one of those positions. A comment is invisible to it, which is the property that makes it liveable.

**A paranoid pack.** Boot with nonsense sentinels — `ZZQQ-PRODUCT`, `ZZQQ-PLATFORM` — and assert no forbidden word reaches a person, over a **checked-in coverage list**: `GET /api/brand`, the templated entry pages, the shell's rendered DOM, the `HTTPException` details a module under test can raise, the generated `AGENTS.md`, the OpenCode system prompt, and the commit message. Adding a surface without adding it to the list is itself the failure. A coverage list that is aspirational is not a test.

The lint catches the string written next week; the paranoid pack catches a token that is never resolved. Neither substitutes for the other.

## There is no attribution floor

A partner may remove every Domino mark. Removing the trademark from Sage's UI is signed off, so `platformName` ships defaulted and ungated — there is no approval gate on the capability and no mark that has to survive somewhere a person can reach.

**So attribution is not a brand surface at all, and the pack has no key for it.** What remains is an ordinary licensing obligation: the third-party licence text that must travel with what we distribute. It is satisfied by a file in the Environment image, so it is a packaging chore rather than something the Workbench renders. This repo currently ships none — there is no `NOTICE` or `LICENSE` anywhere outside `node_modules` and `.venv` — while `workbench/index.html:9-14` serves Inter from our own origin under the SIL OFL, which requires the licence to accompany the font. That gap predates the overlay and is not fixed by it.

**Built Apps carry their own.** The template's runtime dependencies — `react`, `react-dom`, `react-router-dom`, `recharts`, `date-fns`, `lucide-react` — land in a repo the user owns and publishes. The obligation follows the distribution, so it is theirs; Sage generates no licence file into a new app and injects no link into its chrome.

## Considered options

**A response-time filter, so new strings are caught automatically.** Rejected above: it cannot see provenance, so it corrupts Resource names, user SQL and pass-through error text. This is the single most attractive wrong answer here, because it appears to solve the maintenance problem that author-time substitution leaves to a lint.

**Reading the platform's nomenclature instead of copying it.** Preferred on the merits and unavailable: nothing exposes it to a Workspace tool. Recorded below as a cost rather than pretended away.

**A `platformName` fallback chain of platform → pack → defaults.** Rejected. The fallback for a local or off-platform run is the built-in defaults; falling back to partner-set values would put one fact in two places a partner can edit.

**Renaming `.sage/`, `sage-chat` and `sage-plan`.** Rejected. They are identifiers, and renaming the path breaks every workspace that already exists for a string that reads as a path, not as a name.

**Migrating existing Built Apps to the neutral file names.** Rejected above.

**A Notices surface in the Workbench whose existence the pack cannot switch off.** Designed, then rejected once the contractual answer came back: there is no mark that must survive, so a surface defending one defends nothing. The licence obligation it would have carried is met by a file.

**A `version` field in the pack.** Rejected. Every key is optional with a documented default and unknown keys are ignored but logged at startup, which is forward compatibility without a migration story we do not have.

## Consequences

**The nouns live in two places and will drift.** A partner sets them once in Domino's admin UI and again in a `brand.json` baked into an Environment image. Nothing reconciles the two, and the failure is silent — Sage says one word, the page it just linked to says another. This is accepted only because no API exists. If one appears, the pack's noun keys should be deleted, not supplemented.

**Old transcripts keep their old words.** `.sage/history.jsonl` holds what the agent literally said; `.sage/history.md` is regenerated from it (ADR-0006), and regeneration reproduces speech. A pack change cannot re-brand a conversation that already happened, and rewriting one would falsify a record that is committed to a repo.

**`platformName` is only correct if the platform's whitelabel is set.** Sage cannot verify this and does not try. It is stated as a precondition in `brand.md`, and a partner who ignores it gets copy pointing at pages that contradict it.

**A partner with a typo gets a warning, not a refusal.** A noun value containing `_` or starting lowercase logs at startup and is used anyway; an unknown key is ignored and logged. A brand pack must not be able to stop the product booting.

**Two entry pages are templated, not one.** `orchestrator/app.py:472` serves `index.html` or `door.html` from the same route, so substituting the title and favicon there covers both and removes the flash in which the browser paints the unbranded name.

**The partner's image directory is a mount point with a security boundary.** `/opt/sage/brand/` is served, `/opt/sage/` is not — that directory holds `opencode.json` and its gateway configuration. The route allows `.svg` and `.png` and serves nothing else: no directory listing, no fallthrough. Remote URLs are refused, because they break an air-gapped install and hand a partner's CDN a log of every user's session.
