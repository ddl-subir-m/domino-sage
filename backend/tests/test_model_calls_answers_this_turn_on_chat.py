"""`model_calls` answers "this turn" on the Chat lane too (#471).

WHY THIS EXISTS. `/api/diag` publishes `project.model_calls` under the sentence "how many inferences
reached the shim THIS turn". Exactly one place increments it — the `/v1/chat/completions` stream
wrapper in `app.py` — and exactly one place zeroed it, `_build_stream`'s per-send loop. `chat_stream`
zeroed `tool_call_responses` beside it and deliberately left this one alone, because nothing on the
Chat lane READS it. Nothing reading it is not the same as nothing publishing it: on Chat the number
was a running total on top of whatever the last Build or approve turn left, plus every Chat turn
since, and the 0 reading the sentence is written around is gone permanently once any turn has run.

WHY THE RESET IS AT CHAT'S GRANT AND NOT IN `_acquire_turn`, which is the obvious move — all three
lanes take the turn lock through it and it already clears `resolved_model` there. Build's reset lives
in a LOOP, not at a door: `_build_stream` re-zeroes at the top of each agent turn, and a phased build
runs each phase through `_build_stream` WITHOUT re-acquiring the turn. So on Build the counter answers
"inferences in THIS PHASE", and that is what the three-way failure split reads (0 model calls /
model calls but no tool calls / tool calls but no disk edits — see `Project.model_calls`). Hoisting
would silently re-scope Build to the whole turn. What the number SHOULD mean on a phased build is a
separate decision; this change does not take it.

So there are two conditions here and they are tested apart, because one test covering both is short
by construction: a Chat turn reports its own count, AND a phased build still re-zeroes between
phases. `test_a_phased_build_re_zeroes_between_phases` is the second one, and its docstring
names the hoisted reset as the thing it exists to catch.

WHAT THESE TESTS OBSERVE. The attribute, on both lanes. `/api/diag` reads it directly (`app.py`,
`"model_calls": p.model_calls`) and is the only surface it reaches, so reading the attribute is the
same read the product does. Build once also carried it in a `turn-summary` stream event, but nothing
in `backend/sage/workbench/` ever handled that event and #474 deleted it; the phased test below
reads `CountingOpenCode.seen_on_entry` instead, which is a strictly earlier observation.

WHAT THE FAKE STANDS IN FOR. The increment itself is written by the real `/v1/chat/completions`
route, which no fake-OpenCode test reaches; `test_shim_stream.py` drives that route for real and
pins `proj.model_calls == 1`. Here the fake plants the increment at the moment the route would have.
So these tests exercise the RESETS and their scope, and take the write on trust — the same limit
`test_a_toolless_chat_turn_is_observed.py` records for `tool_call_responses`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator import service as svc
from sage.orchestrator.service import Orchestrator
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

PHASED_PLAN = """# Trades Dashboard

A dashboard for exploring trades.

## Plan

### 1. Data module
- Files — src/data.ts
- Do — Export two hundred sample trade rows.
- Done when — src/data.ts exports rows and the app compiles.

### 2. Trades table
- Files — src/Table.tsx
- Do — Render the rows in a sortable table.
- Done when — The preview shows a sortable table.

### 3. Currency filter
- Files — src/Filter.tsx
- Do — Add a currency dropdown above the table.
- Done when — Picking a currency narrows the visible rows.
"""


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Answers the straight-to-gateway classifiers with one body, whatever they ask.

    They are the reason the docstring on `/api/diag` no longer claims a 0 rules out a gateway hang:
    `chat_intent`, `scope` and `table_rank` reach the gateway without passing the shim and
    deliberately never increment `model_calls`. Nothing here asserts on that; it is named so the
    next reader knows this gateway is not the counter's source.
    """

    def __init__(self, verdict: str = "BUILD") -> None:
        self.verdict = verdict

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": self.verdict}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


