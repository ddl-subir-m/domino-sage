"""Scope classifier — is this request big enough to deserve a plan and a sign-off first?

The plan gate (SPEC P6) is keyed on `has_built`, which is permanently true after the first build. So
every request from turn two onward — "make the table sortable" and "add auth, orgs and a billing
page" alike — goes straight to code with nothing to approve. `has_built` was never a principled
boundary for that decision, it is just where the first-build gate happened to land.

Answering it needs a model. Scope is not a property of the prompt STRING — "make it look more
professional" is six words and a week of work, and no regex over the text can see that. That is why
this is the one decision in the turn path that isn't a pure function, and why it stays behind the
deterministic short-circuits in _build_stream: approvals, questions, architecture and explicit plan
requests are all decided for free before anything here runs.

Three properties the caller depends on:

  * Biased to BUILD. Dogfood transcripts are overwhelmingly small iteration turns, and a wrong PLAN
    interrupts one of those with an approval wall every time, while a wrong BUILD lands a diff the
    user can revert (the snapshot is already there). This is the OPPOSITE of the first-build gate's
    bias, where ambiguity gates — on turn one there is no app to fall back to.
  * Fails OPEN when it is down, SAFE when it is broken — two different failures with two different
    right answers. An error or a timeout means the call never landed: that returns False and the turn
    builds exactly as it does today, because a classifier nobody can reach must not be a classifier
    that blocks builds. An answer that arrives and is unreadable is the opposite case — the call
    worked and the contract didn't — and that returns True, because a needless plan card is a pause
    the user can approve away while a needless build is a diff already written into their app.
    Observed live 2026-08-21 (#29): four consecutive empty verdicts, every one silently absorbed into
    a build, on turns whose whole content was "the data you asked about isn't attached".
  * Reports itself broken instead of degrading quietly. Three unreadable answers in a row is not a
    flake, so it trips a breaker: logged once at ERROR for the maintainer (it lands in the ring
    /api/diag/log serves), and the classifier is not called again this process. A permanently broken
    one then costs nothing and falls back to the old build default, rather than walling every Auto
    turn behind an approval prompt it can never stop asking for.
  * Bounded. The call runs on a worker thread with a hard wall-clock timeout, because the gateway
    client sets no read timeout on streams by design (a mid-build stall was traced to one) and a hung
    classify would otherwise hang the whole turn before it started.

It gets a static listing of the app's source — paths and line counts, nothing read — because the two
judgements it is worst at both need it. "Add a settings page" is a new feature or a small edit
depending entirely on whether Settings.tsx already exists, and "make it look more professional" is an
afternoon or a week depending on whether the app is four files or forty. Neither is visible in the
prompt string.

No tools, though, and nothing read. Letting this explore the repo would mean an agent loop inside a
pre-pass that runs before every Auto turn on a built project — build-sized latency and cost paid up
front to answer one word, duplicating what the gated plan turn already does with better tools and a
real budget. The listing is the cheap majority of the signal: one call, one word out. Judging actual
coupling by reading the code is the part deliberately left to the plan turn.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

from ..gateway.client import CostLabels, GatewayClient
from ..router.models import ModelCatalog

log = logging.getLogger(__name__)

# Wall-clock budget for the whole call. Generous enough for a cold gateway, short enough that a
# stalled classifier costs the user a pause rather than a dead turn.
TIMEOUT_S = 12.0

# Long prompts are truncated rather than refused: the decision lives in the first sentence or two,
# and paying for a 5k-token paste to answer one word is the kind of cost nobody would sign off on.
MAX_PROMPT_CHARS = 2000

# The app's own source. Everything beside it in the workspace — package.json, the tsconfigs, dist/,
# public/, node_modules — is template scaffolding identical across every project, so listing it would
# cost tokens to say nothing about this app's size or shape.
SOURCE_DIR = "src"

# A listing long enough to be worth reading, short enough that it can't dominate the call. Past this
# the count itself is the signal ("this app is large"), not which files made the cut.
MAX_FILES = 60

# Above this a file is an asset, not source, and counting its newlines is pointless I/O.
MAX_FILE_BYTES = 200_000

# Unreadable answers in a row before the classifier is declared broken and stops being called. One
# is a flake and two is bad luck, but a model that has answered outside a one-word vocabulary three
# consecutive times is not going to start complying on the fourth — and by then the user has sat
# through three approval walls they didn't ask for.
MAX_UNREADABLE = 3


class _Health:
    """Consecutive unreadable answers, and the breaker they trip.

    Process-wide, not per-project: a Sage builder serves one project, and the thing being tracked is
    the classifier — a gateway route and a model — not anything about a workspace. Held as an object
    rather than two module globals so a test can reset it without reaching for `global`."""

    def __init__(self) -> None:
        self.unreadable = 0
        self.broken = False

    def reset(self) -> None:
        self.unreadable = 0
        self.broken = False

    def answered(self) -> None:
        """A verdict we could read. Consecutive is the point — an answer clears the streak, so a
        classifier that is merely flaky never reaches the breaker."""
        self.unreadable = 0

    def unreadable_answer(self, answer: str) -> bool:
        """Record an answer in neither vocabulary; return what wants_a_plan should return.

        True (gate) until the breaker trips, then False (build) forever after."""
        self.unreadable += 1
        if self.unreadable < MAX_UNREADABLE:
            log.warning("scope: unrecognised verdict %r (%d in a row) — planning instead of building",
                        answer[:60], self.unreadable)
            return True
        if not self.broken:
            self.broken = True
            # ERROR, and said once: this is the line a maintainer should find in /api/diag/log when a
            # user reports that Sage suddenly wants to plan everything, or stopped planning anything.
            log.error("scope: classifier BROKEN — %d unreadable verdicts in a row (last %r). "
                      "Not calling it again; every Auto turn now builds ungated, as it did before "
                      "the classifier existed. Check the gateway route for the ask model.",
                      self.unreadable, answer[:60])
        return False


_health = _Health()

_SYSTEM = """\
You decide whether a change request to an existing web app is big enough to deserve a written plan \
and the user's approval before any code is written.

