# Workbench brand (white-label)

An OEM partner replaces every name and image a person sees, **Domino included**. The **default pack
is Domino**: product switcher **AI Workbench**, agent **Sage**, peer product **ML Studio**, Domino
logo, Domino nouns. Internals stay Sage.

The rule behind every line below is [ADR-0014](../adr/0014-the-overlay-renames-prose-not-identifiers.md):
**prose re-brands, identifiers and paths stay, and prose that cannot be per-pack is de-branded once.**

## Status

**This file is the design, and it is now built.** The one exception is the packaging bug under
Attribution, which predates the overlay and is not brand work.

Shipped in v1 (`40ecb14`): the pack loader and its fallbacks, `GET /api/brand`, `productName`,
`assistantName`, `pageTitle`, `logoUrl`, `logoAlt`, `colors`, the antd theme, about fourteen UI
strings through `SW.brand.*`, and `apply_voice()` / `apply_agent_voice()`.

Shipped since: the author-time substitution helper, `brand.text()` and `SW.brand.text()`;
`platformName`; `nouns`; `faviconUrl` with the `/brand` image mount; and the lint over marked
positions.

Sections carry the marker. Anything still marked unbuilt is unbuilt on purpose, not pending.

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

## Pack — built

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

**Built:** `faviconUrl`, filled into `<link rel="icon">` on both entry pages by the route that
serves them, and validated where the pack is read — relative path on our own origin, `.svg` or
`.png`, no walking, no remote URL. Anything else is refused, logged once and falls back.
The default asset is `img/domino-favicon.svg`: the mark from `domino-logo.svg` with the wordmark
cropped off, on a ground in `colors.primary`. The wordmark could not be the default — 120x23 squeezed
into a 16px box is a smear — and the mark alone could not either, because its paths are white and a
light tab would show nothing. Regenerate it by cropping the logo rather than redrawing it.

An OEM typically sets `productName` and `assistantName` to the same string. `assistantName` omitted
falls back to `productName`. Colors omitted keep the purple tokens.

