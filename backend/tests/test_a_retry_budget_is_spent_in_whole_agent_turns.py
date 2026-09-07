"""What a retry costs, counted in the only unit that is spent whole: the agent turn.

The healthy half of this question was measured live on 2026-09-07 — four turns against the real
gateway, each one agent turn and 2-3 inferences, zero nudges. So on a turn that goes right the
budgets below cost nothing at all. What was never reproduced is a turn that goes WRONG, and that is
what these tests are: they drive each retry path through the real build loop and pin how many agent
turns it spends.

The agent turn is the unit because it is what a budget buys. One pass of the send/poll/typecheck
loop is one `send_prompt` — a whole fresh model turn, the agent re-reading the tree and working the
request again, not a single extra inference on the end of the last one. So a budget of N does not
add N inferences, it adds up to N agent turns, and each of those costs whatever a turn costs.

WHAT IS ASSERTED HERE AND WHAT IS NOT. The agent-turn count is structural, so it is asserted: it
comes out of `timing`'s `agent-turn.N` spans and out of `FakeOpenCode.prompts`, and the two are
cross-checked against each other so a loop that stopped opening spans cannot read as a cheap loop.
The inferences INSIDE one agent turn are not: they are how many times OpenCode rounds back to the
model while working, which depends on the model and the tree, and only the shim on a real
deployment counts them (`project.model_calls`, /api/diag/timing). So the cost of a nudge reads as
`agent turns x the live per-turn figure`, and this file fixes only the left factor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sage import timing
from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.app_helpers import TEMPLATE
from sage.resources.pinned_model import render_config
from sage.resources.provider import FakeResourceProvider, LlmAlias
from sage.router.models import Mode, ModelCatalog

from .fake_opencode import FakeOpenCode, Turn

REPO_TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "react-vite"
BASE = "https://apps.example.com/apps/llm_gateway/v1"
ALIASES = [LlmAlias("id-sonnet", "sonnet", "Claude Sonnet 4.6", None, ["chat"], {"input": 3.0})]

# The budgets under test, restated so a change to one of them fails here with the number in hand
# rather than in an off-by-one somewhere down the file. Not imported: they are locals of
# `_build_stream`, and a test that could import them could not also prove the loop honours them.
MAX_NUDGES = 3
MAX_RUNTIME_FIXES = 3
MAX_LEAK_FIXES = 2
MAX_GATEWAY_FIXES = 2

LEAKED_CSV = "a,b\n1,2\n"
RAW_GATEWAY_CALL = f'''
export async function ask(q: string) {{
  const res = await fetch("{BASE}/chat/completions", {{
    method: "POST",
    credentials: "include",
    body: JSON.stringify({{ model: "sonnet", messages: [{{ role: "user", content: q }}] }}),
  }});
  return (await res.json()).choices[0].message.content;
}}
'''


class OkFeedback:
    """Typecheck always clean. Every path in this file is a defect tsc cannot see — that is the
    whole reason these budgets exist beside the breaker rather than inside it."""

    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _crashes(monkeypatch, times: int) -> None:
    """The open preview reports a render throw for the next `times` checks, then stays clean.

    Patched at `_await_runtime_error` rather than by writing `project.runtime_error`, because the
    real one polls for four seconds for a report NEWER than this turn's send — a field set once
    would be consumed by the first check and never seen again, which is the one-fix case, not the
    budget case."""
    left = {"n": times}

    def crashed(self, project, since, timeout=4.0):
        if left["n"] <= 0:
            return None
        left["n"] -= 1
        return {"message": "TypeError: value.toFixed is not a function",
                "stack": "    at App (src/App.tsx:12:9)",
                "ts": since, "app": project.app_for_turn().app_id}

    monkeypatch.setattr(Orchestrator, "_await_runtime_error", crashed)


def _template(tmp: Path) -> Path:
    """The shipped LLM helper verbatim: the gateway scan skips Sage's own sources by path, and a
    stub at that path would make the skip pass for the wrong reason."""
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / TEMPLATE.llm_path).write_text((REPO_TEMPLATE / TEMPLATE.llm_path).read_text())
    (t / TEMPLATE.llm_config_path).write_text(render_config([], None, None))
    (t / "package.json").write_text("{}")
    (t / "app.sh").write_text("#!/bin/bash\nexec npx vite preview\n")
    (t / "AGENTS.md").write_text("# Template rules\n")
    return t


def _orch(tmp_path: Path, turns: list[Turn]) -> tuple[Orchestrator, FakeOpenCode]:
    ws = tmp_path / "mnt" / "code"
    oc = FakeOpenCode(ws, turns)
    orch = Orchestrator(
        workspace_dir=ws,
        template=_template(tmp_path),
        gateway=None,
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage",
        feedback=OkFeedback(),
        opencode_client=oc,
        resources=FakeResourceProvider(list(ALIASES)),
        browser_gateway_base=BASE,
    )
    project = orch.project(start_preview=False)
    project.control.set_mode(Mode.IMPLEMENT)
    # Without this the first turn is swallowed by the plan gate and every count below is one high.
    project.record.write_settings({"skip_planning": True})
    return orch, oc


def _agent_turns(oc: FakeOpenCode) -> int:
    """How many whole model turns the build spent, cross-checked two ways.

    `timing`'s spans are the number a deployment reads back off /api/diag/timing, and `prompts` is
    the number of times the agent was actually asked. A loop that stopped opening spans would read
    as a free retry on the first, so neither is trusted alone."""
    rec = timing.recent(1)[0]
    spans = [s.name for s in rec.spans if s.name.startswith("agent-turn.")]
    assert spans == [f"agent-turn.{i + 1}" for i in range(len(spans))], spans
    assert len(spans) == len(oc.prompts), f"{len(spans)} spans but {len(oc.prompts)} prompts sent"
    return len(spans)


def _writes(n: int) -> Turn:
    """A retry that does something — but not the thing asked. Each one has to touch a file: a turn
    that changes nothing takes the no-code nudge branch above the runtime/leak/gateway scans and
    never reaches them."""
    return Turn(text="Trying again.", writes={f"src/note{n}.ts": f"export const n = {n};\n"})


def _wrote_nothing(text: str = "Here is what I would do.") -> Turn:
    return Turn(text=text)


def _decision(events: list[dict]) -> str:
    return [e for e in events if e.get("type") == "done"][-1]["decision"]


# ---- the nudge: a turn that writes nothing ---------------------------------------------------


def test_one_nudge_costs_exactly_one_extra_agent_turn(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        _wrote_nothing(),
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
    ])

    events = list(orch.build_stream("add a chart"))

    assert _agent_turns(oc) == 2
    assert _decision(events) == "typecheck clean"
    # The second send is the nudge, not the user's prompt again: a re-send of the same words would
    # cost the same turn and buy nothing, and that is the failure this budget is protecting against.
    assert "IMPLEMENT" in oc.prompts[1]["text"]


def test_an_agent_that_never_writes_spends_the_whole_nudge_budget_and_stops(tmp_path: Path):
    orch, oc = _orch(tmp_path, [_wrote_nothing()] * 10)

    events = list(orch.build_stream("add a chart"))

    # MAX_NUDGES re-sends on top of the first send. The ceiling of the cheapest failure mode there
    # is: four whole model turns to learn the agent will not write code.
    assert _agent_turns(oc) == MAX_NUDGES + 1 == 4
    assert _decision(events) == "the model replied but didn't change any files — try rephrasing or a smaller step"


def test_a_nudge_that_lands_on_the_last_try_still_finishes_clean(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        _wrote_nothing(), _wrote_nothing(), _wrote_nothing(),
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
    ])

    events = list(orch.build_stream("add a chart"))

    assert _agent_turns(oc) == 4
    assert _decision(events) == "typecheck clean"


# ---- the runtime fix: an app that typechecks and throws on render ------------------------------


def test_one_runtime_fix_costs_exactly_one_extra_agent_turn(tmp_path: Path, monkeypatch):
    _crashes(monkeypatch, 1)
    orch, oc = _orch(tmp_path, [
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
        _writes(1),
    ])

    events = list(orch.build_stream("add a chart"))

    assert _agent_turns(oc) == 2
    assert _decision(events) == "typecheck clean"
    # The fix turn carries the throw, not a restatement of the request — the agent has no other way
    # to see it, since tsc passed and the crash only exists in the browser.
    assert "toFixed" in oc.prompts[1]["text"]


def test_a_crash_that_is_never_fixed_spends_the_whole_runtime_budget(tmp_path: Path, monkeypatch):
    _crashes(monkeypatch, 10)
    orch, oc = _orch(tmp_path, [
        Turn(text="Built it.", writes={"src/App.tsx": "export default () => null\n"}),
    ] + [_writes(i) for i in range(10)])

    events = list(orch.build_stream("add a chart"))

    assert _agent_turns(oc) == MAX_RUNTIME_FIXES + 1 == 4
    # Nothing is reverted and the build is not failed over it: a crash Sage cannot get the agent to
    # fix still leaves the app the person can see and keep working on.
    assert _decision(events) == "typecheck clean"


# ---- the leak fix: attached data copied into src/ ----------------------------------------------


def test_a_leak_that_is_never_fixed_spends_the_whole_leak_budget(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Built it.", writes={"src/sales.csv": LEAKED_CSV}),
    ] + [_writes(i) for i in range(10)])
    orch.upload_file("sales.csv", LEAKED_CSV.encode())

    events = list(orch.build_stream("chart the sales data"))

    assert _agent_turns(oc) == MAX_LEAK_FIXES + 1 == 3
    assert len([e for e in events if e.get("type") == "data-leak"]) == MAX_LEAK_FIXES
    assert _decision(events) == "typecheck clean"


# ---- the gateway fix: a raw fetch around askModel -----------------------------------------------


def test_a_raw_gateway_call_that_is_never_rewritten_spends_the_whole_gateway_budget(tmp_path: Path):
    orch, oc = _orch(tmp_path, [
        Turn(text="Built it.", writes={"src/Chat.tsx": RAW_GATEWAY_CALL}),
    ] + [_writes(i) for i in range(10)])
    orch.bind_llm_alias("id-sonnet")

    events = list(orch.build_stream("add a chat box"))

    assert _agent_turns(oc) == MAX_GATEWAY_FIXES + 1 == 3
    assert len([e for e in events if e.get("type") == "gateway-call"]) == MAX_GATEWAY_FIXES
    assert _decision(events) == "typecheck clean"


# ---- the ceiling: every budget spent on one turn ------------------------------------------------


def test_the_four_budgets_add_up_and_nothing_bounds_their_sum(tmp_path: Path, monkeypatch):
    """The number that matters for cost, and the one nobody could name before this test.

    The budgets are four separate counters on one loop, and a turn that trips all four spends all
    four in series. The circuit breaker does not stand between them: it is asked for a decision on
    the typecheck report, and every pass here typechecks clean, so it answers "typecheck clean"
    before it ever looks at its own `max_iterations`. The `done` decision below is that same
    sentence on the eleventh pass, which is how this test says so. So the ceiling is the sum of the
    four, not the breaker's 15 — and it stays the sum if any of them is raised.
    """
    _crashes(monkeypatch, MAX_RUNTIME_FIXES)
    orch, oc = _orch(tmp_path, [_wrote_nothing()] * MAX_NUDGES + [
        Turn(text="Built it.", writes={"src/sales.csv": LEAKED_CSV, "src/Chat.tsx": RAW_GATEWAY_CALL}),
    ] + [_writes(i) for i in range(12)])
    orch.upload_file("sales.csv", LEAKED_CSV.encode())
    orch.bind_llm_alias("id-sonnet")

    events = list(orch.build_stream("chart the sales data and add a chat box"))

    # 3 nudges + the turn that finally writes + 3 runtime fixes + 2 leak fixes + 2 gateway fixes.
    expected = MAX_NUDGES + 1 + MAX_RUNTIME_FIXES + MAX_LEAK_FIXES + MAX_GATEWAY_FIXES
    assert _agent_turns(oc) == expected == 11
    assert _decision(events) == "typecheck clean"
    # And in that order, one defect per pass: `continue` means only one fix fires per iteration, so
    # the leak and the raw call sitting in the tree together are never folded into one message. The
    # `why` on each span is what /api/diag/timing shows, so this is also the readout a deployment
    # would be read back from.
    whys = [s.fields.get("why") or "" for s in timing.recent(1)[0].spans
            if s.name.startswith("agent-turn.")]
    assert whys[0] == "first send"
    kinds = [next((k for k in ("no code", "runtime", "data/", "askModel") if k in w), None)
             for w in whys[1:]]
    assert kinds == (["no code"] * MAX_NUDGES + ["runtime"] * MAX_RUNTIME_FIXES
                     + ["data/"] * MAX_LEAK_FIXES + ["askModel"] * MAX_GATEWAY_FIXES), whys
