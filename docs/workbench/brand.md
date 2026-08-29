# Workbench brand (white-label)

An OEM partner replaces every name and image a person sees, **Domino included**. The **default pack
is Domino**: product switcher **AI Workbench**, agent **Sage**, peer product **ML Studio**, Domino
logo, Domino nouns. Internals stay Sage.

The rule behind every line below is [ADR-0014](../adr/0014-the-overlay-renames-prose-not-identifiers.md):
**prose re-brands, identifiers and paths stay, and prose that cannot be per-pack is de-branded once.**

## Status

**This file is the design. Most of it is not built yet.**

Shipped in v1 (`40ecb14`): the pack loader and its fallbacks, `GET /api/brand`, `productName`,
`assistantName`, `pageTitle`, `logoUrl`, `logoAlt`, `colors`, the antd theme, about fourteen UI
strings through `SW.brand.*`, and `apply_voice()` / `apply_agent_voice()`.

Shipped since: the author-time substitution helper, `brand.text()` and `SW.brand.text()`.

Everything else below is designed and unbuilt. Sections carry the marker.

## Locked

- **Audience:** OEM / partner overlay. Unset pack → Domino. Not per-organization on one process.
- **Precondition:** `platformName` assumes the partner has set Domino's own `/admin/whitelabel`.
  Sage renames the word; it cannot rename the page it links you to. Leave `platformName` as Domino
  if the platform is not rebranded.
- **Names (Domino default):** keep the split. Top bar = `AI Workbench`. Thread byline and “added
  this” = `Sage`. Page title = `Sage Workspace`. Platform = `Domino`.
- **Substitution is author-time.** Every user-visible string is a template resolved when read. Never
  a filter over outgoing responses — a filter cannot tell our word from a Resource the user named.

## Substitution helper — built

`sage.orchestrator.brand.text()` on the backend and `SW.brand.text()` in the Workbench. Both resolve
`{token}` against the resolved pack at the moment the string is read.

```python
brand.text("Ask {assistantName} in {productName}.")
brand.text("{productName} answered {code}.", code=r.status_code)
```

```js
SW.brand.text('Ask {assistantName} in {productName}.');
```

- A token is a pack key. Keyword values fill the rest of the sentence, so the whole sentence stays
  one literal the lint can read. A substituted value is **not scanned again**, so a Resource name
  carrying braces passes through untouched.
- An **unknown token is left as written**, never raised. A typo must not stop the Workbench booting,
  and a passed-through platform error can carry braces of its own.
- Nothing is migrated by the helper existing. The roughly 137 strings that still name Sage or Domino
  move in their own batches.

## Pack — partly built

JSON. Missing or unreadable → the Domino defaults below. Every key optional. **No `version` field.**
Unknown keys are **ignored but logged at startup**, so a typo is findable.

```json
{
  "productName": "AI Workbench",
  "assistantName": "Sage",
  "platformName": "Domino",
  "pageTitle": "Sage Workspace",
  "peerProducts": [{ "key": "studio", "label": "ML Studio" }],
  "logoUrl": "./img/domino-logo.svg",
  "logoAlt": "Domino",
  "faviconUrl": "./img/domino-favicon.svg",
  "nouns": {
    "dataset":    { "singular": "Dataset",     "plural": "Datasets" },
    "dataSource": { "singular": "Data Source", "plural": "Data Sources" }
  },
  "colors": {
    "primary": "#543FDE",
    "primaryDark": "#311EAE",
    "primaryLight": "#EEEBFC"
  }
}
```

**Not built:** `platformName`, `peerProducts`, `faviconUrl`, `nouns`, the unknown-key log, and
the Title Case warning. There is also no default favicon asset yet — it has to be drawn.

An OEM typically sets `productName` and `assistantName` to the same string. `assistantName` omitted
falls back to `productName`. Colors omitted keep the purple tokens.

`peerProducts` is a **list**, not a name, so a partner with no second product sets `[]` and the
switcher collapses to a plain product label. A switcher with one item is not a switcher.

**Load order:** `/opt/sage/brand.json` (baked into the Environment) → `SAGE_BRAND_FILE` if set.
One pack per process. Not `.sage/brand.json` (that would be per project).

**API:** `GET /api/brand` returns the resolved pack.

**Entry pages are templated server-side — not built.** Today `index.html` carries a static
`<title>Sage Workspace</title>` and no `<link rel="icon">` at all.
`orchestrator/app.py:472` serves `index.html` or `door.html` from one route; substitute both
there. Do not
patch them from JS on boot — the browser paints the unbranded name first.

## Nouns — not built

