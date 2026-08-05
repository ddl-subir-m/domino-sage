"""Parse an approved plan back into the self-contained briefs a phased build executes.

A phased build runs each step in a BRAND-NEW OpenCode session, so the model executing step 4 has
never read the plan, never saw steps 1-3, and can't ask. That's the whole point — a cheap model
holds up in a clean 8k window and falls apart at 100k — but it means each step has to carry its own
context: which files, what "done" looks like, what not to touch.

Parsing is a regex over headings, deliberately NOT a second cheap-LLM extraction pass:

- The plan is user-editable in the approval card (`plan_edits`), so anything extracted at plan time
  is stale the moment they edit. Re-extracting at approve time would put a model call — with
  latency and a failure mode — directly under the Approve button.
- "Weak model reads a plan and quietly drops a constraint" is the exact failure a phased build
  exists to avoid. Putting one on the critical path would be self-defeating.
- A pure function tests with no gateway, no OpenCode and no workspace, which is what lets the
  phased executor be tested at all.

The parser is lenient about shape (heading styles, separators, field synonyms) because models drift,
and strict about `Do` and `Done when` because a step missing either isn't a brief — it's a wish. A
plan that doesn't fully parse falls back to a normal single-context build rather than half-phasing,
which would be worse than not phasing at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# "### 1. Sample data module" — 2-4 hashes, and '.' or ')' optional, because models drift between
# heading levels and numbering styles even when the prompt pins one.
_HEADING = re.compile(r"^#{2,4}[ \t]*(\d{1,2})[.)]?[ \t]+(.+?)[ \t]*$")
# "**1. Sample data module**" — the bold-numbered fallback. _PLAN_SHAPE (the non-phased shape) trains
# models on bolded labels, so a planner that half-remembers it produces this instead of a heading.
_BOLD_HEADING = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\*\*[ \t]*(\d{1,2})[.)]?[ \t]*([^*]+?)[ \t]*\*\*[ \t]*:?[ \t]*$")
# "- Done when — the app compiles". Longest synonyms first so "Done when" can't be read as "Do".
# The separator set covers what renderers and models substitute for the em-dash we ask for.
_FIELD = re.compile(
    r"^[ \t]*[-*][ \t]*"
    r"(done when|done|do not touch|don'?t touch|leave alone|files|touch|verify|change|work|do)"
    r"[ \t]*[—–:-][ \t]*(.+?)[ \t]*$",
    re.IGNORECASE,
)
_CANON = {
    "files": "files", "touch": "files",
    "do": "do", "change": "do", "work": "do",
    "done when": "done_when", "done": "done_when", "verify": "done_when",
    "don't touch": "dont_touch", "dont touch": "dont_touch",
    "do not touch": "dont_touch", "leave alone": "dont_touch",
}
# Below this a phased build doesn't repay its own overhead: every phase pays a fresh session's
# bootstrap (OpenCode re-reads AGENTS.md and project context), so two phases can cost more than one
# context ever would.
MIN_STEPS = 3


@dataclass(frozen=True)
class PlanStep:
    n: int
    label: str
    files: list[str]
    do: str
    done_when: str
    dont_touch: list[str]
    # The verbatim section, which is what actually gets handed to the executor. Parsed fields drive
    # decisions (is this phasable, what do we show); `raw` makes sure anything the parser didn't
    # model still reaches the model that has to act on it.
    raw: str


def _split_list(value: str) -> list[str]:
    return [p.strip(" `") for p in value.split(",") if p.strip(" `")]


def _build(n: int, label: str, body: list[str]) -> PlanStep | None:
    """One step from its heading and body lines, or None if it isn't a usable brief."""
    fields: dict[str, str] = {}
    for line in body:
        m = _FIELD.match(line)
        if m:
            key = _CANON.get(m.group(1).lower().replace("’", "'"))
            if key and key not in fields:  # first write wins: a repeated label is a model stutter
                fields[key] = m.group(2).strip()
    do, done_when = fields.get("do", ""), fields.get("done_when", "")
    if not do or not done_when:
        # No acceptance criterion (or no work) means a cold executor has nothing to aim at and no
        # way to know it's finished. Half a brief is what produces a phase that "succeeds" empty.
        return None
    return PlanStep(
        n=n,
        label=label.strip(" .:"),
        files=_split_list(fields.get("files", "")),
        do=do,
        done_when=done_when,
        dont_touch=_split_list(fields.get("dont_touch", "")),
        raw="\n".join([f"### {n}. {label}", *body]).strip(),
    )


def parse_steps(plan_md: str) -> list[PlanStep]:
    """Every fully-formed step in the plan, in document order. Malformed steps are dropped, not
    repaired — is_phasable() then declines the whole plan rather than building a partial one."""
    steps: list[PlanStep] = []
    n: int | None = None
    label = ""
    body: list[str] = []

    def flush() -> None:
        if n is not None:
            step = _build(n, label, body)
            if step is not None:
                steps.append(step)

    for line in (plan_md or "").splitlines():
        m = _HEADING.match(line) or _BOLD_HEADING.match(line)
        if m:
            flush()
            n, label, body = int(m.group(1)), m.group(2), []
            continue
        if line.startswith("#"):  # any other heading ends the current step ("## Open questions")
            flush()
            n, label, body = None, "", []
            continue
        if n is not None:
            body.append(line)
    flush()
    return steps


def is_phasable(plan_md: str, min_steps: int = MIN_STEPS) -> bool:
    """Whether this plan should run as a phased build at all. False sends it down the normal
    single-context path — the safe direction, since that's what every plan did before."""
    return len(parse_steps(plan_md)) >= min_steps


def step_index(steps: list[PlanStep], current_n: int) -> str:
    """A one-line-per-step map of the build, for the executor's prompt.

    The cheapest possible defence against cross-phase amnesia: ~15 tokens a step buys the model
    enough to know that the data module already exists and the filters are someone else's job,
    without carrying the plan itself — which is the context we're trying not to pay for.
    """
    out = []
    for s in steps:
        where = "done" if s.n < current_n else "this step" if s.n == current_n else "later"
        out.append(f"{s.n}. {s.label} ({where})")
    return "\n".join(out)
