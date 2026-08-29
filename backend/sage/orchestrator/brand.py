"""Workbench brand pack (docs/workbench/brand.md).

OEM overlay on top of the Domino default. Missing or unreadable files leave the default.
One pack per process; not per project.
"""
from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

log = logging.getLogger("sage.orchestrator.brand")

_BAKED = Path("/opt/sage/brand.json")
_TOKEN = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")
_WARNED: set[str] = set()   # Title Case complaints already made, so each is made once
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

DEFAULT: dict[str, Any] = {
    "productName": "AI Workbench",
    "assistantName": "Sage",
    # The platform under us, as actor and as destination. One key, because they are one
    # fact: is the platform rebranded? It presupposes the partner set the platform's own
    # /admin/whitelabel — Sage renames the word, not the page it links to.
    "platformName": "Domino",
    "pageTitle": "Sage Workspace",
    "logoUrl": "./img/domino-logo.svg",
    "logoAlt": "Domino",
    # The platform's own whitelabel renames its nouns and no API exposes that vocabulary to a
    # Sage Builder, so the pack carries a copy of it. This will drift, silently, and it is
    # accepted only until such an API exists — if one appears these keys go, not grow.
    #
    # Two forms per noun because the nouns are woven into sentences, not confined to labels.
    # There is no pluralisation engine and no article engine: copy needing `a`/`an` is reworded.
    "nouns": {
        "dataset": {"singular": "Dataset", "plural": "Datasets"},
        "dataSource": {"singular": "Data Source", "plural": "Data Sources"},
        "modelApi": {"singular": "Model API", "plural": "Model APIs"},
        "llmAlias": {"singular": "LLM Alias", "plural": "LLM Aliases"},
        "builtApp": {"singular": "Built App", "plural": "Built Apps"},
        "gallery": {"singular": "Gallery", "plural": "Galleries"},
    },
    "colors": {
        "primary": "#543FDE",
        "primaryDark": "#311EAE",
        "primaryLight": "#EEEBFC",
    },
}


def load() -> dict[str, Any]:
    pack = deepcopy(DEFAULT)
    pack = _merge(pack, _read(_BAKED))
    extra = (os.environ.get("SAGE_BRAND_FILE") or "").strip()
    if extra:
        pack = _merge(pack, _read(Path(extra)))
    return pack


def text(template: str, **values: object) -> str:
    """Resolve the brand tokens in a user-visible string, where the string is written.

    Substitution is author-time (ADR-0014): a new string is branded because whoever wrote it wrote
    it that way. A filter over outgoing bytes was rejected — by then provenance is gone, so it
    cannot tell our word for the platform from a Resource a user named after the company.

    `{productName}` and friends come from the pack. `values` fill the rest of the sentence, so the
    whole sentence stays one literal that the lint over marked positions can read. A substituted
    value is not scanned again, so a Resource name carrying braces passes through untouched.

    An unknown token is left as it was written rather than raising: a typo in a string must never
    stop the Workbench booting, and a passed-through platform error can carry braces of its own.
    """
    if not template or "{" not in template:
        return template
    table = _tokens(load())
    table.update({key: str(value) for key, value in values.items()})
    return _TOKEN.sub(lambda m: table.get(m.group(1), m.group(0)), template)


def apply_voice(text: str, assistant_name: str | None = None) -> str:
    """Swap the default speaker name in a prompt or AGENTS.md body."""
    name = (assistant_name if assistant_name is not None else load()["assistantName"]).strip()
    if not text or name == "Sage":
        return text
    return text.replace("Sage's", f"{name}'s").replace("Sage", name)


def apply_agent_voice(cfg: dict, assistant_name: str | None = None) -> dict:
    """Rewrite OpenCode agent prompt strings only. Provider ids and agent keys stay Sage."""
    name = assistant_name if assistant_name is not None else load()["assistantName"]
    agents = cfg.get("agent")
    if not isinstance(agents, dict):
        return cfg
    for spec in agents.values():
        if isinstance(spec, dict) and isinstance(spec.get("prompt"), str):
            spec["prompt"] = apply_voice(spec["prompt"], name)
    return cfg


def _tokens(pack: dict[str, Any]) -> dict[str, str]:
    """The pack flattened to the token names a string may use. A noun contributes both its forms,
    `{dataset}` and `{datasetPlural}`, because a plural is read from the pack and never derived."""
    table = {key: value for key, value in pack.items() if isinstance(value, str)}
    for key, forms in pack["nouns"].items():
        table[key] = forms["singular"]
        table[key + "Plural"] = forms["plural"]
    return table


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _merge(base: dict, overlay: dict | None) -> dict:
    if not overlay:
        return base
    out = deepcopy(base)
    product = _nonempty(overlay.get("productName"))
    if product:
        out["productName"] = product
    if "assistantName" in overlay:
        out["assistantName"] = _nonempty(overlay.get("assistantName")) or out["productName"]
    elif product:
        out["assistantName"] = product
    for key in ("platformName", "pageTitle", "logoUrl", "logoAlt"):
        value = _nonempty(overlay.get(key))
        if value:
            out[key] = value
    nouns = overlay.get("nouns")
    if isinstance(nouns, dict):
        merged_nouns = {key: dict(forms) for key, forms in out["nouns"].items()}
        for key, forms in nouns.items():
            if key not in merged_nouns or not isinstance(forms, dict):
                continue          # a token Sage never emits is not a rename
            for form in ("singular", "plural"):
                value = _nonempty(forms.get(form))
                if value:
                    _warn_unless_title_case(key, form, value)
                    merged_nouns[key][form] = value
        out["nouns"] = merged_nouns
    colors = overlay.get("colors")
    if isinstance(colors, dict):
        merged = dict(out["colors"])
        for key in ("primary", "primaryDark", "primaryLight"):
            raw = colors.get(key)
            if isinstance(raw, str) and _HEX.match(raw.strip()):
                merged[key] = raw.strip()
        out["colors"] = merged
    return out


def _warn_unless_title_case(key: str, form: str, value: str) -> None:
    """A noun carrying `_` or starting lowercase reads as a leaked code identifier rather than a
    product term — *"No files in this xyz_dataset."* — so it is worth saying out loud. It is used
    anyway: a brand pack must never be able to stop the product booting.

    Said once per bad value, because `load()` runs per request and a pack nobody is going to
    change would otherwise fill the log.
    """
    if "_" not in value and not value[:1].islower():
        return
    seen = f"{key}.{form}={value}"
    if seen in _WARNED:
        return
    _WARNED.add(seen)
    log.warning(
        "brand pack noun %s.%s is %r — nouns are Title Case. Using it anyway.", key, form, value
    )


def _nonempty(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
