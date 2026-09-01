"""The plan document: sections parsed out of the plan's own markdown.

The plan the user reads and edits is a markdown file. That stays true here. This module only
gives that file a shape — a heading per section — and reads the sections back out.

Parsing rather than a second model call, for the reasons `plan_steps` already gives: the plan is
user-editable in the approval card and on the plan page, so anything extracted at plan time is
stale the moment someone edits it, and a model call between the user and their own document is
latency plus a failure mode. `parse_sections(render(sections))` is the whole contract; both
directions are pure functions with no gateway, no OpenCode and no workspace behind them.

Lenient about headings, because models drift: any `#` or `##` heading starts a section, its label
is matched loosely, and an unknown one is kept as prose rather than dropped. Deeper headings are
body — the phased shape writes `### N. Label` inside `## Plan`, and those belong to the plan.

`plan` is a section like any other here, but its body is kept verbatim so that
`plan_steps.parse_steps` and `_count_plan_steps` keep reading exactly what they read before.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass


def now() -> str:
    """Same stamp format the Thread store writes, so the two read alike in the UI."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    # text | list | screens | questions | raw
    kind: str


# Keys, labels and kinds mirror SECTIONS in workbench/js/components/plan.js. The page renders
# straight off these keys, so the two lists have to agree; the order here is the order on the page.
SECTIONS: tuple[Section, ...] = (
    Section("problem", "Problem & outcome", "text"),
    Section("users", "Who uses this", "text"),
    Section("outcomes", "What it does", "list"),
    Section("screens", "Screens", "screens"),
    Section("nonGoals", "Not doing", "list"),
    Section("acceptance", "Done when", "list"),
    Section("plan", "Plan", "raw"),
    Section("openQuestions", "Open questions", "questions"),
)

SECTION_BY_KEY = {s.key: s for s in SECTIONS}

# Loose label matching. The canonical label of every section is a synonym of itself; the rest are
# the ways models phrase the same heading when they drift off the shape they were given.
_SYNONYMS = {
    "problem": "problem", "problem outcome": "problem", "problem and outcome": "problem",
    "outcome": "problem", "goal": "problem",
    "users": "users", "who uses this": "users", "who uses it": "users", "audience": "users",
    "what it does": "outcomes", "outcomes": "outcomes", "features": "outcomes",
    "screens": "screens", "pages": "screens", "views": "screens",
    "not doing": "nonGoals", "non goals": "nonGoals", "nongoals": "nonGoals",
    "out of scope": "nonGoals",
    "done when": "acceptance", "acceptance": "acceptance", "acceptance criteria": "acceptance",
    "plan": "plan", "build steps": "plan", "steps": "plan", "build plan": "plan",
    "open questions": "openQuestions", "questions": "openQuestions",
}

_HEADING = re.compile(r"^[ \t]*(#{1,6})[ \t]*(.+?)[ \t]*$")
_BULLET = re.compile(r"^[ \t]*[-*•][ \t]+(.+?)[ \t]*$")
# "- [x] Which columns?" — how a resolved question renders. Models write a plain bullet; both parse.
_CHECKBOX = re.compile(r"^\[([ xX])\][ \t]*(.*)$")
# "- **Table view** — one row per run" — the screens shape. The separator set matches the one
# `plan_steps._FIELD` accepts, because models substitute the dash we ask for just as freely here.
_SCREEN = re.compile(r"^\*\*[ \t]*(.+?)[ \t]*\*\*[ \t]*(?:[—–:-][ \t]*(.*))?$")


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _key_for(label: str) -> str | None:
    return _SYNONYMS.get(_norm(label))