class CountingOpenCode(FakeOpenCode):
    """Stands in for the `/v1` route: one inference per send, counted where the route counts it.

    `per_send` is how many inferences the Nth send makes, so "this send ran the model three times"
    is written as exactly that. It also records what `model_calls` read on ENTRY to each send, which
    is what tells a re-zeroed counter from an accumulating one without waiting for a summary event.
    """

    def __init__(self, workspace: Path, turns: list[Turn] | None = None, *,
                 per_send: list[int] | None = None) -> None:
        super().__init__(workspace, turns)
        self.per_send = list(per_send or [])
        self.project = None
        self.seen_on_entry: list[int] = []
        self.sends = 0

    def send_prompt(self, session_id: str, text: str, **kwargs) -> None:
        nth = self.sends
        self.sends += 1
        if self.project is not None:
            self.seen_on_entry.append(self.project.model_calls)
            n = self.per_send[nth] if nth < len(self.per_send) else 1
            self.project.model_calls += n
        super().send_prompt(session_id, text, **kwargs)


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building the app\n")
    return t


def _catalog() -> ModelCatalog:
    return ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                        plan="strong-model", implement="cheap-coder", ask="ask-model")


def _orch(tmp: Path, oc: CountingOpenCode) -> Orchestrator:
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(), catalog=_catalog(),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc)
    oc.project = orch.project(start_preview=False)
    return orch


