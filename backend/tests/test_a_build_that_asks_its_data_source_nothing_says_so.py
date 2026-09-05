"""A build that ends green over a store it never queried says so, to the person and to the agent.

THE LIVE FAILURE. A creator asked for a dashboard over the Anthropic API usage table in a Snowflake
Data Source, and picked the source without picking a table. The build shipped a complete dashboard —
KPI cards, three charts, a sortable table, a detail panel — on rows the model had written itself:
`claude-3-opus`, `evt_1012`, `devon.singh`. Typecheck passed. No query existed, so no query could
fail, and the turn reported "Done — build is clean". Nothing anywhere disagreed.

WHY THE ANSWER WAS ALREADY THERE, AND STILL UNSAID. `_resource_usage` (#93) computes exactly this as
a by-product, and `.sage/usage.json` records it. But it runs in the turn's `finally`, strictly AFTER
`_build_stream` has yielded its terminal `done`, and by design it is a label on the Resources row
rather than anything the turn says. So the one surface the creator was actually reading stayed quiet.

WHY THIS IS NOT THAT SCAN. `_data_sources_never_asked` is one local read of `.sage/queries.json`. It
costs no source walk, because a Data Source with no query recorded against it cannot be reached by
any code — which is why `_resource_usage` returns before walking the tree in exactly this case.

WHAT IT MAY NOT BECOME. A gate. ADR-0010 keeps the declaration authoritative for publish, bind and
unbind, and its rule 3 is "WHAT IT CHANGES. Nothing." That rule is about those three acts, not about
what a turn is allowed to SAY — the last test here pins the boundary. An app whose agent has not
written the query yet is an app mid-build, and refusing it would refuse correct work.

WHY IT IS NOT A PROBLEM (ADR-0027). A Problem is standing — true the moment the Workbench opens —
and this becomes true only when a turn ends. It gets no chip, no drawer entry, and no red.
"""
from __future__ import annotations

import json
from pathlib import Path

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import _PERSISTED_EVENTS, Orchestrator
from sage.resources.provider import FakeResourceProvider
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _template(tmp: Path) -> Path:
    t = tmp / "template"
    (t / "src").mkdir(parents=True, exist_ok=True)
    (t / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (t / "package.json").write_text('{"name": "template"}')
    (t / "AGENTS.md").write_text("# Building an app\n")
    return t


def _orch(tmp: Path, turns: list[Turn]) -> tuple[Orchestrator, FakeOpenCode]:
    oc = FakeOpenCode(tmp / "mnt" / "code", turns)
    orch = Orchestrator(workspace_dir=oc.workspace, template=_template(tmp),
                        gateway=ScriptedGateway(),
                        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s",
                                             sovereign_ask="s", plan="p", implement="i", ask="a"),
                        project_id="Sage", feedback=OkFeedback(), opencode_client=oc,
                        resources=FakeResourceProvider())
    orch.project(start_preview=False).record.write_settings({"skip_planning": True})
    return orch, oc


def _of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e.get("type") == kind]


def _catalog(orch: Orchestrator, entries: list[dict]) -> None:
    """The `.sage/queries.json` the agent is supposed to write."""
    path = orch.project(start_preview=False).workspace.path / ".sage" / "queries.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


# ---- what the person is told -------------------------------------------------------------------


def test_a_green_build_over_an_unqueried_store_does_not_finish_in_silence(tmp_path: Path):
    """The reported bug, end to end. The turn still succeeds — what it may not do is say nothing."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "export default () => null\n"})])
    orch.bind_data_source("ds-dwh")

    events = list(orch.build_stream("build me a dashboard for the anthropic api data"))

    assert _of(events, "done")[0]["ok"] is True          # it worked, and it is not being called a failure
    message = _of(events, "data-source-unasked")[0]["message"]
    assert "Snowflake-Data-Warehouse" in message
    assert "no query names it" in message
    # A remedy, or the person who owns one — the sentence is useless without it.
    assert "remove it from this app's Resources" in message


def test_the_notice_comes_before_the_turn_ends(tmp_path: Path):
    """It belongs to the turn it describes. After `done` it would attach to whatever came next, and
    on a reload it would replay in the wrong order."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")

    kinds = [e.get("type") for e in orch.build_stream("build it")]

    assert kinds.index("data-source-unasked") < kinds.index("done")


def test_an_approved_plan_is_told_the_same_thing(tmp_path: Path):
    """The path the reported bug actually took: the creator was shown a plan, approved it, and the
    build that followed ran without planning again. An approve IS a build turn — `_approve_locked`
    yields from the same generator — but the flow that produced the invented dashboard is worth
    holding down rather than inferring from a delegation."""
    orch, _oc = _orch(tmp_path, [Turn(text="1. Add the table\n2. Wire up the data"),
                                 Turn(writes={"src/App.tsx": "// the table\n"})])
    orch.project(start_preview=False).record.write_settings({"skip_planning": False})
    orch.bind_data_source("ds-dwh")

    list(orch.build_stream("build me a dashboard for the anthropic api data"))
    events = list(orch.approve_stream())

    assert _of(events, "done")[0]["ok"] is True
    assert "Snowflake-Data-Warehouse" in _of(events, "data-source-unasked")[0]["message"]