Domino's own whitelabel renames its nouns, and nothing exposes that vocabulary to a Workspace tool,
so the pack carries a copy. **This will drift.** It is accepted only until an API exists.

- `{singular, plural}` per noun. **No pluralization engine, no article engine.** Where copy needs
  `a`/`an`, reword the copy.
- **Title Case.** A value containing `_` or starting lowercase logs a warning and is used anyway.
  `"No files in this xyz_dataset."` reads as a leaked code identifier, not a product term.
- **A `CONTEXT.md` term gets a key iff it appears in a user-visible string.** The lint computes this.
  A new glossary term used in the UI fails the lint until it has a key; a glossary-only
  disambiguator (`AI Gateway`, `Domino Artifacts`) never gets one. Sage's own coinages —
  `Built App`, `Gallery` — are names a person sees, so they get keys too.
- **Never rename a noun inside text we did not write.** A Domino error body, a Resource name the
  user chose, a URL path segment. Render a passed-through platform error **as a quotation**: its own
  marked block, so two vocabularies on one screen read as attribution rather than a bug.

## Images — not built

- **Mount `/opt/sage/brand/`. Never `/opt/sage/`** — that holds `opencode.json` and its gateway
  configuration. A static mount one level too high publishes it.
- Allowlist `.svg` and `.png`. No directory listing, no fallthrough.
- **No remote URLs.** They break an air-gapped install and hand the partner's CDN a log of every
  user's session.
- Favicon is SVG. Unset → the Domino default, same fallback rule as every other key.

## Voice — partly built

Every string a person reads that names us, including agent system prompts.

Substitute when installing OpenCode config (`_install_opencode_config`) and when writing
`.sage/chat-work/AGENTS.md`. Restart OpenCode after a pack change so the prompt reloads.

The agent **speaks** the mapped nouns and **understands** the defaults: the prompt states that the
default nouns are synonyms it must recognise but not use. The user will type "dataset", and the code
the agent reads says `runQuery` and `sageQuery.ts`.

**Old transcripts keep their old words.** `.sage/history.md` is regenerated from
`.sage/history.jsonl` (ADR-0006), and regeneration reproduces what was actually said. A pack change
cannot re-brand a conversation that already happened, and rewriting one would falsify a committed
record.

## Built Apps — not built

The repo is a surface — the partner's own customer can read it.

- **`AGENTS.md` re-brands.** Generated per project, tokenised like every other prompt.
- **`src/sage*.ts` de-brand, template only.** New apps get neutral names; existing apps keep theirs.
  One resolver answers "what is this app's helper called", not 49 conditionals. The names are
  load-bearing in 49 places, including `pinned_model.py:30-31` and `pinned_model_api.py:27-28`.
- **Commit prefix is `build: `.** Nothing parses it. `seed.py:63` → `"Initial commit"`.
- **Built App chrome stays out.** That is the user's product.

## Proof — not built

Two tests, different jobs, both block CI. A grep over the source is not one of them — it breaks the
moment somebody writes a code comment.

- **Lint over marked positions.** `detail=` on `HTTPException`, the Python substitution helper,
  `SW.brand.*` in JS. No bare `Sage` / `Domino` / `ML Studio` / unmapped noun in a string literal at
  one of those positions. Comments are invisible to it.
- **Paranoid pack.** Boot with `ZZQQ-PRODUCT` / `ZZQQ-PLATFORM` sentinels and assert nothing leaks
  across a **checked-in coverage list**: `GET /api/brand`, both templated entry pages, the shell's
  rendered DOM, `HTTPException` details, the generated `AGENTS.md`, the OpenCode system prompt, the
  commit message. Adding a surface without listing it is itself the failure.

## Attribution

**No floor.** A partner may remove every Domino mark; the trademark removal is signed off, so
`platformName` ships defaulted and ungated. **The pack has no attribution key and the Workbench
renders no Notices surface.**

What is left is not brand. Third-party licence text has to travel with what we distribute, which a
file in the Environment image satisfies. **Not built, and not this file's problem** — the repo ships
no `NOTICE` or `LICENSE` today while `index.html:9-14` serves Inter under the SIL OFL. Track it as a
packaging bug; it predates the overlay.

Built Apps carry their own. Their dependencies ship in a repo the user owns, so the obligation is
the user's. Sage writes no licence file into a new app and injects nothing into its chrome.

## Out of scope

- Paths `.sage/`, agent ids `sage-chat` / `sage-plan`, HTML `<!-- sage:… -->` — identifiers, same
  class as `DOMINO_API_HOST`
- The `sageBuilder` key in `environment/pluggable-tools.yaml`; its `title` is prose and re-brands on
  the next Environment rebuild
- Per-org packs
- Chrome of Built Apps this Workbench publishes
