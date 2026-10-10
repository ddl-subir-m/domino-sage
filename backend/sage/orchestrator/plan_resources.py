"""The Project resources a plan step names in `Uses`, and whether the built app reaches them (#712).

Live (Signal Room, #714): a step's Deal Desk route shipped as a placeholder and the build called
itself complete, because the unbuilt-step check asks only whether a step's files changed. A step that
names a resource now also has to REACH it, and that is read as literal text in the app's own sources,
with no model call:

- an MCP server: one file both calls `call_tool(` or `list_tools(` (`sage_mcp`) and names that
  server, by its URL or by `secret("NAME")` for a secret its URL or headers use. The same file,
  because that is where the call's address is: a URL constant beside some other server's call is not
  this server's call. The URL alone is not enough for a server whose key is in its URL.
- a secret: `secret("NAME")`, the one way app code reads a key (template AGENTS.md).
- an LLM Alias: its name as a quoted string, or, when the app has only one, any `askModel(` or
  `askJson(` call, since a call that names no model gets the only one.
- a query: in `.sage/queries.json` and its name as a quoted string in the app.

A name in `Uses` that is none of these is not judged: the planner may name something the Project
does not have, and a repair cannot conjure it.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

from ..extension_mcp import variables
from . import brand
from .plan_steps import PlanStep

log = logging.getLogger(__name__)

_MCP_CALL = re.compile(r"\b(?:call_tool|list_tools)\s*\(")
_MODEL_CALL = re.compile(r"\b(?:askModel|askJson)\s*\(")
# Names already logged as unknown, so a check that runs twice a turn says so once.
_logged_unknown: set[str] = set()


@dataclass(frozen=True)
class Resources:
    """What this Project offers a plan: MCP server rows (`extension_mcp.list_servers`), secret
    names, the LLM Alias call names this app may use, and the Project skills switched on for it."""
    servers: tuple[dict, ...] = ()
    secrets: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()


def server_lines(servers: Iterable[dict]) -> list[str]:
    """One line per MCP server: its name and the tools it listed."""
    return [f"- MCP server `{row['name']}` — tools: "
            f"{', '.join(row.get('tools') or []) or 'none listed'}" for row in servers]


def planner_note(res: Resources) -> str:
    """The planner's list of what the Project has, and how a step names it. Empty for none."""
    alias = brand.apply_voice("{llmAlias}")
    lines = [*server_lines(res.servers),
             *(f"- Secret `{name}`" for name in res.secrets),
             *(f"- {alias} `{name}`" for name in res.aliases),
             *(brand.apply_voice(f"- {{project}} skill `{name}`") for name in res.skills)]
    if not lines:
        return ""
    skills = (" A measure, query or default scope a {project} skill defines is binding: a step "
              "that shows one names the skill, and the build uses the skill's pinned query or "
              "definition and opens on its default scope rather than writing its own."
              if res.skills else "")
    return brand.apply_voice(
        "This {project} has these resources. When a plan step depends on one of them, give that "
        "step a '- Uses —' bullet naming each one by its name in backticks alone, comma-separated. "
        "A step that needs what an MCP server's tools provide depends on that server even when the "
        "request does not name it: the app calls it, and shows no sample data in its place. Leave "
        "the bullet out of a step that depends on none." + skills) + "\n" + "\n".join(lines)


def query_names(path: Path) -> list[str]:
    """The names in a `.sage/queries.json` catalog: a list of `{"name": ...}`. Empty when unreadable."""
    try:
        catalog = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [str(q["name"]) for q in catalog if isinstance(q, dict) and q.get("name")] \
        if isinstance(catalog, list) else []


def _base_url(url: str) -> str:
    """A server's address without its query, fragment or a `{env:...}` part: what code spells."""
    head = url.split("{env:", 1)[0]
    parts = urlsplit(head)
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/") if parts.netloc else ""


def _reads_secret(text: str, name: str) -> bool:
    return re.search(r"\bsecret\(\s*([\"'])" + re.escape(name) + r"\1\s*\)", text) is not None


def _quoted(text: str, name: str) -> bool:
    return re.search(r"([\"'`])" + re.escape(name) + r"\1", text) is not None


def _reaches_server(row: dict, texts: list[str]) -> bool:
    base = _base_url(str(row.get("url") or ""))
    keys = variables([row.get("url") or "", row.get("headers") or {}])
    return any(_MCP_CALL.search(t) and ((base and base in t) or any(_reads_secret(t, k) for k in keys))
               for t in texts)


def _reached(name: str, res: Resources, texts: list[str], queries: list[str]) -> bool | None:
    """Whether the app reaches `name`, or None when `name` is no resource of this Project."""
    known = False
    for row in res.servers:
        if row.get("name") == name:
            known = True
            if _reaches_server(row, texts):
                return True
    if name in res.secrets:
        known = True
        if any(_reads_secret(t, name) for t in texts):
            return True
    if name in res.aliases:
        known = True
        if any(_quoted(t, name) for t in texts) or (
                len(res.aliases) == 1 and any(_MODEL_CALL.search(t) for t in texts)):
            return True
    if name in queries:
        known = True
        if any(_quoted(t, name) for t in texts):
            return True
    return False if known else None


@dataclass(frozen=True)
class UnreachedStep(PlanStep):
    """A step whose files were written but whose named resources nothing in the app reaches. A
    `PlanStep`, so it travels the unbuilt-step path unchanged; `why` is what that path says."""
    missing: tuple[str, ...] = ()
    why: str = ""


def unreached(steps: list[PlanStep], res: Resources, sources: list[tuple[str, str | None]],
              queries: list[str]) -> list[UnreachedStep]:
    """Each step naming a resource in `Uses` that nothing in `sources` reaches.

    `sources` is the app's own code as (path, text), Sage-owned files already left out; `queries` is
    the names in `.sage/queries.json`.
    """
    texts = [text for _path, text in sources if text]
    kind = re.compile(r"(?i)(?:mcp server|secret|query|" + re.escape(
        brand.apply_voice("{llmAlias}")) + r")\s+`?(.+?)`?$")
    out = []
    for step in steps:
        missing = []
        for name in step.uses:
            reached = _reached(name, res, texts, queries)
            # A planner copying a line of `planner_note` writes its kind too: "MCP server deal-desk".
            bare = kind.match(name)
            if reached is None and bare:
                name = bare.group(1)
                reached = _reached(name, res, texts, queries)
            if reached is None:
                if name not in _logged_unknown:
                    _logged_unknown.add(name)
                    log.info("plan step %d names %r in Uses, which is no resource of this project",
                             step.n, name)
            elif not reached:
                missing.append(name)
        if missing:
            names = ", ".join(missing[:-1]) + f" and {missing[-1]}" if len(missing) > 1 else missing[0]
            verb = "reads" if all(n in res.secrets for n in missing) else "calls"
            why = (f"{step.n} ({step.label}) uses {names}, but nothing in the app {verb} "
                   f"{'them' if len(missing) > 1 else 'it'}.")
            out.append(UnreachedStep(**{f.name: getattr(step, f.name) for f in fields(step)},
                                     missing=tuple(missing), why=why))
    return out