def test_an_app_that_queries_its_store_is_not_accused(tmp_path: Path):
    """The no-false-alarm test. This fires on every clean build of every app with a Data Source, so
    one wrong sentence here is a sentence the creator learns to scroll past."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")
    _catalog(orch, [{"name": "usage_by_account", "binding": "ds-dwh", "sql": "SELECT 1"}])

    events = list(orch.build_stream("build it"))

    assert _of(events, "data-source-unasked") == []
    assert _of(events, "done")[0]["ok"] is True


def test_an_app_with_no_data_source_is_never_asked_about_one(tmp_path: Path):
    """Most apps. Machinery for a store that is not there costs a line on every build."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])

    events = list(orch.build_stream("build it"))

    assert _of(events, "data-source-unasked") == []


def test_a_query_against_a_different_store_does_not_count(tmp_path: Path):
    """`binding` is what says which store a query reads. A catalog full of queries against another
    Data Source leaves this one as unread as an empty file does."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")
    _catalog(orch, [{"name": "other", "binding": "ds-pg", "sql": "SELECT 1"}])

    assert _of(list(orch.build_stream("build it")), "data-source-unasked") != []


def test_a_turn_that_failed_carries_no_second_sentence(tmp_path: Path):
    """A build cut off part-way has its own message, and this one under it would read as part of the
    fault rather than as a fact about the app."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"}, broken_write=True)])
    orch.bind_data_source("ds-dwh")

    events = list(orch.build_stream("build it"))

    assert _of(events, "done")[0]["ok"] is False
    assert _of(events, "data-source-unasked") == []


def test_the_notice_survives_a_reload(tmp_path: Path):
    """The transcript is rebuilt from the persisted events, so an event missing from the allow-list
    is one the creator sees once and never again."""
    assert "data-source-unasked" in _PERSISTED_EVENTS


def test_the_transcript_draws_it_grey_rather_than_red():
    """A frame's colour is a claim about the turn. `sw-status-line` has two states, and the red one
    says the build failed — this one did not. Worse, an `{ type: 'error' }` frame IS a failure to
    `endedBadly`, which keys on the type alone and would fetch a gateway listing over a clean build.

    Asserted against the source because the reducer's branch is the whole behaviour here, and the
    repo already pins `mentions-ambiguous`, the neutral line this one follows, the same way."""
    store = (Path(__file__).resolve().parents[1]
             / "sage" / "workbench" / "js" / "store.js").read_text()

    branch = store[store.index("ev.type === 'data-source-unasked'"):]
    # The push itself, not the reasoning above it — the comment explaining why there is no `ok`
    # key necessarily contains the words `ok: false`.
    pushed = branch[branch.index(".push("):branch.index("} else if")]
    assert "type: 'status'" in pushed
    assert "ok:" not in pushed          # neither `ok: false` nor `ok: true` — no claim at all


# ---- what the next turn's agent is told --------------------------------------------------------


def test_the_agent_is_told_its_screens_answer_from_nowhere(tmp_path: Path):
    """The other half. Without it the next turn repeats the mistake, because nothing in front of the
    model says the numbers it wrote are not the store's."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")

    list(orch.build_stream("build it"))
    agents = (orch.project(start_preview=False).workspace.path / "AGENTS.md").read_text()

    assert "This app asks nothing of its Data Sources" in agents
    assert "no query in `.sage/queries.json` names it" in agents
    assert "If a screen shows values you wrote yourself, that is the bug" in agents


def test_the_agent_is_not_told_it_once_the_query_exists(tmp_path: Path):
    """AGENTS.md is re-read every turn, so a section that outlives its reason is a standing lie."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")
    _catalog(orch, [{"name": "usage", "binding": "ds-dwh", "sql": "SELECT 1"}])

    list(orch.build_stream("build it"))
    agents = (orch.project(start_preview=False).workspace.path / "AGENTS.md").read_text()

    assert "asks nothing of its" not in agents


# ---- and what it still does not do -------------------------------------------------------------


def test_saying_so_does_not_make_it_a_gate(tmp_path: Path):
    """ADR-0010's rule 3 bounds this. The Binding is the app's permission to reach the store at run
    time, and it is the creator's declaration — so an unqueried one still publishes, still stands in
    the manifest, and is removed only by the deliberate act of unbinding it."""
    orch, _oc = _orch(tmp_path, [Turn(writes={"src/App.tsx": "x\n"})])
    orch.bind_data_source("ds-dwh")

    list(orch.build_stream("build it"))

    recorded = json.loads(
        (orch.project(start_preview=False).workspace.path / ".sage" / "bindings.json").read_text())
    assert [b["id"] for b in recorded if b["kind"] == "data_source"] == ["ds-dwh"]
