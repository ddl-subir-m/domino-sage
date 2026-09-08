"""One click on a mention fix finishes the request (#213).

WHAT WAS MISSING. A refused @mention arrives with the fix attached (#135, #136, #212), and the
click wrote the record and stopped. The person then retyped the request they had already made —
which is the second act the button was supposed to have bought. #183 and #185 had already settled
the shape for this: a card's click writes the record AND replays the request, because the two are
one job. The mention fixes never got that treatment, and the report that opened this ticket is a
person asking why agreeing with a true message costs them a retype.

WHAT THIS ASSERTS. `mentions-unresolved` carries the prompt, the way `reset-offer` already did, so
the card has the request to send again — one more field on an event that exists rather than a new
event type. And `mentionFixes` takes the act that starts the turn: a fix that WRITES the record
gains it, a fix that only opens a door does not, and a bind the route refuses starts nothing.

AND THE SENTENCES THAT WENT WITH IT. A line whose row has a button no longer ends "then ask again"
— it would be describing the long way round past the button. A line with no row keeps every word,
because nothing is ever drawn behind it and shortening it would leave the dead end #135 was filed
for. The instruction is not lost: a replayed card draws no buttons on purpose (#135, #209), and the
client's no-button branch says it there.

Nothing here binds on its own. ADR-0010 is untouched — this makes the click do more, not the
mention do anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sage.feedback.runner import FeedbackReport
from sage.orchestrator.service import Orchestrator
from sage.resources.bindings import KIND_DATA_SOURCE, KIND_LLM_ALIAS
from sage.router.models import ModelCatalog

from .fake_opencode import FakeOpenCode, Turn


class OkFeedback:
    def check(self, path: Path) -> FeedbackReport:
        return FeedbackReport(ok=True, errors=[], raw="")


class ScriptedGateway:
    """Enough of an answer to reach the BUILD route and stop."""

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
        opencode_client=FakeOpenCode(ws, [Turn(text="1. Add a table")]))
    orch.project(start_preview=False)
    return orch


# ---- the request the button sends again -------------------------------------------------------


def test_the_refusal_carries_the_request_it_refused(tmp_path: Path):
    """The card has to have the prompt to send it, and `reset-offer` already carried one beside its
    message — so this is one more field, not one more event type. The mentions do not ride along:
    the server re-resolves the @tokens against the records as they stand when the replay arrives,
    which is the whole point, because the click has just changed them."""
    orch = _orch(tmp_path)

    events = list(orch.build_stream(
        "summarise the desk notes with @sonnet", None,
        [{"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet"}]))
    said = [e for e in events if e["type"] == "mentions-unresolved"]

    assert len(said) == 1
    assert said[0]["prompt"] == "summarise the desk notes with @sonnet"


def test_the_replayed_request_is_the_one_in_their_own_bubble(tmp_path: Path):
    """Their words, not ours. A typed approval reaches the agent as an expanded prompt, and the
    transcript writes what they typed — so the field the button reads has to be the same half, or
    the replay puts a sentence nobody wrote into their own voice."""
    orch = _orch(tmp_path)

    list(orch.build_stream("summarise the desk notes with @sonnet", None,
                           [{"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "sonnet"}]))
    rows = orch.project(start_preview=False).app_for_turn().read_history()
    asked = [r for r in rows if r["type"] == "user"]
    card = [r for r in rows if r["type"] == "mentions-unresolved"]

    assert card[0]["prompt"] == asked[-1]["text"]
    # And it survives the reload, because the reload is when the card is most likely to be read.
    assert "live" not in card[0]


# ---- what the sentence says now, and what it stopped saying ------------------------------------


def test_a_line_with_a_button_behind_it_stops_giving_directions(tmp_path: Path):
    """The five-step path the button replaces, and after #213 the button also builds — so the tail
    described a longer way round than the thing sitting next to it."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)
    proj.workspace.set_display_name("Sales dashboard")

    said, rows = orch._unusable_mentions(
        proj, None, [".sage/scratch/events.csv"],
        [{"kind": KIND_LLM_ALIAS, "id": "al_1", "name": "gpt-5-4"},
         {"kind": KIND_DATA_SOURCE, "id": "ds1", "name": "Warehouse"}])

    assert "Couldn't use @events.csv — a Chat file lives outside this app." in said
    assert "Sales dashboard can't call @gpt-5-4 yet." in said
    assert "Couldn't use @Warehouse — Sales dashboard doesn't use it yet." in said
    assert "then ask again" not in said
    assert "in the list of what it ships" not in said
    # Three drops, three rows: every shortened line has a button standing behind it.
    assert len(rows) == 3


def test_a_line_with_no_button_behind_it_keeps_every_word(tmp_path: Path):
    """The dead end #135 was filed for. A workspace path that resolves to nothing has no act to
    offer, so it gets no row — and a row is the only thing that earns a line the right to stop
    saying where to go."""
    orch = _orch(tmp_path)
    proj = orch.project(start_preview=False)

    said, rows = orch._unusable_mentions(proj, None, ["public/data/gone.csv"], None)

    assert said == ("Couldn't use @gone.csv — not attached to this app. "
                    "Attach it in the Data panel, then ask again.")
    assert rows == []