Answer with exactly one word, nothing else:

PLAN - a substantial change: a new feature or page, a restructure touching several parts of the app, \
or a request so open-ended that what gets built is a real choice ("make it look more professional", \
"make it production ready").
BUILD - everything else: a tweak, a fix, a copy or styling change, a new column or control, anything \
a user would expect to simply happen.

Default to BUILD. Interrupting a small change with an approval step is worse than building a \
medium-sized one directly. Answer PLAN only if you would be uncomfortable writing the code without \
checking first."""

# Appended to the system prompt, not the user message: this is background the model judges against,
# and a listing pasted in front of the request reads like part of what the user typed.
_CONTEXT_HEADER = """\

The app's existing source files, with line counts, so you can judge the request against what is \
already built. A request naming something already in this list is usually an edit, not a new \
feature; an open-ended request against a large app is a bigger job than the same words against a \
small one. This is a listing only — you have not seen inside these files."""


def _content_from(chunk: dict[str, Any]) -> str:
    """The text of one OpenAI-shaped chunk, streamed (`delta`) or whole (`message`)."""
    choices = chunk.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    holder = first.get("delta") or first.get("message") or {}
    return holder.get("content") or ""


def _extract(raw: bytes) -> str:
    """Assistant text out of a gateway response body.

    Tolerates both shapes on purpose. The Domino client streams SSE (`data: {...}` lines, terminated
    by `[DONE]`), but the fake used in tests answers with one plain JSON object, and a non-streaming
    endpoint would too. Parsing both means the classifier doesn't care which it is talking to."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line or line == "[DONE]":
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict):
            out.append(_content_from(chunk))
    return "".join(out).strip()


def _lines(p: Path) -> int | None:
    """Newline count, or None for anything too big, binary, or unreadable to bother with.

    Binaries are skipped rather than counted because "hero.png (53 lines)" is not a small
    inaccuracy — it is a number the model will reason about as if it meant something."""
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        with p.open("rb") as fh:
            head = fh.read(4096)
            if b"\0" in head:
                return None
            return head.count(b"\n") + sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 16), b""))
    except OSError:
        return None


def app_context(root: Path | None) -> str:
    """A listing of the app's source files with line counts, or "" when there is nothing to say.

    Returns "" on every failure path — a missing workspace, an unreadable directory, an app with no
    src/ — because the classifier is strictly better off with no context than with a wrong or partial
    one, and the caller has no way to act on the difference anyway."""
    if root is None:
        return ""
    src = Path(root) / SOURCE_DIR
    try:
        files = sorted(
            p for p in src.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src).parts)
        )
    except OSError:
        return ""
    if not files:
        return ""

    shown = files[:MAX_FILES]
    lines = []
    for p in shown:
        n = _lines(p)
        rel = p.relative_to(root).as_posix()
        lines.append(f"  {rel} ({n} lines)" if n is not None else f"  {rel}")
    if len(files) > len(shown):
        # Said out loud rather than silently cut: a truncated listing that looks complete would make
        # a large app read as a medium one, which is the exact misjudgement this context exists to fix.
        lines.append(f"  ... and {len(files) - len(shown)} more files")
    return _CONTEXT_HEADER + "\n\n" + "\n".join(lines)


def _model_for(catalog: ModelCatalog, locked: bool) -> str:
    """The read-only ask model, or its sovereign counterpart under a sensitivity lock.

    The lock is absolute — it is the product's central promise — so a classification is not an
    exception to it. Routing this one call to a vendor model on a locked project would leak the
    user's prompt off the sovereign path for the sake of an optimisation."""
    return catalog.sovereign_ask if locked else catalog.ask


