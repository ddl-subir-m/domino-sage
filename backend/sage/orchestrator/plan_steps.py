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
from pathlib import PurePosixPath

from ..workspace import plan_doc

# "### 1. Sample data module" — 2-4 hashes, and '.' or ')' optional, because models drift between
# heading levels and numbering styles even when the prompt pins one.
_HEADING = re.compile(r"^#{2,4}[ \t]*(\d{1,2})[.)]?[ \t]+(.+?)[ \t]*$")
# "**1. Sample data module**" — the backward-compatible bold-numbered fallback for older plans.
_BOLD_HEADING = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\*\*[ \t]*(\d{1,2})[.)]?[ \t]*([^*]+?)[ \t]*\*\*[ \t]*:?[ \t]*$")
# Wider than the execution grammar on purpose. The validator must see a numbered heading with an
# empty label, or a malformed step can disappear before the candidate count is compared with the
# parsed count.
_CANDIDATE_HEADING = re.compile(r"^#{2,4}[ \t]*\d{1,2}[.)]?(?:[ \t]+.*)?$")
_CANDIDATE_BOLD_HEADING = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?\*\*[ \t]*\d{1,2}[.)]?(?:[ \t]+[^*]*)?\*\*[ \t]*:?[ \t]*$"
)
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
    # The step's position in the list, 1..len(steps) — NOT the number the model wrote. `parse_steps`
    # renumbers, because everything downstream already reads this as a 1-based position: the "phase
    # n of N" the person is shown, `step_index`'s current-step marker, and the resume point a failed
    # phase writes. That last one is why 0 in particular cannot be allowed through — 0 is how
    # `read_plan_retry_step` encodes "this plan owes no build", so a step numbered 0 that died
    # archived its plan having built nothing (#272).
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


@dataclass(frozen=True)
class PlanContractCheck:
    valid: bool
    step_count: int
    missing_sections: tuple[str, ...]
    malformed_steps: int
    invalid_file_fields: int
    contradictory_file_fields: int


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
    files = _split_list(fields.get("files", ""))
    # A file in both lists is a contradiction — "create src/types.ts" and "don't touch src/types.ts"
    # in one brief — and an agent that takes it literally cannot finish the step. Files wins: it says
    # what the step is FOR, while Don't touch only fences it off from other steps' work.
    dont_touch = [p for p in _split_list(fields.get("dont_touch", "")) if p not in files]
    return PlanStep(
        n=n,
        label=label.strip(" .:"),
        files=files,
        do=do,
        done_when=done_when,
        dont_touch=dont_touch,
        raw="\n".join([f"### {n}. {label}", *body]).strip(),
    )


def parse_steps(plan_md: str) -> list[PlanStep]:
    """Every fully-formed step in the plan, in document order. Malformed steps are dropped, not
    repaired — is_phasable() then declines the whole plan rather than building a partial one."""
    steps: list[PlanStep] = []
    in_step = False
    label = ""
    body: list[str] = []

    def flush() -> None:
        if in_step:
            # Numbered by position among the steps that survived parsing, discarding the number the
            # model wrote. The regexes are lenient about numbering on purpose (models drift off the
            # shape the prompt pins), and every way they drift — starting at 0, repeating a number,
            # leaving a gap where a malformed step was dropped — reached a consumer that assumed
            # 1..N contiguous. Renumbering here is the single place that assumption can be made true
            # for all of them at once.
            step = _build(len(steps) + 1, label, body)
            if step is not None:
                steps.append(step)

    for line in (plan_md or "").splitlines():
        m = _HEADING.match(line) or _BOLD_HEADING.match(line)
        if m:
            flush()
            # The number in the heading is what identifies the line AS a step. It is not the
            # step's number — that comes from `flush` above.
            in_step, label, body = True, m.group(2), []
            continue
        if line.startswith("#"):  # any other heading ends the current step ("## Open questions")
            flush()
            in_step, label, body = False, "", []
            continue
        if in_step:
            body.append(line)
    flush()
    return steps


_REQUIRED_SECTIONS = ("problem", "users", "outcomes", "screens", "acceptance", "plan")


def _present(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _one_sentence(value: str) -> bool:
    text = " ".join((value or "").split())
    if not text:
        return False
    # Do not split `U.S. data`, but reject the ordinary `First sentence. Second sentence.` drift.
    return re.search(r"[.!?][\"')\]]*[ \t]+[A-Z0-9]", text) is None


def _valid_workspace_path(value: str) -> bool:
    path = value.strip()
    if not path or path.startswith("/") or "\\" in path:
        return False
    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and ".." not in parsed.parts


def validate_execution_contract(markdown: str) -> PlanContractCheck:
    """Check the durable plan shape without reading files or calling a model.

    `parse_steps` stays backward compatible. This stricter door compares every numbered candidate
    with the steps that survived that parser, then checks the fields that a cold implementation
    session needs.
    """
    parsed = plan_doc.parse_sections(markdown)
    sections = parsed["sections"]
    missing = []
    if not _present(parsed["title"]):
        missing.append("title")
    if not _one_sentence(parsed["summary"]):
        missing.append("summary")
    missing.extend(key for key in _REQUIRED_SECTIONS if not _present(sections.get(key)))

    plan = str(sections.get("plan") or "")
    candidate_count = sum(
        1 for line in plan.splitlines()
        if _CANDIDATE_HEADING.match(line) or _CANDIDATE_BOLD_HEADING.match(line)
    )
    steps = parse_steps(plan)
    malformed = max(0, candidate_count - len(steps))

    labels = [step.label.strip().casefold() for step in steps]
    malformed += sum(1 for label in labels if not label)
    malformed += len(labels) - len(set(labels))

    invalid_files = 0
    contradictory = 0
    for step in steps:
        if not step.files:
            invalid_files += 1
        invalid_files += sum(not _valid_workspace_path(path) for path in step.files)

        fields: dict[str, str] = {}
        for line in step.raw.splitlines()[1:]:
            match = _FIELD.match(line)
            if match:
                key = _CANON.get(match.group(1).lower().replace("’", "'"))
                if key and key not in fields:
                    fields[key] = match.group(2).strip()
        dont_touch = _split_list(fields.get("dont_touch", ""))
        invalid_files += sum(not _valid_workspace_path(path) for path in dont_touch)
        files_normalized = {str(PurePosixPath(path)) for path in step.files}
        dont_touch_normalized = {str(PurePosixPath(path)) for path in dont_touch}
        contradictory += len(files_normalized & dont_touch_normalized)

    valid = bool(steps) and not (missing or malformed or invalid_files or contradictory)
    return PlanContractCheck(
        valid=valid,
        step_count=len(steps),
        missing_sections=tuple(missing),
        malformed_steps=malformed,
        invalid_file_fields=invalid_files,
        contradictory_file_fields=contradictory,
    )


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