# ---- the click, and what follows it ------------------------------------------------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (it is in the Sage image)"
)

# `app_c` binds one LLM Alias and nothing else, so each of these is a Resource this app really
# cannot reach — the only state a refusal card is ever drawn in.
ON = {"thread": "thr_many", "select": "app_c"}
APP = "Rate curve viewer"
SOURCE = {"kind": "data_source", "id": "ds_9", "name": "Risk warehouse"}
MODEL_API = {"kind": "model_api", "id": "ma_1", "name": "Churn risk"}


def _fix(entry: dict, **step) -> dict:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps([{"fixMention": entry, **ON, **step}]),
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])[-1]


@needs_node
def test_the_bind_is_followed_by_the_request_that_was_refused():
    """Two acts, one click. The record stands whether or not the build after it succeeds — they run
    in that order for the same reason #183 ran them in that order, and the label says both."""
    step = _fix(SOURCE, replay=True)

    assert step["labels"] == [f"Use in {APP} and build"]
    assert step["posted"] == [{"kind": "data_source", "id": "ds_9"}]
    assert step["replayed"] == 1


@needs_node
def test_a_refused_bind_sends_nothing():
    """The record is what decides. A turn sent on top of a Binding the route turned down would walk
    into the refusal the click was supposed to have cleared, and read as a second failure of the
    request rather than the first failure of the fix."""
    step = _fix(SOURCE, replay=True, refuse="Nope")

    assert step["replayed"] == 0


@needs_node
def test_the_act_that_only_opens_a_door_still_only_opens_a_door():
    """A Model API needs its access token before Sage will record a Binding for it, so that click
    leaves the gap exactly where it was. Building behind it would meet the same refusal wearing a
    longer label."""
    step = _fix(MODEL_API, replay=True)

    assert step["labels"] == ["Add its access token"]
    assert step["replayed"] == 0
    assert step["posted"] == []


@needs_node
def test_a_card_with_nothing_to_send_offers_the_button_it_always_had():
    """A live card whose event predates the prompt field. A label promising a build that nothing
    would start is the dead end #135 exists to close, one word longer."""
    step = _fix(SOURCE)

    assert step["labels"] == [f"Use in {APP}"]
    assert step["replayed"] == 0


# ---- the wiring on both surfaces ---------------------------------------------------------------
#
# Source assertions, the way the two suites beside this one pin their own shape: what is at risk is
# which act each surface hands the shared map, and that is visible in the source.


def _js(*parts: str) -> str:
    return (_JS / Path(*parts)).read_text()


def test_the_refusal_card_sends_the_prompt_it_was_given_and_nothing_when_it_has_none():
    store = _js("store.js")
    blocks = _js("components", "message-blocks.js")

    assert "prompt: ev.prompt || ''," in store
    assert "block.prompt ? () => SW.store.sendBuildPrompt(block.prompt) : null)" in blocks
    # Straight to `sendBuildPrompt` with no skip flags: this turn RAN and the agent declined it, so
    # the click starts a fresh turn rather than resuming a paused one — unlike every named answer
    # beside it, which is a gate being settled.
    assert "skipResetGate" not in blocks


def test_the_composer_button_sends_what_is_in_the_box():
    """Decided rather than assumed: binding and leaving the person to press Send is the second click
    this was reported for. It goes through the composer's own send, so the box clears and the turn
    starts exactly as it would have without the warning."""
    ui = _js("components", "composer.js")

    assert "const fixes = SW.store.mentionFixes(entries, activeAppId, onSend);" in ui
    assert "onSend: send," in ui
    # Returned rather than dropped, so the spinner on the button lasts as long as what it waits for.
    assert "return onSend(value);" in ui


def test_one_map_still_draws_both_surfaces():
    """#135's rule, unchanged by this. Two copies of "what does an unbound Alias need" would drift,
    and the replay is a third thing they would drift about."""
    store = _js("store.js")
    ui = _js("components", "composer.js")

    assert "mentionFixes(entries, activeAppId, replay) {" in store
    assert "const withReplay = (fix) => (!replay || !fix.sends ? fix : {" in store
    assert "label: `${fix.label} and build`," in store
    assert "act: () => Promise.resolve(fix.act()).then((ok) => (ok ? replay() : ok))," in store
    assert "mentionFixes" in ui and "MENTION_FIX" not in ui


def test_the_replayed_card_says_where_to_go_since_it_cannot_take_you():
    """The other half of moving the instruction out of the prose. A replayed refusal draws no
    buttons on purpose, so the branch that knows that is the one that says it — and it says it in
    the words the server used to, naming the app because a Project holds many Built Apps
    (ADR-0008)."""
    store = _js("store.js")
    blocks = _js("components", "message-blocks.js")

    assert "mentionFixHints(entries) {" in store
    assert ("const ships = (e) => `Choose Use in ${e.app} in the list of what it ships, "
            "then ask again.`;") in store
    assert "file: (e) => `Attach it to ${e.app} in the Data panel, then ask again.`," in store
    assert "const hints = SW.store.mentionFixHints(block.entries);" in blocks
    assert "[block.message, ...hints].join(' '));" in blocks
