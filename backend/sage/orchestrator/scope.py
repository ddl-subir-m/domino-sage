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
  * Fails open. Any error, timeout, or answer we don't recognise returns False and the turn builds
    exactly as it does today. A classifier that is down must not be a classifier that blocks builds.
  * Bounded. The call runs on a worker thread with a hard wall-clock timeout, because the gateway
    client sets no read timeout on streams by design (a mid-build stall was traced to one) and a hung
    classify would otherwise hang the whole turn before it started.

Text-only on purpose: no repo access, no tools. It judges the REQUEST, not the codebase. That is the
known limit — "make it look more professional" is caught because the phrasing is unbounded, not
because anything measured the app.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
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
    session: str | None = None,
    version: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> bool:
    """True when this request should be planned and approved before any code is written.

    False on every failure path, so the caller can treat it as "gate this?" and nothing else."""
    text = (prompt or "").strip()
    if not text:
        return False

    request = {
        "model": _model_for(catalog, locked),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": text[:MAX_PROMPT_CHARS]},
        ],
        # One word is the whole contract; the ceiling is slack for a model that opens with a space
        # or a stray newline, not room to explain itself.
        "max_tokens": 8,
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
    except Exception as e:  # noqa: BLE001 - any upstream failure must fall back to today's behaviour
        log.warning("scope: classify failed (%s: %s) — building without a plan", type(e).__name__, e)
        return False
    finally:
        pool.shutdown(wait=False)

    verdict = answer.strip().upper()
    if verdict.startswith("PLAN"):
        return True
    if not verdict.startswith("BUILD"):
        # An answer in neither vocabulary means the contract didn't hold — treat it as no signal
        # rather than guessing, and say so, because a model that stopped answering in one word is
        # something to notice rather than silently absorb.
        log.warning("scope: unrecognised verdict %r — building without a plan", answer[:60])
    return False
