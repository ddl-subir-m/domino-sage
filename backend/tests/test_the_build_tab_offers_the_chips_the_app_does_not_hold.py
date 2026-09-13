"""The bar that offers a Conversation's chips to the selected app, and the mark on the rest (#275).

A chip is Conversation data and must keep being drawn in Build — the Dataset chip is what arms the
sensitivity lock, so hiding chips there would relax the lock with nothing on screen to say so. What
was wrong is that the chips reached Build as decoration: the app held none of them and nothing said
which.

One offer and one mark, and both are the SAME question — the one `unusableMentions` already asks of
a typed token, asked per chip instead: does the selected app hold this, in its Bindings or in its own
files? Asking it per render is what makes the answer re-ask itself when somebody switches app.

THE ALTERNATIVE THIS RULES OUT: crossing on arrival. A Binding is a person's pick (ADR-0010), and
walking into Build is not one — so the bar offers, and the button is the click.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_crossing_offer_harness.mjs"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not on PATH (it is in the Sage image)")

APP = {"id": "app_a", "name": "Desk exposure"}

# One chip of each kind the crossing can move, in the shape `api.js` hands the store.
WAREHOUSE = {"id": "ctx_1", "resourceId": "data_source:ds_1", "resourceName": "Snowflake-Warehouse",
             "resourceKind": "datasource", "bindingKey": ["data_source", "ds_1"]}
DATASET_FILE = {"id": "ctx_2", "resourceId": "dsfile:train.csv", "resourceName": "train.csv",
                "resourceKind": "file", "datasetId": "ds_sales_2026",
                "datasetRelPath": "train.csv"}
UPLOAD = {"id": "ctx_3", "resourceId": "file:.sage/scratch/uploads/sales.csv",
          "resourceName": "sales.csv", "resourceKind": "file",
          "path": ".sage/scratch/uploads/sales.csv"}
# An Artifact chip: a chart this Conversation made. The crossing has no act for it, so the bar must
# not name it — a button that offers to move something and moves nothing is worse than no button.
CHART = {"id": "ctx_4", "resourceId": "artifact:by-desk", "resourceName": "By desk",
         "resourceKind": "artifact"}


_COMPOSER = Path(__file__).resolve().parent / "js" / "composer_context_harness.mjs"

# A Dataset file picked out of the rail, in the shape the panel drops onto the composer.
DROPPED = {"id": "dsfile:ds_1:a.csv", "name": "a.csv", "kind": "file",
           "datasetId": "ds_1", "datasetRelPath": "a.csv", "path": ".sage/chat-data/ds_1/a.csv"}


def _run(cases: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"cases": cases}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _composer(steps: list[dict]) -> list[dict]:
    """The real Composer, mounted twice off one store — Chat's props and Build's.

    Rendered rather than asked: a bar the store computes and the component never draws looks exactly
    like a bar that works, and which mount draws it is a branch off `showMode`.
    """
    out = subprocess.run(["node", str(_COMPOSER)], input=json.dumps(steps),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---- the offer ---------------------------------------------------------------------------------


@needs_node
def test_the_bar_names_every_chip_the_app_does_not_hold():
    """Criterion 1. An app that holds nothing: all three movable chips are named, by the tokens
    somebody would type for them, and the count agrees with the list."""
    got = _run([{"app": APP, "chips": [WAREHOUSE, DATASET_FILE, UPLOAD]}])[0]

    assert got["notInApp"] == ["Snowflake-Warehouse", "train.csv", "sales.csv"]
    assert got["offer"]["count"] == 3
    assert "Desk exposure" in got["offer"]["text"]
    assert "@Snowflake-Warehouse" in got["offer"]["text"]
    assert "@train.csv" in got["offer"]["text"]
    assert got["offer"]["label"] == "Add them"


@needs_node
def test_the_bar_names_no_chip_the_app_already_holds():
    """The same question `unusableMentions` asks, and the same two lists: a bound Resource and a
    file under the app's own `public/data/` are both held. Offering either is a click that appears
    to do nothing."""
    got = _run([{
        "app": APP,
        "chips": [WAREHOUSE, DATASET_FILE, UPLOAD],
        "bindings": [{"kind": "data_source", "id": "ds_1", "name": "Snowflake-Warehouse"}],
        "attached": [{"path": "public/data/sales_2026/train.csv"}],
    }])[0]

    assert got["notInApp"] == ["sales.csv"]
    assert got["offer"]["count"] == 1
    assert got["offer"]["label"] == "Add it"


@needs_node
def test_a_conversation_whose_chips_all_crossed_gets_no_bar():
    """Criterion 4. Arriving through the handoff sheet is exactly this state — everything crossed
    already — so the door that crossed nothing must be silent where the door that crossed
    everything has been."""
    got = _run([{
        "app": APP,
        "chips": [WAREHOUSE, UPLOAD],
        "bindings": [{"kind": "data_source", "id": "ds_1"}],
        "attached": [{"path": "public/data/sales_2026/uploads/sales.csv"}],
    }])[0]

    assert got["notInApp"] == []
    assert got["offer"] is None


@needs_node
def test_an_empty_conversation_gets_no_bar():
    """No chips, nothing to offer. The bar is drawn from the same guard as the chip row."""
    assert _run([{"app": APP, "chips": []}])[0]["offer"] is None


@needs_node
def test_a_chip_nothing_can_move_is_never_offered():
    """An Artifact is Conversation output, not a dependency the crossing knows how to record. It
    stays on screen and out of the bar, which is what `unusableMentions` does with a path it has no
    act for."""
    got = _run([{"app": APP, "chips": [CHART, UPLOAD]}])[0]

    assert got["notInApp"] == ["sales.csv"]
    assert "By desk" not in got["offer"]["text"]


@needs_node
def test_a_file_taken_back_out_of_the_app_is_offered_again():
    """`attachedApp` records where bytes WENT and no detach ever clears it. So the question is put to
    the app's live lists, never to that field: a file removed from the app in the Data panel is one
    the app does not hold, and a chip that went on reading as held would be refused by the next turn
    with no way back from Build — the gap this whole ticket is about."""
    chip = dict(DATASET_FILE, attachedApp="app_a")

    got = _run([{"app": APP, "chips": [chip]}])[0]

    assert got["notInApp"] == ["train.csv"]
    assert got["offer"]["count"] == 1


@needs_node
def test_no_app_selected_asks_nothing():
    """Build with no app selected has no app to name, and a bar reading "not in undefined" is
    worse than no bar."""
    assert _run([{"app": None, "chips": [WAREHOUSE, UPLOAD]}])[0]["offer"] is None


# ---- switching app re-asks ---------------------------------------------------------------------


@needs_node
def test_switching_app_re_asks_the_newly_selected_one():
    """Criterion 5. Two apps, the same Conversation: the first holds the warehouse, the second
    holds the file. The answer is derived per render off the lists that arrive with the app, so the
    switch re-asks rather than keeping the first app's answer under the second app's name."""
    first, second = _run([
        {"app": APP, "chips": [WAREHOUSE, UPLOAD],
         "bindings": [{"kind": "data_source", "id": "ds_1"}]},
        {"app": {"id": "app_b", "name": "Daily P&L"}, "chips": [WAREHOUSE, UPLOAD],
         "attached": [{"path": "public/data/sales_2026/uploads/sales.csv"}]},
    ])

    assert first["notInApp"] == ["sales.csv"]
    assert "Desk exposure" in first["offer"]["text"]
    assert second["notInApp"] == ["Snowflake-Warehouse"]
    assert "Daily P&L" in second["offer"]["text"]


# ---- the click ---------------------------------------------------------------------------------


@needs_node
def test_the_click_crosses_and_the_bar_goes_quiet():
    """Criterion 2, from the button. One press, one request, and the bar answers off the lists the
    refresh read back — not off the answer it was handed."""
    got = _run([{
        "app": APP, "conversation": "conv_1", "press": True,
        "chips": [WAREHOUSE, UPLOAD],
        "answer": {"ok": True, "crossed": ["sales.csv"], "refused": []},
        "boundAfter": [{"kind": "data_source", "id": "ds_1"}],
        "attachedAfter": [{"path": "public/data/sales_2026/uploads/sales.csv"}],
    }])[0]

    assert got["posted"] == ["conv_1"]
    assert got["notInAppAfter"] == []
    assert got["offerAfter"] is None
    # And the lock is asked again, because this click can arm it: the crossing attaches files under
    # `public/data/` and binds Datasets, so a declared Dataset that was in nobody's scope can be in
    # this app's on the way out. Reading only the app's two lists would leave the picker offering a
    # model the router then refuses under the person.
    assert got["asked"] == ["sensitivity"]


@needs_node
def test_a_refused_chip_stays_named_and_the_moved_ones_do_not():
    """Criterion 3. Crossing is per chip: the Binding lands while the Upload is refused for want of
    a writable Dataset. The bar must not claim the refused one moved, and the mark on it must carry
    its own reason rather than the one sentence covering both."""
    got = _run([{
        "app": APP, "press": True,
        "chips": [WAREHOUSE, UPLOAD],
        # `appId` as the server sends it, because that is what the refusal is filed under.
        "answer": {"ok": True, "appId": "app_a", "appName": "Desk exposure",
                   "crossed": ["Snowflake-Warehouse"],
                   "refused": [{"name": "sales.csv",
                                "reason": "sales.csv stayed in Chat — no writable Dataset is "
                                          "mounted here"}]},
        "boundAfter": [{"kind": "data_source", "id": "ds_1"}],
        "attachedAfter": [],
    }])[0]

    assert got["notInAppAfter"] == ["sales.csv"]
    assert got["offerAfter"]["count"] == 1
    # Asserted off the STORE, not off the answer: the answer is this harness's own fixture, and what
    # the chip's mark reads is the map the store kept — filed under the app the SERVER wrote into, so
    # a switch cannot draw one app's refusal on another app's chip.
    assert got["refusedState"]["appId"] == "app_a"
    assert got["refusedState"]["byName"]["sales.csv"].startswith("sales.csv stayed in Chat")


# ---- drawn, in Build, on the real component ----------------------------------------------------


@needs_node
def test_the_bar_and_the_mute_are_drawn_in_build_and_nowhere_else():
    """A chip added in Chat, read off both mounts. Build draws the bar and mutes the chip; Chat draws
    neither, because in Chat there is no app the chip could be missing from — the same reason
    `unusableMentions` is a Build-only warning."""
    after = _composer([{"drop": {"on": "chat", "resource": DROPPED}}])[-1]

    assert "a.csv" in after["chat"] and "a.csv" in after["build"], "the chip is drawn in both modes"
    assert after["buildMuted"] == ["a.csv"]
    assert "a.csv" in after["buildBar"]
    assert "Alpha" in after["buildBar"]
    assert after["chatMuted"] == []
    assert after["chatBar"] == ""


@needs_node
def test_the_chip_whose_bytes_this_app_took_is_not_muted_or_offered():
    """The same drop in Build attaches the bytes and records which app took them (ADR-0048), so this
    app holds it. Muting it would put two marks on one chip that contradict each other: "In Alpha"
    from the receipt and "Not in Alpha" from the lists."""
    after = _composer([{"drop": {"on": "build", "resource": DROPPED}}])[-1]

    assert after["buildMarks"] == ["", "In Alpha"]
    assert after["buildMuted"] == []
    assert after["buildBar"] == ""


@needs_node
def test_the_lock_is_asked_again_where_it_is_armed_and_left_alone_where_it_is_off():
    """The read is gated the way the promote's is: a deployment with the lock turned off pays nothing
    per click, and a deployment running it is asked again because this click can arm it — it attaches
    files under `public/data/` and binds Datasets.

    Both branches, because a gate tested on one side is a gate nobody has tested: with the lock
    absent-or-on the read must go out, and with it loaded and off it must not."""
    armed, off = _run([
        {"app": APP, "press": True, "chips": [UPLOAD],
         "sensitivity": {"enabled": True, "locked": True, "approved": ["opus"], "datasets": ["x"]},
         "answer": {"ok": True, "appId": "app_a", "crossed": ["sales.csv"], "refused": []}},
        {"app": APP, "press": True, "chips": [UPLOAD],
         "sensitivity": {"enabled": False, "locked": False, "approved": [], "datasets": []},
         "answer": {"ok": True, "appId": "app_a", "crossed": ["sales.csv"], "refused": []}},
    ])

    assert armed["asked"] == ["sensitivity"]
    assert off["asked"] == []


@needs_node
def test_the_mark_that_is_withdrawn_is_actually_redrawn():
    """Attaching in Build changes what the app holds, and the store reads the app's lists back. That
    read lands after the render the click already made, so it needs a notify of its own — without one
    the corrected list is invisible and the chip keeps a mute the store has withdrawn, until some
    unrelated render happens by.

    Read off a subscription rather than by mounting on demand: a reader that mounts whenever it likes
    sees the corrected state either way, which is exactly how this class of bug survives a test."""
    after = _composer([{"drop": {"on": "build", "resource": DROPPED}}])[-1]

    assert after["appAttachments"] == ["public/data/ds_1/a.csv"]
    assert after["lastRenderMuted"] == [], "the app holds the file and the last render still muted it"


@needs_node
def test_a_refused_chip_says_why_on_the_chip_itself():
    """Criterion 3, on the chip. One press, one chip refused: the reason reaches the chip it belongs
    to, rather than living only in a toast somebody has already dismissed. `Not in <app>` alone would
    read as the same plain gap its neighbours have, when this one has been tried and refused."""
    steps = [{"drop": {"on": "chat", "resource": DROPPED}}, {"crossChips": {"refuse": "a.csv"}}]
    after = _composer(steps)[-1]

    assert any("stayed in Chat" in note for note in after["chipNotes"]), after["chipNotes"]
    assert after["buildMuted"] == ["a.csv"]
    # And the provenance it always carried is still on the same chip, after the reason.
    assert any("You added this to the conversation." in note for note in after["chipNotes"])


@needs_node
def test_a_crossing_that_was_not_refused_leaves_no_reason_behind():
    """The other half of the same mark: a chip that crossed is neither muted nor carrying a reason.
    Without this, the test above passes for a bar that marks every chip refused."""
    steps = [{"drop": {"on": "chat", "resource": DROPPED}}, {"crossChips": {}}]
    after = _composer(steps)[-1]

    assert after["buildMuted"] == []
    assert after["buildBar"] == ""
    assert not any("stayed in Chat" in note for note in after["chipNotes"])


@needs_node
def test_switching_app_mutes_the_chip_the_newly_selected_app_does_not_hold():
    """Criterion 5, on the rendered chip. The bytes went to Alpha; Beta holds nothing. Both marks are
    then true of one chip and neither contradicts the other — "In Alpha" says where bytes went, the
    mute says what is selected now."""
    steps = [{"drop": {"on": "build", "resource": DROPPED}}, {"selectApp": "app_beta"}]
    before, after = _composer(steps)[-2:]

    assert before["buildMuted"] == [] and before["buildBar"] == ""
    assert after["buildMuted"] == ["a.csv"]
    assert "Beta" in after["buildBar"]
    assert after["buildMarks"] == ["", "In Alpha"]