`platformName` names the platform under us in both the parts that word plays — **actor** (*"The
Domino API answered 500"*) and **destination** (*"Manage settings in Domino"*). One key, because
they are one fact: is the platform rebranded? It falls back to the built-in `Domino`, never to
`productName` — a chain would put that one fact in two places a partner can edit.

**Precondition: set the platform's own `/admin/whitelabel` first.** Sage renames the word; it
cannot rename the page it links you to. A partner who renames only Sage builds a dead end — copy
saying "Acme" pointing at a page saying "Domino". Leave `platformName` as `Domino` if the platform
is not rebranded. Sage cannot verify this and does not try.

`peerProducts` is a **list**, not a name, so a partner with no second product sets `[]` and the
switcher collapses to a plain product label. A switcher with one item is not a switcher.

**Load order:** `/opt/sage/brand.json` (baked into the Environment) → `SAGE_BRAND_FILE` if set.
One pack per process. Not `.sage/brand.json` (that would be per project).

**API:** `GET /api/brand` returns the resolved pack.

**Entry pages are templated server-side — built.** One route serves `index.html` or `door.html` and
runs the whole document through `brand.text()`, so the title, the icon and the door's logo are the
pack's before the browser paints. Not patched from JS on boot: that order shows the unbranded name
first, and the door is the first page a published App's viewer ever sees.

## Nouns — built

Domino's own whitelabel renames its nouns, and nothing exposes that vocabulary to a Workspace tool,
so the pack carries a copy. **This will drift.** It is accepted only until an API exists.

**Built:** the map, both forms through `brand.text()` / `SW.brand.text()`, the Title Case warning,
`nouns` on `GET /api/brand`, the lint below, and rendering a passed-through platform error as a
quotation.

The default keys are `dataset`, `dataSource`, `modelApi`, `llmAlias`, `builtApp` and `gallery`. A
key the pack invents is ignored — a token Sage never emits is not a rename. A key set to only one
of its two forms keeps the default for the other.

- `{singular, plural}` per noun, read as `{dataset}` and `{datasetPlural}`. **No pluralization
  engine, no article engine.** Where copy needs `a`/`an`, reword the copy.
- **Title Case.** A value containing `_` or starting lowercase logs a warning and is used anyway.
  `"No files in this xyz_dataset."` reads as a leaked code identifier, not a product term. The
  warning is made once per bad value: `load()` runs per request.
- **A `CONTEXT.md` term gets a key iff it appears in a user-visible string.** The lint computes this.
  A new glossary term used in the UI fails the lint until it has a key; a glossary-only
  disambiguator (`AI Gateway`, `Domino Artifacts`) never gets one. Sage's own coinages —
  `Built App`, `Gallery` — are names a person sees, so they get keys too.
- **Never rename a noun inside text we did not write.** A Domino error body, a Resource name the
  user chose, a URL path segment. Render a passed-through platform error **as a quotation**: its own
  marked block, so two vocabularies on one screen read as attribution rather than a bug.

## Images — built

Served at `/brand`, by `BrandImages` in `orchestrator/brand.py`. A pack names a partner's file as
`./brand/<file>.svg` — relative, because the platform serves the shell under a proxy prefix an
absolute path would walk out of. The Domino defaults are not here; they are the shell's own
assets, under `/img`.

- **Mount `/opt/sage/brand/`. Never `/opt/sage/`** — that holds `opencode.json` and its gateway
  configuration. A static mount one level too high publishes it.
- Allowlist `.svg` and `.png`. No directory listing, no fallthrough. The extension is checked
  before anything touches the filesystem, and a refused name answers exactly like an absent one.
- **No remote URLs.** They break an air-gapped install and hand the partner's CDN a log of every
  user's session.
- Favicon is SVG. Unset → the Domino default, same fallback rule as every other key.

## Voice — built

Every string a person reads that names us, including agent system prompts.

Substitute when installing OpenCode config (`_install_opencode_config`) and when writing
`.sage/chat-work/AGENTS.md`. Restart OpenCode after a pack change so the prompt reloads.

The agent **speaks** the mapped nouns and **understands** the defaults: the prompt states that the
default nouns are synonyms it must recognise but not use. The user will type "dataset", and the code
the agent reads says `runQuery` and `appQuery.ts`.

**Old transcripts keep their old words.** `.sage/history.md` is regenerated from
`.sage/history.jsonl` (ADR-0006), and regeneration reproduces what was actually said. A pack change
cannot re-brand a conversation that already happened, and rewriting one would falsify a committed
record.

## Built Apps — built

The repo is a surface — the partner's own customer can read it.

- **`AGENTS.md` re-brands.** Generated per project, tokenised like every other prompt.
- **`src/sage*.ts` de-brand, template only.** New apps get neutral names; existing apps keep theirs.
  One resolver answers "what is this app's helper called", not 49 conditionals. The names are
  load-bearing in 49 places, including `pinned_model.py:30-31` and `pinned_model_api.py:27-28`.
- **Commit prefix is `build: `.** Nothing parses it. `seed.py:63` → `"Initial commit"`.
- **Commit author is `agent <agent@localhost>`.** The fallback identity used when the repo has none
  configured, so it is the author line of every save the partner's own customer reads. Lowercase, so
  the case-sensitive scan never caught it; nothing resolves it, so it is not an identifier either.
  De-branded once for the same reason as the prefix beside it.
- **Built App chrome stays out.** That is the user's product.

## Proof — built

Two tests, different jobs, both in the suite. A grep over the source is not one of them — it breaks
the moment somebody writes a code comment.

There is no pipeline in this repo: no `.github/workflows`, nothing. "Blocks CI" means `make test`,
which is `uv run pytest -q` and picks both up from `testpaths`. If a literal gate is ever wanted, a
workflow file has to exist first — neither test can supply one.

- **Lint over marked positions — built.** `sage/tools/brand_lint.py`, run by
  `tests/test_the_lint_over_marked_positions.py` and by hand as `python -m sage.tools.brand_lint`.
  `detail=` on `HTTPException`, the Python substitution helper, `SW.brand.*` in JS. No bare `Sage` /
  `Domino` / `ML Studio` / unmapped noun in a string literal at one of those positions. Comments are
  invisible to it. The forbidden words are read out of `brand.DEFAULT`, and which `CONTEXT.md` terms
  owe a noun key is computed from the tokens the marked positions write — neither is a list anybody
  maintains. A new marked position goes into the lint, never into an exclusion list.
- **Paranoid pack — built.** Boots with `ZZQQ-PRODUCT` / `ZZQQ-PLATFORM` sentinels and asserts
  nothing leaks across a **checked-in coverage list**: `GET /api/brand`, both templated entry
  pages, the shell's rendered DOM, `HTTPException` details, the generated `AGENTS.md`, the OpenCode
  system prompt, the commit message. Adding a surface without listing it is itself the failure.

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
