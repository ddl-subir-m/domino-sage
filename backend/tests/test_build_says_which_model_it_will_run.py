"""Build's model picker, restored (the pre-Workbench `<select id="pick">`).

The backend never stopped supporting the override: `POST /api/project/model` still takes `pick`,
and `llm_router` still returns PLAN_OVERRIDE / IMPLEMENT_OVERRIDE when one is set. The Workbench
was the gap — `applyModelStatus` kept `catalog.ask` and dropped the rest of the status, so there
was nothing for a picker to draw and Build ran on the pinned slots with no way to say otherwise.

Ask and Auto are the two modes the router will not honour a pick in: Ask is pinned to `catalog.ask`
and Auto follows the phase. Neither gets a menu, and both still name the model they will use —
"you cannot change this" is a different answer from saying nothing.
"""

import json
import subprocess
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "js" / "build_model_picker_harness.mjs"

PLAN_MODEL = "anthropic/claude-planner"
IMPLEMENT_MODEL = "anthropic/claude-builder"


def _drawn(steps: list[dict]) -> list[dict]:
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(steps),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _keys(row: dict) -> list[str]:
    flat = []
    for item in row["items"]:
        if "group" in item:
            flat.extend(c["key"] for c in item["children"])
        else:
            flat.append(item["key"])
    return flat


def test_plan_offers_the_catalog_with_its_own_slot_as_the_default():
    """What the old `renderCatalog` drew. The pinned row is the mode's own slot and it is the one
    marked, because the person is choosing what to run INSTEAD of it — a list where the current
    model is just another row does not say what "instead" means."""
    (row,) = _drawn([{"mode": "plan"}])
    assert row["offered"] is True
    assert row["label"] == f"{PLAN_MODEL} (default)"
    # `.get`, because a divider is an item with neither key nor label — JSON drops both.
    labels = [i.get("label") for i in row["items"] if "group" not in i]
    assert f"{PLAN_MODEL} (default)" in labels
    assert IMPLEMENT_MODEL in labels
    # `ask` points at the plan model in the fixture. The menu offers MODELS, so that is one row,
    # not two identical ones the person has to choose between.
    assert labels.count(f"{PLAN_MODEL} (default)") == 1
    # Two models, then the way through to the assignments (ADR-0017). The divider between them
    # carries no label, which is what the filter drops.
    assert [l for l in labels if l] == [
        f"{PLAN_MODEL} (default)", IMPLEMENT_MODEL, "Model assignments…"]


def test_implement_marks_its_own_slot_rather_than_plans():
    """The two overridable modes have different pins, and the picker is the same control in both.
    A "(default)" computed once for the screen rather than per mode would name plan's model here."""
    (row,) = _drawn([{"mode": "implement"}])
    assert row["offered"] is True
    assert row["label"] == f"{IMPLEMENT_MODEL} (default)"


def test_the_open_weight_catalog_is_offered_as_extra_options():
    """The `openai` gateway's broader list, under its own heading and never duplicating a slot that
    already points at the same model — the same reduction the four slots get above."""
    (row,) = _drawn([{"mode": "plan"}])
    (group,) = [i for i in row["items"] if "group" in i]
    assert group["group"] == "Open-weight"
    keys = [c["key"] for c in group["children"]]
    assert keys == ["deepseek/deepseek-v3", "qwen/qwen-2-5"]
    assert PLAN_MODEL not in keys  # it is already a slot, one row up


def test_the_pick_reaches_set_model():
    """The whole point of restoring the control. `pick` alone, with no `mode` beside it: the mode
    is a standing choice the picker never touched, and ModelControl.pick does not need it."""
    (row,) = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3"}])
    assert row["wrote"] == [{"pick": "deepseek/deepseek-v3"}]
    assert row["serverPick"] == "deepseek/deepseek-v3"
    # And the control now reads back the override rather than the pin it replaced.
    assert row["afterLabel"] == "deepseek/deepseek-v3"


def test_the_default_row_clears_the_override_rather_than_setting_it():
    """`(default)` is the way BACK. Sending the pinned model's id would look identical on screen
    and leave a standing override behind that survives an edit to the deployment's slots."""
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3"},
                     {"mode": "implement", "pick": "__pinned__"}])
    assert row["wrote"] == [{"pick": None}]
    assert row["serverPick"] is None
    assert row["afterLabel"] == f"{IMPLEMENT_MODEL} (default)"


def test_ask_offers_no_override_but_is_no_longer_a_dead_control():
    """`llm_router` returns ASK_PINNED without ever reading `picked_model`, so an override menu here
    would be a control that does nothing — that much is unchanged. What changed is the other half:
    the assignment behind the slot IS settable now (ADR-0017), so the chip is a door rather than a
    disabled label. A disabled control with a working door behind it is the worst of both."""
    (row,) = _drawn([{"mode": "ask"}])
    assert row["offered"] is False
    assert row["disabled"] is False
    assert row["label"] == PLAN_MODEL  # the fixture's ask slot
    # One slot, two consumers: `_resolve_chat` returns `catalog.ask` as CHAT_DEFAULT, so a person
    # setting this repoints Chat as well and has to be told before they do it.
    assert "so does Chat" in row["why"]


def test_auto_names_the_phase_it_is_in_rather_than_just_a_model():
    """Auto is the other mode the router will not honour a pick in — it answers from the phase, not
    from `picked_model`. So it gets the same door and not a menu.

    The label carries the phase because Auto has no model of its own: it runs Plan's assignment
    while it plans and Implement's while it builds, so a bare id changes under the person with
    nothing on screen to say why."""
    (row,) = _drawn([{"mode": "auto"}])
    assert row["offered"] is False
    assert row["disabled"] is False
    assert row["label"] == f"{PLAN_MODEL} · planning"
    assert PLAN_MODEL in row["why"] and IMPLEMENT_MODEL in row["why"]