def wants_a_plan(
    prompt: str,
    *,
    gateway: GatewayClient,
    catalog: ModelCatalog,
    locked: bool,
    root: Path | None = None,
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> bool:
    """True when this request should be planned and approved before any code is written.

    False on every failure path, so the caller can treat it as "gate this?" and nothing else."""
    text = (prompt or "").strip()
    if not text:
        return False
    if _health.broken:
        # Declared broken earlier in this process (see _Health). Skip the call entirely rather than
        # pay for another answer we already know we can't read.
        return False

    # File paths are app structure, not user data — but they are still workspace content leaving the
    # box, so this rides the same routing as the prompt and a locked project sends it sovereign. The
    # lock being incidental here is the point: there is no path where it can be skipped as an
    # optimisation.
    request = {
        "model": _model_for(catalog, locked),
        "messages": [
            {"role": "system", "content": _SYSTEM + app_context(root)},
            {"role": "user", "content": text[:MAX_PROMPT_CHARS]},
        ],
        # One word is the whole contract, but the CEILING can't be one word's worth. A route with
        # extended thinking on spends this budget on reasoning tokens before emitting any content,
        # and the call then returns a perfectly successful response whose content is "" — which is
        # what four consecutive turns saw in #29, each one logged as an unrecognised verdict and
        # absorbed into a build. Raised to leave room for that. It costs nothing in the ordinary
        # case: max_tokens is a ceiling, not a spend, and a model answering "BUILD" still stops at
        # one word.
        "max_tokens": 256,
        "temperature": 0,
        "stream": True,
    }
    # phase="plan": this call decides whether to plan, so it is planning overhead, and tagging it as
    # its own component keeps it separable from build inference in cost analysis.
    labels = CostLabels(phase="plan", mode="auto", component="scope", session=session, version=version)

    def _call() -> str:
        return _extract(b"".join(gateway.route(request, labels)))

    # Deliberately NOT a `with` block: the executor's context manager shuts down with wait=True, so
    # exiting it blocks until the worker returns and the timeout above it buys nothing — the turn
    # still hangs for as long as the gateway does. shutdown(wait=False) is what makes it a bound.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="sage-scope")
    try:
        answer = pool.submit(_call).result(timeout=timeout_s)
    except concurrent.futures.TimeoutError:
        # The worker is abandoned, not cancelled — a blocked socket read can't be interrupted. It
        # holds one thread until the gateway gives up, which is the price of not hanging the turn.
        log.warning("scope: classify timed out after %.1fs — building without a plan", timeout_s)
        return False
    except Exception as e:
        log.warning("scope: classify failed (%s: %s) — building without a plan", type(e).__name__, e)
        return False
    finally:
        pool.shutdown(wait=False)

    verdict = answer.strip().upper()
    if verdict.startswith("PLAN"):
        _health.answered()
        return True
    if verdict.startswith("BUILD"):
        _health.answered()
        return False
    # An answer in neither vocabulary means the contract didn't hold. Returning False here — which is
    # what this did until #29 — is not "no signal", it is a guess, and it guesses the one outcome
    # with side effects: the turn goes on to write code. Gate instead, and let _Health decide when a
    # run of these stops being an anomaly and becomes a broken classifier.
    return _health.unreadable_answer(answer)
