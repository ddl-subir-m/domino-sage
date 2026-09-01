"""A refused @mention arrives with the fix attached (#135).

WHAT WAS MISSING. `mentions-unresolved` said the true thing and then sent the reader away: open the
Resources panel, find the row, take the act, come back, retype the prompt. Five steps to close a gap
the transcript had already identified precisely — and the sentence is prose, so nothing on the page
could act on it.

WHAT THIS ASSERTS. The event now carries rows beside the prose: one per drop somebody can close in
ONE act, each naming the Binding identity, the label the creator picked, and the app the act lands
in (ADR-0008 — a Project holds many Built Apps, so "this app" is never enough). No new event type,
because a transcript written before this shipped has the sentence and no rows, and that has to go on
reading as the sentence.

AND WHAT IT MUST NOT DO. Nothing here binds. Every act is a click a person makes (ADR-0010), and the
two kinds that cannot finish in one click — a Data Source, whose Binding carries a Scope chosen by
standing in the cascade (ADR-0011, #129), and a Model API, which needs an access token first — open
their door and stop rather than offering a bind the server would refuse. The buttons are drawn only
on the frame that arrived live, and only while the app the refusal named is still the one selected:
every act behind a button resolves the app on screen NOW, and switching Built App mid-build is
allowed (#77), so the two ways a card can end up acting on an app it did not name are closed
together — one across a reload, one inside a session.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS, Binding
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """The scope classifier is the only caller on this path; BUILD keeps it out of the way."""

    def route(self, request, labels):
        body = json.dumps({"choices": [{"delta": {"content": "BUILD"}}]})
        yield f"data: {body}\n\ndata: [DONE]\n\n".encode()


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(Orchestrator, "_await_runtime_error", lambda *a, **k: None)


def _orch(tmp: Path) -> Orchestrator:
    template = tmp / "template"
    (template / "src").mkdir(parents=True, exist_ok=True)
    (template / "src" / "App.tsx").write_text("export default function App() { return null }\n")
    (template / "package.json").write_text("{}")

    ws = tmp / "mnt" / "code"
    orch = Orchestrator(
        workspace_dir=ws, template=template, gateway=ScriptedGateway(),
        catalog=ModelCatalog(sovereign_plan="s", sovereign_implement="s", sovereign_ask="s",
                             plan="p", implement="i", ask="a"),
        project_id="Sage", feedback=OkFeedback(),
        opencode_client=FakeOpenCode(ws, [Turn(text="1. Add the table")]))
    orch.project(start_preview=False)
    return orch


def _refusal(orch: Orchestrator, mentions=None, resources=None) -> dict:
    """The one `mentions-unresolved` a Build turn emits."""
    events = list(orch.build_stream("add a table of sales", mentions, resources))
    said = [e for e in events if e["type"] == "mentions-unresolved"]
    assert len(said) == 1, f"expected one mentions-unresolved event, got {said}"
    return said[0]


# ---- the rows the card hangs its buttons on ---------------------------------------------------


def test_one_turn_reports_an_unbound_resource_and_an_unattached_chat_file(tmp_path: Path):
    """Both drop reasons in one turn, because that is the turn that used to be hardest to read: two
    sentences of prose, and no way to tell which half each direction belonged to."""
    orch = _orch(tmp_path)
    orch.project(start_preview=False).workspace.set_display_name("Gong sentiment")

    ev = _refusal(
        orch,
        mentions=[".sage/scratch/events.csv"],
        resources=[{"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet"}])

    app_id = orch.project(start_preview=False).app_for_turn().app_id
    assert ev["entries"] == [
        {"kind": "file", "id": ".sage/scratch/events.csv", "name": "events.csv",
         "app": "Gong sentiment", "appId": app_id},
        {"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet",
         "app": "Gong sentiment", "appId": app_id},
    ]
    # The rows are the sentence's other half, not a second opinion of it.
    assert "@events.csv" in ev["message"] and "@sonnet" in ev["message"]


def test_the_row_names_the_app_the_rail_names(tmp_path: Path):
    """`display_name` is "" until somebody renames an app, which is most apps most of the time. The
    button quotes the app back at the reader — "Use in {app}" — so it has to call it what the rail
    calls it, or it names a row that is not on screen."""
    orch = _orch(tmp_path)

    ev = _refusal(orch, resources=[{"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}])

    assert ev["entries"] == [
        {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse", "app": "Unnamed Built App",
         "appId": orch.project(start_preview=False).app_for_turn().app_id}]


def test_a_path_that_resolves_to_nothing_keeps_the_sentence_and_gets_no_button(tmp_path: Path):
    """A Chat file has a one-click fix — promoting it onto a Dataset attaches it to the app — and a
    workspace path pointing at nothing has none. Offering one anyway would be the dead end this card
    exists to remove, so that drop stays prose."""
    orch = _orch(tmp_path)

    ev = _refusal(orch, mentions=["public/data/gone.csv"])

    assert "@gone.csv" in ev["message"] and "not attached to this app" in ev["message"]
    assert ev["entries"] == []


def test_the_same_resource_mentioned_twice_is_one_row(tmp_path: Path):
    """"@Warehouse and @FCT_USAGE_DAILY" names one Data Source at one table. Two rows would draw the
    same button twice and offer the same bind twice."""
    orch = _orch(tmp_path)
    ref = {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}

    ev = _refusal(orch, resources=[ref, {**ref, "table": "FCT_USAGE_DAILY"}])

    assert [e["id"] for e in ev["entries"]] == ["ds1"]


def test_a_recorded_resource_leaves_neither_a_sentence_nor_a_row(tmp_path: Path):
    """Both halves stop together, because both are read off the same Binding list the turn honors.
    A card that went on offering "Use in {app}" for a Binding that exists would be asking someone to
    fix something that is not broken."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    proj.workspace.update_bindings(
        lambda entries: [*entries,
                         Binding(KIND_DATA_SOURCE, "ds1", "Warehouse", "Warehouse").to_dict()])

    events = list(orch.build_stream(
        "add a table of sales", None, [{"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}]))

    assert [e for e in events if e["type"] == "mentions-unresolved"] == []


def test_the_rows_survive_into_the_transcript_under_the_type_that_was_always_there(tmp_path: Path):
    """No new event type: the reload path reads one branch, and a row written before this shipped
    still lands in it. The rows have to be in the persisted copy too — a card that only ever had
    them live would lose the sentence's shape on the reload that follows the refusal."""
    orch = _orch(tmp_path)
    _refusal(orch, resources=[{"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet"}])

    rows = [r for r in orch.project(start_preview=False).app_for_turn().read_history()
            if r["type"] == "mentions-unresolved"]

    assert len(rows) == 1
    assert [e["id"] for e in rows[0]["entries"]] == ["al_1"]
    # And the record carries no `live`: what the card reads to decide whether to draw a button is
    # written by the client onto the SSE frame alone (see applyBuildEvent).
    assert "live" not in rows[0]


# ---- the card, and the acts its buttons call ---------------------------------------------------
#
# Source assertions rather than a DOM harness, the way the composer's mention suite pins its own
# shape: what is at risk here is the wiring — a branch that never runs, a button whose act does not
# exist, a live check that got dropped — and every one of those is visible in the source.


def _js(*parts: str) -> str:
    return (Path(__file__).resolve().parents[1]
            / "sage" / "workbench" / "js" / Path(*parts)).read_text()


def test_the_transcript_builds_a_card_out_of_the_rows_rather_than_a_status_line():
    store = _js("store.js")

    assert "type: 'mentions_unresolved'," in store
    assert "entries: ev.entries || []," in store
    assert "live: !!ev.live," in store
    blocks = _js("components", "message-blocks.js")
    assert "case 'mentions_unresolved':" in blocks
    assert "h(MentionsUnresolved, { block })" in blocks


def test_only_the_frame_that_arrived_this_session_carries_buttons():
    """The invariant the three offers beside it keep. A replayed refusal draws the status line it
    always drew, so a reloaded transcript reads exactly as it did before this shipped."""
    store = _js("store.js")
    assert ("|| ev.type === 'build-stalled' || ev.type === 'mentions-unresolved') ev.live = true;"
            in store)

    blocks = _js("components", "message-blocks.js")
    assert "const fixes = block.live" in blocks
    assert "if (!fixes.length) {" in blocks
    assert "return h('div', { className: 'sw-status-line is-err' }, block.message);" in blocks


def test_a_card_naming_an_app_the_rail_has_moved_off_stands_down():
    """The second half of the live rule, and the one a reload does not cover. `bindToApp` and the
    promote behind the file act both resolve the selected app at click time, while the label was
    frozen when the turn was refused — so a creator who switches Built App mid-build would read
    "Use in Gong sentiment" and bind somewhere else. The row carries the app's id for exactly this
    comparison; without a match the card is a record again."""
    blocks = _js("components", "message-blocks.js")

    assert "function mentionFixes(entries, activeAppId) {" in blocks
    assert "if (!entry.appId || entry.appId !== activeAppId) return null;" in blocks
    assert "? mentionFixes(block.entries, activeApp && activeApp.id)" in blocks


def test_the_card_offers_one_act_per_kind_and_names_the_app_each_one_lands_in():
    blocks = _js("components", "message-blocks.js")

    # An Alias binds in one click; the other two open the door their kind needs first.
    assert "llm_alias: (e) => ({ label: `Use in ${e.app}`, act: () => SW.store.bindAliasFromMention(e) })" in blocks
    assert "label: `Choose a Scope in ${e.app}`," in blocks
    assert "act: () => SW.store.openScopeForMention(e)," in blocks
    assert "act: () => SW.store.openCredentialForMention(e)," in blocks
    assert "file: (e) => ({ label: `Attach to ${e.app}`, act: () => SW.store.attachFileForMention(e) })" in blocks
    # A Model API's token is stored per model and outlives any one Binding, so that one label names
    # the Resource rather than an app.
    assert "label: 'Add its access token'," in blocks
    # A kind with no door here draws no button rather than a broken one.
    assert "if (!make || !entry.id || !entry.app) return null;" in blocks


def test_the_four_acts_are_the_store_s_and_reach_the_doors_that_already_exist():
    """They live in the store rather than in the card because the compose-time guard offers the same
    fixes before send (#136) — two copies of "what does an unbound Alias need" would drift."""
    store = _js("store.js")

    # The Alias's bind goes through `bindToApp`, carrying the Binding identity and not the prefixed
    # row id: the bare pair is what the route resolves.
    assert "bindAliasFromMention(entry) {" in store
    assert "bindingKey: [entry.kind, entry.id] });" in store
    # The Data Source opens the cascade at that Resource. The counter is what lets a second ask
    # reopen a row somebody collapsed.
    assert "openScopeForMention(entry) {" in store
    assert "state.cascadeResourceId = SW.util.bindingId({ kind: entry.kind, id: entry.id });" in store
    assert "state.cascadeSeq += 1;" in store
    # The Model API routes into the credential flow rather than offering a bind the server refuses.
    # And it does NOT set `panelFilter`, which draws "Pick a {kind} to continue" over the list — a
    # different instruction from the one the button and the sentence give.
    assert "openCredentialForMention(entry) {" in store
    assert "panelFilter = 'model_predictive'" not in store
    # The Chat file lands on the server's own default target, because a refusal offers one click and
    # has no list of Datasets to show.
    assert "attachFileForMention(entry) {" in store
    assert "store.addScratchToDataset({ id: `file:${path}`, name, path }, '', { quiet: true })" in store
    # Nothing here records a Binding on its own: the two that cannot finish in one click set panel
    # state and return, and the one that can hands off to the act the panel's own menu calls.
    assert "SW.api.bind(" not in store[store.index("bindAliasFromMention"):store.index("// Out of the selected Built App")]


def test_the_panel_stands_open_where_the_cascade_was_asked_for():
    """The other end of the Data Source act. Without it the button opens a panel and leaves the
    person to find the row — which is the walk this whole card exists to remove."""
    panel = _js("components", "resource-panel.js")
    assert "setExpandedId(cascadeResourceId);" in panel
    assert "}, [cascadeSeq]);" in panel
    # An expanded row hidden behind a leftover search term or a collapsed group is a button that
    # appears to have done nothing.
    assert "setQuery('');" in panel
    assert "setCollapsed({});" in panel