def _strip_blanks(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _parse_body(kind: str, lines: list[str]):
    body = _strip_blanks(list(lines))
    if kind == "raw":
        return "\n".join(body)
    if kind == "text":
        return "\n".join(body).strip()

    items = []
    for line in body:
        m = _BULLET.match(line)
        if m:
            items.append(m.group(1).strip())
    if kind == "list":
        return items
    if kind == "screens":
        out = []
        for item in items:
            m = _SCREEN.match(item)
            if m:
                out.append({"name": m.group(1).strip(), "detail": (m.group(2) or "").strip()})
            else:
                out.append({"name": item, "detail": ""})
        return out
    # questions
    out = []
    for item in items:
        m = _CHECKBOX.match(item)
        if m:
            out.append({"text": m.group(2).strip(), "resolved": m.group(1).lower() == "x"})
        else:
            out.append({"text": item, "resolved": False})
    return out


def empty_sections() -> dict:
    """Every key present, so the page never reads `undefined` off a plan that skipped a section."""
    return {s.key: ("" if s.kind in ("text", "raw") else []) for s in SECTIONS}


def parse_sections(plan_md: str) -> dict:
    """`{title, summary, sections}` from a plan's markdown. Never raises; a plan missing every
    heading still comes back with its prose as the summary.

    `title` is a `# ` heading before anything else, and it is the document's name. A plan used to
    open on its first SENTENCE, which three surfaces then read as a name — the plan card, the
    document, and the Built App's display name. None of them wanted a sentence: live, an app was
    called "- This app will be an AI consumption dashboard for exploring daily usage, spend," in
    the rail, bullet marker and trailing comma included, because that is what the first line was.

    Only the first level-1 heading, and only ahead of every section: `# Plan` is a section (it is
    matched below first), and a `#` further down is a heading inside a document that already began.
    """
    title = ""
    summary: list[str] = []
    collected: dict[str, list[str]] = {}
    current: str | None = None
    unknown: list[str] = []

    for line in (plan_md or "").splitlines():
        m = _HEADING.match(line)
        # Only top-level headings divide the document. `### 1. Label` is a phased step and belongs
        # to whichever section it sits in.
        if m and len(m.group(1)) <= 2:
            key = _key_for(m.group(2))
            if key:
                current = key
                collected.setdefault(key, [])
                continue
            if (len(m.group(1)) == 1 and not title and not collected
                    and not any(seen.strip() for seen in summary)):
                title = m.group(2).strip()
                continue
            # An unknown heading keeps its text rather than vanishing: better a section the page
            # shows as prose than a silently shorter plan.
            current = None
            unknown.append(line)
            continue
        (collected[current] if current else (unknown if unknown else summary)).append(line)

    sections = empty_sections()
    for key, lines in collected.items():
        sections[key] = _parse_body(SECTION_BY_KEY[key].kind, lines)

    lead = "\n".join(_strip_blanks(summary)).strip()
    trailing = "\n".join(_strip_blanks(unknown)).strip()
    if trailing:
        sections["plan"] = "\n\n".join(p for p in (sections["plan"], trailing) if p)
    return {"title": title, "summary": lead, "sections": sections}


def _render_body(kind: str, value) -> list[str]:
    if kind in ("text", "raw"):
        return [str(value or "")]
    if kind == "list":
        return [f"- {item}" for item in (value or [])]
    if kind == "screens":
        out = []
        for screen in value or []:
            name = (screen.get("name") or "").strip()
            detail = (screen.get("detail") or "").strip()
            out.append(f"- **{name}** — {detail}" if detail else f"- **{name}**")
        return out
    return [
        f"- [{'x' if q.get('resolved') else ' '}] {(q.get('text') or '').strip()}"
        for q in (value or [])
    ]


def render(summary: str, sections: dict, title: str = "") -> str:
    """The markdown a plan document is stored as, and the markdown handed to the builder.

    Empty sections are left out rather than written as empty headings — the same reason
    `_drop_empty_questions` exists: a heading with nothing under it reads as a section the user
    still has to fill in.

    `title` last and optional, so every existing caller reads the same. It is written back because
    the round trip is this module's whole contract: parsed out and not rendered again, a document's
    name would survive until somebody edited a section, and the copy handed to the builder would
    then fall back to naming the app after its first sentence — the bug the heading exists to fix.
    """
    out: list[str] = []
    if (title or "").strip():
        out.append(f"# {title.strip()}")
    if (summary or "").strip():
        out.append(summary.strip())
    for section in SECTIONS:
        value = (sections or {}).get(section.key)
        body = _strip_blanks(_render_body(section.kind, value))
        if not body or not any(line.strip() for line in body):
            continue
        out.append(f"## {section.label}")
        out.append("\n".join(body))
    return "\n\n".join(out).strip() + "\n" if out else ""
