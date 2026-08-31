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
    labels = [i["label"] for i in row["items"] if "group" not in i]
    assert f"{PLAN_MODEL} (default)" in labels
    assert IMPLEMENT_MODEL in labels
    # `ask` points at the plan model in the fixture. The menu offers MODELS, so that is one row,
    # not two identical ones the person has to choose between.
    assert labels.count(f"{PLAN_MODEL} (default)") == 1
    assert len(labels) == 2


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


def test_ask_mode_does_not_offer_an_override():
    """`llm_router` returns ASK_PINNED without ever reading `picked_model`, so a menu here would be
    a control that does nothing. It still says which model answers, and why that is fixed."""
    (row,) = _drawn([{"mode": "ask"}])
    assert row["offered"] is False
    assert row["disabled"] is True
    assert row["label"] == PLAN_MODEL  # the fixture's ask slot
    assert "can't be changed" in row["why"]
    assert "Switch to Plan or Implement" in row["why"]


def test_auto_names_the_phases_model_without_offering_to_change_it():
    """Auto is the other mode the router will not honour a pick in — it answers from the phase, not
    from `picked_model`. Same treatment as Ask, for the same reason."""
    (row,) = _drawn([{"mode": "auto"}])
    assert row["offered"] is False
    assert row["disabled"] is True
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


def test_the_unchangeable_picker_can_actually_be_hovered():
    """A browser dispatches no mouse events on a disabled button, so a Tooltip wrapped straight
    round one never opens — the explanation for every closed state above would be written and
    unreachable. The wrapper is the only thing that makes it a sentence a person can get to."""
    for row in _drawn([{"mode": "ask"}, {"mode": "auto"}, {"mode": "plan", "running": True}]):
        assert row["wrapsDisabledIn"] == "span", row["step"]


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
    silently short of every extra option."""
    (row,) = _drawn([{"health": True}])
    assert row["fetched"] == ["./healthz"]