@pytest.fixture(autouse=True)
def _no_real_waiting(monkeypatch):
    """The Chat poll loop waits on `_EventTap`, which blocks for real seconds against a fake that
    never emits a session frame. Scripted for the reason #466's file scripts it."""
    real = time.monotonic
    offset = {"s": 0.0}

    def advance(s=0.0):
        offset["s"] += (s or 0.0)

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset["s"])
    monkeypatch.setattr(svc._EventTap, "wait", lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(svc._EventTap, "wait_any", lambda self, timeout, floor=0.0: bool(advance(timeout)))
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


# --- Condition one: a Chat turn reports its own count -------------------------------------------


def _chat_orch(tmp: Path, oc: CountingOpenCode) -> Orchestrator:
    orch = _orch(tmp, oc)
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch


def test_a_chat_turn_counts_only_its_own_inferences(tmp_path: Path):
    """The defect, said as a number. Three Chat turns of one inference each: without a reset the
    attribute `/api/diag` publishes reads 1, 2, 3 and calls every one of them "THIS turn"."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An answer.")] * 3, per_send=[1, 1, 1])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    readings = []
    for i in range(3):
        list(orch.chat_stream(tid, f"summarise the event table ({i})"))
        readings.append(orch.project(start_preview=False).model_calls)

    assert readings == [1, 1, 1], "a Chat turn reported the conversation's inferences, not its own"


def test_a_chat_turn_with_several_inferences_reports_all_of_them(tmp_path: Path):
    """The reset must zero, not pin to one. A turn whose agent ran the model three times reports
    three; a fix that assigned 1 instead of 0 would pass the test above and fail here."""
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An answer.")] * 2, per_send=[2, 3])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    readings = []
    for i in range(2):
        list(orch.chat_stream(tid, f"summarise the event table ({i})"))
        readings.append(orch.project(start_preview=False).model_calls)

    assert readings == [2, 3]


def test_a_chat_turn_does_not_inherit_what_an_earlier_lane_left(tmp_path: Path):
    """The cross-lane carry named in the ticket, planted at the only interface the lanes share.

    `model_calls` is a plain int on the Project and nothing clears it when a turn ENDS, so after a
    Build or approve turn it holds that turn's last send. 7 is an ordinary value for it to hold, and
    a Chat turn starting behind it must not add to it.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An answer.")], per_send=[1])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    orch.project(start_preview=False).model_calls = 7
    list(orch.chat_stream(tid, "summarise the event table"))

    assert orch.project(start_preview=False).model_calls == 1, \
        "a Chat turn added its inference to what the previous lane left"


def test_the_reset_happens_before_the_first_inference_of_the_turn(tmp_path: Path):
    """Placement, not just presence: at the grant, ahead of everything the turn sends.

    A reset that ran at the END of a Chat turn would leave every assertion above green while
    `/api/diag` — which is read MID-turn, and is the whole point of the field — still showed the
    previous turn's total for the entire time a turn was live.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An answer.")] * 2, per_send=[1, 1])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    orch.project(start_preview=False).model_calls = 7
    for i in range(2):
        list(orch.chat_stream(tid, f"summarise the event table ({i})"))

    assert oc.seen_on_entry == [0, 0], \
        f"a Chat send began with a non-zero count: {oc.seen_on_entry}"


def test_a_request_that_never_became_a_turn_does_not_clear_the_last_one(tmp_path: Path):
    """The other side of the placement, and the reason it is AFTER the grant rather than at the door.

    A wedged workspace is refused inside `_acquire_turn` and never runs. It is not a turn, so it has
    no count of its own to report, and wiping the last real turn's would leave `/api/diag` saying 0
    — which is the reading that means "OpenCode sent nothing", about a turn that sent plenty. This
    is the same argument `_begin_model_record` records for `resolved_model`, asked of this counter.
    """
    ws = tmp_path / "mnt" / "code"
    oc = CountingOpenCode(ws, [Turn(text="An answer.")], per_send=[2])
    orch = _chat_orch(tmp_path, oc)
    tid = orch.create_thread()["id"]

    list(orch.chat_stream(tid, "summarise the event table"))
    assert orch.project(start_preview=False).model_calls == 2

    orch._turn_wedged = True
    list(orch.chat_stream(tid, "and again"))

    assert orch.project(start_preview=False).model_calls == 2, \
        "a refused request cleared the count of the turn that did run"
    assert oc.sends == 1, "the refused request reached the agent"


# --- Condition two: a phased build still re-zeroes between phases --------------------------------


def _writes(rel: str) -> Turn:
    return Turn(writes={rel: f"// {rel}\nexport const x = 1;\n"})


def _phased(tmp: Path, per_send: list[int]):
    ws = tmp / "mnt" / "code"
    turns = [Turn(text=PHASED_PLAN), _writes("src/data.ts"), _writes("src/Table.tsx"),
             _writes("src/Filter.tsx")]
    oc = CountingOpenCode(ws, turns, per_send=per_send)
    orch = _orch(tmp, oc)
    project = orch.project(start_preview=False)
    project.record.write_settings({"phased_build": True})
    project.control.set_mode(Mode.AUTO)
    list(orch.build_stream("build me a trades dashboard"))
    return orch, oc, project


def test_a_phased_build_re_zeroes_between_phases(tmp_path: Path):
    """The rejected fix, named as the thing that would break.

    Three phases run through `_build_stream` under ONE turn lock, taken once by `approve_stream`.
    Each phase must start its own count. A reset moved up into `_acquire_turn` — which every
    lane passes through, and which is the obvious place for it — runs once for all three, and the
    count becomes a running total that no longer answers "inferences in THIS PHASE".
    """
    orch, oc, _project = _phased(tmp_path, per_send=[1, 2, 3, 4])
    list(orch.approve_stream())

    # Every send began at zero — the plan turn's included, which is the non-phased loop's own
    # reset seen once. Read at the ENTRY to each send: `CountingOpenCode` records the counter
    # the moment the route would have incremented it, so a phase that began mid-count is caught
    # here whatever any later reader of the number does or does not report.
    assert oc.seen_on_entry == [0, 0, 0, 0], f"a phase began mid-count: {oc.seen_on_entry}"


def test_a_phased_builds_phases_do_not_re_acquire_the_turn(tmp_path: Path, monkeypatch):
    """Why the reset cannot be hoisted, stated as the fact the reasoning rests on.

    If a phase DID take the lock again, `_acquire_turn` would be a correct home for the reset and
    this whole placement argument would be wrong. It does not: `approve_stream` takes the lock once
    and `_approve_locked` calls straight into the phase loop. Pinned here because the argument in
    `chat_stream`'s comment is only true while this is.
    """
    orch, _oc, _project = _phased(tmp_path, per_send=[1, 1, 1, 1])
    grants = []
    real = Orchestrator._acquire_turn

    def counting(self, ticket, **kwargs):
        grants.append(kwargs.get("kind"))
        yield from real(self, ticket, **kwargs)

    monkeypatch.setattr(Orchestrator, "_acquire_turn", counting)
    list(orch.approve_stream())

    assert grants == ["build"], f"the phase loop re-acquired the turn: {grants}"


# --- The docstring's other sentence, kept honest -------------------------------------------------


def test_no_straight_to_gateway_caller_touches_the_counter():
    """`/api/diag` now says 0 does not rule out a gateway hang. This is why, and it is derived.

    The population is every non-HTTP function in the orchestrator that reaches `gateway.route` ITSELF. Those
    calls go past the `/v1` shim handler, which is the only place `model_calls` is incremented, so a
    turn can be sitting on the gateway with this counter reading 0. Several of them run inside a live
    Chat turn: `chat_intent.start()` BLOCKS at the top of every one, `_delegated_ask` serves the
    agent's own `askmodel` tool, and `_withhold_probe` bisects a guardrail refusal with many calls in
    flight at once.

    Derived from the mechanism rather than from a file list, so a caller added next year joins it
    without anyone remembering to — and `service.py` is searched like every other module rather than
    excluded, because two of the six live there. An earlier version of this test skipped it and an
    earlier version of that docstring named three of the six; a list of receivers cannot know it is
    short, which is the whole reason this is a derivation.

    WHAT THIS STILL CANNOT SEE, said plainly rather than left for the next reader to discover: a
    module handed an already-built callable instead of the gateway. `withhold.py` is exactly that
    shape — its caller passes `ask`, and nothing in it names `gateway`. The derivation keys on the
    call, so a future classifier written that way joins the population invisibly. That is why the
    docstring on /api/diag names no list at all.

    AST on both sides, never a text search. `scope.py` and `handoff.py` each SAY `model_calls` in a
    comment explaining why they leave it alone, and a grep cannot tell that from an increment.
    """
    import ast

    pkg = Path(svc.__file__).parent
    found = {}
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text())
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        touches = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Attribute) and n.attr == "model_calls"]
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "route"
                    and "gateway" in ast.dump(call.func.value).lower()):
                continue
            # The OUTERMOST enclosing function, not the innermost. `_withhold_probe` wraps its call
            # in a two-line closure, and asking only about that closure's span would pass by being
            # too small to contain anything.
            holding = [f for f in funcs if f.lineno <= call.lineno <= f.end_lineno]
            if not holding:
                continue
            # Inference HTTP handlers ARE the counter boundary, including a handler
            # registered inside install(). Derive that boundary from its decorator,
            # not a filename exemption that could also hide an ancillary caller.
            if any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                   and d.func.attr == "post" and d.args
                   and isinstance(d.args[0], ast.Constant)
                   and isinstance(d.args[0].value, str) and d.args[0].value.startswith("/v1/")
                   for f in holding for d in f.decorator_list):
                continue
            owner = max(holding, key=lambda f: f.end_lineno - f.lineno)
            inside = [ln for ln in touches if owner.lineno <= ln <= owner.end_lineno]
            found[f"{path.name}:{owner.name}"] = inside

    # The derivation must actually find these, or this test passes by finding nobody. Named as the
    # floor it has to clear, not as the answer: anything else it turns up is in scope too.
    assert set(found) >= {
        "chat_intent.py:start", "handoff.py:wants_an_app", "scope.py:start",
        "table_rank.py:_ask", "service.py:_delegated_ask", "service.py:_withhold_probe",
    }, sorted(found)
    assert {k: v for k, v in found.items() if v} == {}, \
        f"a straight-to-gateway caller now touches model_calls: {found}"
