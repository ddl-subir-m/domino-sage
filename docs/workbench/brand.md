# Workbench brand (white-label)

OEM can replace the chrome and the speaking name. The **default pack is Domino**: product switcher
**AI Workbench**, agent **Sage**, Domino logo. Internals stay Sage. The Environment tool tab stays
**Sage** (`sageBuilder` in `environment/pluggable-tools.yaml`).

## Locked

- **Audience:** OEM / partner overlay. Unset pack → Domino. Not per-organization on one process.
- **v1 surface:** chrome + voice. Not the workspace tool title.
- **Names (Domino default):** keep the split. Top bar = `AI Workbench`. Thread byline and “added
  this” = `Sage`. Page title = `Sage Workspace`.
- **Workspace tool:** leave labeled `Sage`. Changing it is an Environment rebuild, later.

## Pack

JSON. Missing or unreadable → the Domino defaults above.

```json
{
  "productName": "AI Workbench",
  "assistantName": "Sage",
  "pageTitle": "Sage Workspace",
  "logoUrl": "./img/domino-logo.svg",
  "logoAlt": "Domino",
  "colors": {
    "primary": "#543FDE",
    "primaryDark": "#311EAE",
    "primaryLight": "#EEEBFC"
  }
}
```

OEM typically sets `productName` and `assistantName` to the same string. `assistantName` omitted
falls back to `productName`. Colors omitted keep the purple tokens.

**Load order:** `/opt/sage/brand.json` (baked into the Environment) → `SAGE_BRAND_FILE` if set.
One pack per process. Not `.sage/brand.json` (that would be per project).

**API:** `GET /api/brand` returns the resolved pack. The Workbench applies it on boot: document
title, logo `src`/`alt`, top-bar product name, CSS variables (`--purple-700/600/500/100`), and
user-visible copy that today says Sage.

## Voice

Every string a person reads that names us, including agent system prompts.

Substitute `{assistantName}` when installing OpenCode config (`_install_opencode_config`) and when
writing `.sage/chat-work/AGENTS.md`. Restart OpenCode after a pack change so the prompt reloads.

Do not rewrite Resource names, Domino catalogue copy the user did not ask us to hide, or error
text that names a missing Dataset.

## Out of v1

- Paths `.sage/`, agent ids `sage-chat` / `sage-plan`, git `sage: …`, HTML `<!-- sage:… -->`
- `sageBuilder` key and `title: "Sage"` on the pluggable tool
- Per-org packs; Domino `/admin/whitelabel`
- Chrome of Built Apps this Workbench publishes