def test_the_picker_closes_while_a_build_is_running():
    """A pick is not pinned for the turn the way the mode is — `ModelControl.snapshot` reads
    `_picked_model` live, so the shim would resolve the rest of this build against a new model with
    the first half's tool calls already in context. The mode pill queues instead; there is no queue
    for a pick, so the control closes and says which model the turn is on."""
    (row,) = _drawn([{"mode": "plan", "running": True}])
    assert row["offered"] is False
    assert row["disabled"] is True
    assert "This turn is running on" in row["why"]


def test_the_one_closed_state_left_can_actually_be_hovered():
    """A browser dispatches no mouse events on a disabled button, so a Tooltip wrapped straight round
    one never opens — the explanation would be written and unreachable. The wrapper is the only thing
    that makes it a sentence a person can get to.

    Only one state still needs it. Ask and Auto are live controls now, and a Tooltip on an enabled
    button opens without help; a running turn is the last thing that closes this control."""
    (running,) = _drawn([{"mode": "plan", "running": True}])
    assert running["disabled"] is True
    assert running["wrapsDisabledIn"] == "span"
    for row in _drawn([{"mode": "ask"}, {"mode": "auto"}]):
        assert row["disabled"] is False, row["step"]


def test_an_override_naming_the_pinned_model_still_reads_as_the_default():
    """Pick Plan's model while in Implement, then switch to Plan: the override now names the model
    Plan is already pinned to. Read as an override it would mark no row selected and drop the
    "(default)" off a control that is running exactly the default."""
    _, row = _drawn([{"mode": "implement", "pick": PLAN_MODEL}, {"mode": "plan"}])
    assert row["label"] == f"{PLAN_MODEL} (default)"
    assert row["selectedKeys"] == ["__pinned__"]


def test_the_open_weight_list_is_read_from_healthz():
    """/healthz is the only route the Workbench reads off `BASE`, because it is one of the two the
    orchestrator leaves unproxied. A path that drifted under /api would 404 and leave the picker
    silently short of every extra option.

    `SW.api.healthz`, not `SW.api.health`. Both were called `health` until ADR-0027, and the
    readiness probe is not the route that reports Problems."""
    (row,) = _drawn([{"health": True}])
    assert row["fetched"] == ["./healthz"]


# ---- the signing pin, which the picker cannot compute (ADR-0032) --------------------------------
SIGNING_MODEL = "google/gemini-3.7-flash"


def test_a_pinned_session_names_the_model_it_will_actually_run():
    """The picker restated the router's precedence in JS, and the pin is the one rule that copy
    could not see. Plan showed its own slot's model and the turn ran on the signing one."""
    _, row = _drawn([{"mode": "plan"}, {"mode": "plan", "signing": "implement"}])
    assert row["label"] == f"{SIGNING_MODEL} (default)"


def test_a_pinned_session_says_why_it_is_not_running_the_slot_you_assigned():
    """Q4 of ADR-0032: a guarantee the person cannot see is one they file as a bug. Plan and
    Implement carry no tooltip normally, so this is the only place the reason can land."""
    (row,) = _drawn([{"mode": "plan", "signing": "implement"}])
    assert "signs its tool calls" in row["why"]
    assert "Implement is assigned to it" in row["why"]


def test_a_pinned_session_still_offers_the_override_that_beats_the_pin():
    """Precedence is in-session act > pin, so taking the menu away would be a lie in the other
    direction — the pick really does win."""
    (row,) = _drawn([{"mode": "plan", "signing": "implement",
                      "pick": "deepseek/deepseek-v3"}])
    assert row["offered"] is True
    assert row["wrote"] == [{"pick": "deepseek/deepseek-v3"}]
    assert row["afterLabel"] == "deepseek/deepseek-v3"


def test_the_default_row_goes_back_to_the_pinned_model_not_the_slots_own():
    """`(default)` calls setBuildModel(null), so it has to mark where routing actually returns to.
    Marking the phase's slot offered a way back to a model the turn would not go back to."""
    keys = _drawn([{"mode": "plan", "signing": "implement"}])[0]["items"]
    labels = [i.get("label") for i in keys if "group" not in i]
    assert f"{SIGNING_MODEL} (default)" in labels
    assert f"{PLAN_MODEL} (default)" not in labels


def test_auto_stops_claiming_two_models_when_only_one_can_run():
    """The worst sentence in the control: "Auto runs X to plan and Y to build" is specific,
    confident and false under the pin."""
    (row,) = _drawn([{"mode": "auto", "signing": "implement"}])
    assert row["label"] == f"{SIGNING_MODEL} · planning"
    assert "to plan and" not in row["why"]
    assert "signs its tool calls" in row["why"]


def test_ask_under_the_pin_names_the_pinned_model_too():
    # Ask shares the harness session with Build, and read tools survive the read-only strip, so an
    # Ask turn signs history like any other.
    (row,) = _drawn([{"mode": "ask", "signing": "implement"}])
    assert row["label"] == SIGNING_MODEL
    assert "signs its tool calls" in row["why"]


def test_no_signing_slot_leaves_every_word_of_the_picker_alone():
    plain, _ = _drawn([{"mode": "auto"}, {"mode": "auto", "signing": None}])
    assert plain["label"] == f"{PLAN_MODEL} · planning"
    assert "to plan and" in plain["why"]
