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

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

log = logging.getLogger("sage.orchestrator.brand")

_BAKED = Path("/opt/sage/brand.json")

# Where a partner's own logo and favicon live, and the only directory this process publishes.
# NEVER `/opt/sage` itself: that holds `opencode.json` and the gateway credentials configured in
# it, so a static mount one level too high hands them to anyone who can reach the shell. The
# Domino defaults are not here — they are the shell's own assets, under `/img`.
BRAND_DIR = Path("/opt/sage/brand")

# The only two things the brand route will ever serve, and the only two a pack URL may name.
IMAGE_SUFFIXES = (".svg", ".png")

# A pack image URL is a relative path on our own origin and nothing more. No scheme, no host, no
# query, and nothing that could close the `href="…"` it is written into on both entry pages.
_RELATIVE_IMAGE = re.compile(r"^(?:\./)?[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")
_TOKEN = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")
_WARNED: set[str] = set()   # complaints already made, so each is made once
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

DEFAULT: dict[str, Any] = {
    "productName": "AI Workbench",
    "assistantName": "Sage",
    # The platform under us, as actor and as destination. One key, because they are one
    # fact: is the platform rebranded? It presupposes the partner set the platform's own
    # /admin/whitelabel — Sage renames the word, not the page it links to.
    "platformName": "Domino",
    "pageTitle": "Sage Workspace",
    # The other products the top bar can switch to. A list rather than a name, so a partner with
    # no second product sets `[]` and the switcher collapses to a plain label: a switcher with one
    # item is not a switcher, it offers a choice that does not exist.
    "peerProducts": [{"key": "studio", "label": "ML Studio"}],
    "logoUrl": "./img/domino-logo.svg",
    "logoAlt": "Domino",
    # Written into `<link rel="icon">` on both entry pages by the one route that serves them, so
    # the browser paints the partner's icon rather than ours and then swaps. A partner's own file
    # goes under BRAND_DIR and is named "./brand/<file>.svg"; relative, because the platform
    # serves the shell under a proxy prefix that an absolute path would walk out of.
    "faviconUrl": "./img/domino-favicon.svg",
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
    pack = _merge(pack, _overlay(_BAKED))
    extra = (os.environ.get("SAGE_BRAND_FILE") or "").strip()
    if extra:
        pack = _merge(pack, _overlay(Path(extra)))
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


class BrandImages(StaticFiles):
    """The partner's own images, mounted at `/brand` over `BRAND_DIR` and nowhere near its parent.

    **`/opt/sage/brand/`, never `/opt/sage/`.** One level up holds `opencode.json` and the gateway
    configuration written into it, so a mount that high publishes the credentials this process
    runs on. That is the boundary. The extension allowlist is the second lock behind it, so a
    brand directory that later grows a config file of its own does not grow a leak with it — the
    check runs on the requested path before anything touches the filesystem, so a name that is
    not `.svg` or `.png` is a 404 whatever it turns out to be, `..` on the way in included.

    The rest is StaticFiles' behaviour, chosen rather than inherited by accident: `html` stays
    off, so a directory is a 404 and never a listing or an index page; symlinks are resolved
    before the containment check, so a link planted in the brand directory cannot reach out of
    it; and a Mount is terminal, so a miss is a 404 rather than a fallthrough to the shell.

    `check_config` is dropped because the directory exists only where a partner baked one into an
    Environment image. A laptop has no `/opt/sage` at all and must still boot; a directory that
    is not there answers 404 exactly like an empty one.
    """

    async def check_config(self) -> None:
        return

    async def get_response(self, path: str, scope: Any) -> Any:
        if not path.lower().endswith(IMAGE_SUFFIXES):
            # The same 404 StaticFiles raises for a miss, so a probe cannot tell a refused name
            # from an absent one and learn what is up there by the difference.
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        # The pack names one filename and a partner replaces its bytes under it; nothing renames
        # it on the way through, so the browser has to ask rather than guess at freshness.
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _tokens(pack: dict[str, Any]) -> dict[str, str]:
    """The pack flattened to the token names a string may use. A noun contributes both its forms,
    `{dataset}` and `{datasetPlural}`, because a plural is read from the pack and never derived."""
    table = {key: value for key, value in pack.items() if isinstance(value, str)}
    for key, forms in pack["nouns"].items():
        table[key] = forms["singular"]
        table[key + "Plural"] = forms["plural"]
    return table


def _overlay(path: Path) -> dict | None:
    """One pack file, read and complained about before it is merged."""
    overlay = _read(path)
    _warn_unknown_keys(overlay, path)
    return overlay


def _warn_unknown_keys(overlay: dict | None, path: Path) -> None:
    """A key Sage does not recognise is ignored, and saying so out loud is the whole
    forward-compatibility story: the pack carries no `version` field and never will, so a partner's
    typo is findable only if the log names it. It stays a warning and never a refusal — a brand pack
    must not be able to stop the product booting.

    What counts as recognised is `DEFAULT`'s own keys rather than a second list beside them, so a
    key stops warning by being implemented and the two cannot drift apart.

    Said once per key per file, because `load()` runs per request: the complaint belongs to the pack
    the process booted with, not to whoever happened to ask for it first.
    """
    if not overlay:
        return
    for key in overlay:
        if key in DEFAULT:
            continue
        seen = f"{path}:{key}"
        if seen in _WARNED:
            continue
        _WARNED.add(seen)
        log.warning("brand pack %s has unknown key %r — ignoring it.", path, key)


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
    favicon = _image_url("faviconUrl", overlay.get("faviconUrl"))
    if favicon:
        out["faviconUrl"] = favicon
    peers = overlay.get("peerProducts")
    if isinstance(peers, list):
        # An empty list is the point of the key, so it is honoured rather than treated as unset:
        # `[]` is a partner saying there is nowhere else to go, and it must reach the shell.
        out["peerProducts"] = [
            {"key": _nonempty(peer.get("key")), "label": _nonempty(peer.get("label"))}
            for peer in peers
            if isinstance(peer, dict) and _nonempty(peer.get("key")) and _nonempty(peer.get("label"))
        ]
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


def _image_url(key: str, value: object) -> str:
    """A pack image URL, or `""` to keep the default. Refusal is the fallback, not an error.

    A **remote URL is refused rather than fetched** (ADR-0014). It breaks an air-gapped install,
    and it hands the partner's CDN a log of every user's session — one request per page load,
    from every viewer, whether or not the partner meant to collect that.

    The rest is the same boundary the route enforces, said once more where the pack is read:
    `.svg` or `.png`, no walking, and a plain relative path so it survives the proxy prefix the
    platform serves the shell under. The value ends up inside `href="…"` on both entry pages, so
    the character set is what a path needs and nothing that could close the attribute.

    Said out loud and once, like every other pack complaint: a partner who mistypes this gets the
    Domino icon and a log line naming the value, not a Workbench that will not boot.
    """
    url = _nonempty(value)
    if not url:
        return ""
    if "://" in url or url.startswith("//"):
        why = "a remote URL is refused — it breaks an air-gapped install"
    elif ".." in url.split("/") or not _RELATIVE_IMAGE.match(url):
        why = "it has to be a relative path under the Workbench's own origin"
    elif not url.lower().endswith(IMAGE_SUFFIXES):
        why = f"only {' and '.join(IMAGE_SUFFIXES)} are served"
    else:
        return url
    seen = f"{key}={url}"
    if seen not in _WARNED:
        _WARNED.add(seen)
        log.warning("brand pack %s is %r — %s. Using the default.", key, url, why)
    return ""


def _nonempty(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
