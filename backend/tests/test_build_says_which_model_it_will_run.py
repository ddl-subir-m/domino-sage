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
    assert keys == ["deepseek/deepseek-v3", "qwen/qwen-2-5", "openai/gpt-5.4"]
    assert PLAN_MODEL not in keys  # it is already a slot, one row up


def test_the_pick_reaches_set_model():
    """The whole point of restoring the control. `pick` alone, with no `mode` beside it: the mode
    is a standing choice the picker never touched, and ModelControl.pick does not need it."""
    (row,) = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::default"}])
    assert row["wrote"] == [{"pick": "deepseek/deepseek-v3", "pick_effort": None}]
    assert row["serverPick"] == "deepseek/deepseek-v3"
    # And the control now reads back the override rather than the pin it replaced.
    assert row["afterLabel"] == "deepseek/deepseek-v3"


def test_the_default_row_clears_the_override_rather_than_setting_it():
    """`(default)` is the way BACK. Sending the pinned model's id would look identical on screen
    and leave a standing override behind that survives an edit to the deployment's slots."""
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::default"},
                     {"mode": "implement", "pick": "__pinned__"}])
    assert row["wrote"] == [{"pick": None, "pick_effort": None}]
    assert row["serverPick"] is None
    assert row["afterLabel"] == f"{IMPLEMENT_MODEL} (default)"


# ---- the effort submenu (#295, ADR-0049) --------------------------------------------------------
#
# An effort is half of a pick, so the menu offers a level only underneath the model it belongs to
# and one click delivers both. The rule for whether to offer anything at all is the alias's own
# advertised list — the same `length > 0` the Chat chip uses one bar over, because validity is per
# alias and measured, not anything a name predicts.
#
# The fixture's aliases: `claude-planner` and `deepseek-v3` advertise levels, `claude-builder` and
# `qwen-2-5` advertise none.


def _every_row(row: dict) -> list:
    """Every drawable row in the menu, groups flattened and submenu children included.

    Children are the point: `— not accepted` and every other level label exists only down there, so
    a sweep that stops at `items` can only ever report that the top level is clean — which it always
    is, bug or no bug.
    """
    flat = []
    for item in row["items"]:
        for candidate in (item["children"] if "group" in item else [item]):
            flat.append(candidate)
            flat.extend(candidate.get("children") or [])
    return flat


def _children(row: dict, key: str) -> list | None:
    """The submenu under one row, or None where the row has no submenu at all.

    The two answers are different claims — "offers Low and High" and "offers nothing" — and a
    reader that flattened a missing submenu into an empty list would pass over the second.
    """
    for item in row["items"]:
        for candidate in (item["children"] if "group" in item else [item]):
            # `.get`, because a divider is an item too and carries no key of its own.
            if candidate.get("key") == key:
                return candidate.get("children")
    raise AssertionError(f"no row keyed {key}")


def test_build_offers_the_levels_that_survive_beside_tools_not_the_enum():
    """Every Build turn carries function tools, and the send path drops a level the alias will not
    take beside them (`enforcement.py`, `reasoning_efforts_with_tools`). `gpt-5.4` advertises five
    levels and keeps exactly one.

    Offering the enum here is the ticket's own defect arriving through the control it added: nothing
    marks `high` stranded, because it IS in the enum, so the chip would read `gpt-5.4 · High` over a
    turn the shim has quietly put back on the alias's own default. The narrowing is the server's —
    `EFFORTS_WITH_TOOLS` published as its own field — not a second copy of the table in the browser.
    """
    (row,) = _drawn([{"mode": "plan"}])

    assert [c["label"] for c in _children(row, "openai/gpt-5.4")] == ["Model default", "None"]
    # And the levels it advertises but cannot keep are absent, not merely reordered.
    assert "High" not in [c["label"] for c in _children(row, "openai/gpt-5.4")]


def test_a_level_the_alias_advertises_but_drops_beside_tools_is_stranded():
    """The same narrowing, judged the other way round. A level picked while it was offered — or
    arriving from a status poll — must be read against the TOOL-carrying list, or `gpt-5.4 · High`
    reads as fine on a chip whose turn is running at the alias default.

    This is the case the enum cannot see: `high` is perfectly valid for `gpt-5.4` on a request with
    no tools, and there is no such thing as a Build turn without them.
    """
    (row,) = _drawn([{"mode": "plan",
                      "seedPick": {"model": "openai/gpt-5.4", "effort": "high"}}])

    # The chip does not name it: the turn will run at the alias's own default, not at High.
    assert row["label"] == "openai/gpt-5.4"
    # And the menu says so, rather than leaving a level nobody can see still standing.
    stranded = _children(row, "openai/gpt-5.4")[-1]
    assert stranded["label"] == "High — not accepted"
    assert stranded["disabled"] is True


def test_a_row_offers_the_levels_its_own_alias_advertises():
    """Per alias, and measured (ADR-0049). The gateway's own answer for one model says nothing
    about the next one, so the submenu is built from that row's alias and not from a global set."""
    (row,) = _drawn([{"mode": "implement"}])
    # Implement's pin is `claude-builder`, so `claude-planner` is here as a pickable override.
    assert [c["label"] for c in _children(row, PLAN_MODEL)] == [
        "Model default", "Low", "Medium", "High"]
    # A different alias, a different list — read off the row, not off the first one drawn.
    assert [c["label"] for c in _children(row, "deepseek/deepseek-v3")] == [
        "Model default", "Low", "High"]


def test_running_the_alias_at_its_own_default_is_the_first_thing_offered():
    """What every Build pick did before this submenu existed stays the easiest thing to ask for.
    Buried under the levels it would become the thing you have to know to look for, and picking a
    model without choosing a level is still the common case."""
    (row,) = _drawn([{"mode": "implement"}])
    first, *rest = _children(row, PLAN_MODEL)
    assert first == {"key": f"{PLAN_MODEL}::default", "label": "Model default"}
    assert all(c["key"] != f"{PLAN_MODEL}::default" for c in rest)


def test_a_model_that_advertises_no_levels_offers_no_submenu():
    """`efforts.length > 0` — the rule Chat's picker already uses, reused rather than restated. A
    row with a submenu of one entry would promise a choice the alias does not have, and on a
    deployment nobody has probed that is every row."""
    (row,) = _drawn([{"mode": "plan"}])
    assert _children(row, IMPLEMENT_MODEL) is None
    assert _children(row, "qwen/qwen-2-5") is None


def test_the_pinned_row_offers_no_level_because_the_assignment_owns_one():
    """`(default)` is the way BACK: picking it CLEARS the override, and what the slot then runs at
    is its ASSIGNMENT's effort — the Project's standing choice, which belongs to the drawer and not
    to a control that forgets itself on restart (ADR-0017). A submenu here would offer a level to
    the one row that cannot carry one."""
    # Implement's pinned row is `claude-builder`, an alias that advertises no levels at all — so
    # this mode alone could not tell the pinned row's rule from the alias's own silence. Plan's
    # pinned row is `claude-planner`, which advertises three and still offers none here.
    implement, plan = _drawn([{"mode": "implement"}, {"mode": "plan"}])
    assert _children(implement, "__pinned__") is None
    assert _children(plan, "__pinned__") is None
    # The same alias, one row over in the other mode, where it IS pickable: the levels are there.
    assert _children(implement, PLAN_MODEL) is not None


def test_a_barred_row_offers_no_level_either():
    """Under the sensitivity lock (ADR-0043) the row cannot be picked at all, so a submenu beneath
    it would be a door into a wall — and one whose levels read as an offer the lock has refused."""
    (row,) = _drawn([{
        "mode": "implement",
        "sensitivity": {"enabled": True, "locked": True, "group": "approved-for-sensitive",
                        "approved": ["anthropic/claude-builder"], "datasets": ["claims"],
                        "refusal": None, "model": "anthropic/claude-builder", "chat_model": None,
                        "slot_models": {}, "reason": "claims is sensitive"},
        "app": "Claims app", "declaredIn": "binding",
    }])
    assert _children(row, PLAN_MODEL) is None
    assert f"{PLAN_MODEL} — not allowed" in [i.get("label") for i in row["items"]]


def test_picking_a_level_sends_it_with_the_model_it_belongs_to():
    """Criterion 3, at the wire this control actually writes. A submenu that draws correctly and
    sends nothing passes a rendering test and fails the ticket — so what is asserted here is the
    body, not the menu.

    One call carrying both. Two calls would leave a window where a level chosen for one alias is
    standing against another, which is the stale pairing the whole of ADR-0049 is about."""
    (row,) = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"}])
    assert row["wrote"] == [{"pick": "deepseek/deepseek-v3", "pick_effort": "high"}]
    assert row["serverPick"] == "deepseek/deepseek-v3"
    assert row["serverEffort"] == "high"


def test_the_chosen_level_is_marked_on_the_menu_and_the_chip():
    """Criterion 2's browser half: after the click settles, both controls name the level.

    What this does NOT prove, despite an earlier version of this docstring saying so: that the mark
    survives the SERVER's echo. `setBuildModel` writes `state.buildEffort` optimistically before the
    POST, and `applyModelStatus` only overwrites on `'picked_effort' in m` — the same partial-payload
    idiom the model beside it uses — so deleting the key from the fixture's `status()` leaves this
    passing on the optimistic value. The echo is covered where it can actually be observed, in
    `test_the_status_reports_the_level_beside_the_model`, which reads the key off a write's answer
    AND off a plain GET.

    Kept because the two reads here are real: a menu that marked the wrong key, or a chip that
    dropped the level, would fail this and nothing else.
    """
    (row,) = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"}])
    assert row["afterSelected"] == ["deepseek/deepseek-v3::high"]
    # And on the chip, which is the only thing left once the menu closes. A setting with no receipt
    # reads as a setting that was dropped.
    assert row["afterLabel"] == "deepseek/deepseek-v3 · High"


def test_a_pick_left_on_the_model_default_says_nothing_extra_on_the_chip():
    """The level is only worth chip space when somebody chose one. `Model default` is what every
    pick did before this existed, so a chip that announced it would put new words on screen for a
    behaviour that has not changed."""
    (row,) = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::default"}])
    assert row["afterLabel"] == "deepseek/deepseek-v3"
    assert row["afterSelected"] == ["deepseek/deepseek-v3::default"]


def test_a_level_the_model_stopped_offering_is_still_shown_and_still_escapable():
    """A deployment default can move under a live pick, and the measured table can narrow when an
    alias is probed (#280) — so a level somebody is standing on can stop being one the model takes,
    with nobody having done anything wrong.

    Dropped from the menu it would be invisible, still standing, and clearable only by giving up
    the model too. This is the same call `model-assignments.js` makes for the drawer's half, and
    the same label: shown, disabled, named for what it is, with `Model default` right there as the
    way out.
    """
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "narrow": {"alias": "deepseek/deepseek-v3",
                                                 "efforts": ["low"]}}])
    stranded = _children(row, "deepseek/deepseek-v3")[-1]
    assert stranded["label"] == "High — not accepted"
    # Unclickable, and saying why. The label alone is not the guarantee: a row that reads
    # "not accepted" and still sends on click would re-send the exact level it just said is refused.
    assert stranded["disabled"] is True
    assert "doesn't accept this level" in stranded["title"]
    assert [c["label"] for c in _children(row, "deepseek/deepseek-v3")] == [
        "Model default", "Low", "High — not accepted"]


def test_the_chip_does_not_name_a_level_the_turn_will_not_run_at():
    """The chip names what will RUN, and the send path drops a level the resolved alias does not
    accept — so the turn runs at the alias's own default. Naming the stranded level here would be
    the one thing this control must never do: a confident, specific, false sentence.

    The level is not lost by being left off the chip. It is in the submenu one click away, marked.
    """
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "narrow": {"alias": "deepseek/deepseek-v3",
                                                 "efforts": ["low"]}}])
    assert row["label"] == "deepseek/deepseek-v3"


def test_a_model_left_with_no_levels_at_all_still_shows_the_one_being_stood_on():
    """The narrowing that empties the list is the case a `levels.length` gate alone gets wrong: the
    submenu would vanish entirely and take the standing level with it, which is exactly the state
    with no way out."""
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "narrow": {"alias": "deepseek/deepseek-v3",
                                                 "efforts": []}}])
    assert [c["label"] for c in _children(row, "deepseek/deepseek-v3")] == [
        "Model default", "High — not accepted"]


def test_a_model_nobody_is_standing_on_gets_no_stranded_row():
    """`stranded` is about the OVERRIDE's level, not about every row. Without that scope every
    model in the menu would sprout a submenu the moment one level was picked anywhere."""
    _, row = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::high"},
                     {"mode": "implement", "narrow": {"alias": PLAN_MODEL, "efforts": ["low"]}}])
    # The picked model carries it...
    assert [c["label"] for c in _children(row, PLAN_MODEL)][-1] == "High — not accepted"
    # ...and a model nobody picked is a plain row, because it advertises none of its own.
    assert _children(row, "qwen/qwen-2-5") is None


def test_a_row_with_no_levels_still_writes_the_pick_on_its_own():
    """The BARE branch of `onClick` — a row that advertises no levels is a plain item, so its own
    key is what fires, with no `::` in it.

    Every other `pick` step in this file clicks an `X::level` child, so without this the bare branch
    had no payload assertion anywhere: a regression that split a bare id, or sent an invented level
    beside it, would not have reddened a single test.
    """
    (row,) = _drawn([{"mode": "plan", "pick": IMPLEMENT_MODEL}])

    assert row["wrote"] == [{"pick": IMPLEMENT_MODEL, "pick_effort": None}]
    assert row["serverPick"] == IMPLEMENT_MODEL
    assert row["serverEffort"] is None
    assert row["afterLabel"] == IMPLEMENT_MODEL


def test_the_levels_are_read_from_the_resources_listing_when_the_gateway_leg_is_empty():
    """The composer's second source. `gatewayAliases` empty falls back to `resourceGroups.model_llm`,
    which carries `reasoning_efforts` too — `provider.py` builds both from one helper.

    This is the shape a real deployment takes when the gateway leg 40x's, and it is NOT the shape
    the two missing-listing tests above pin: those hold the state before any listing has landed at
    all. Read only through `gatewayAliases`, a regression that dropped `reasoning_efforts` from the
    resources payload would leave every other test in this file green.
    """
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "listing": False, "resourceAliases": True}])

    assert [c["label"] for c in _children(row, "deepseek/deepseek-v3")] == [
        "Model default", "Low", "High"]
    assert row["label"] == "deepseek/deepseek-v3 · High"


def test_a_row_that_carries_no_narrow_list_refuses_nothing():
    """The HIGH from #295's sixth review. An absent FIELD is not an empty list.

    Two producers build `model_llm` rows: the Domino listing, which carries
    `reasoning_efforts_with_tools`, and `rowFromMember`, which builds from the project's membership
    file and (for rows written before the field existed) does not. On the fallback path the row
    EXISTS while the field does not, so a guard that only checks the row would read every level as
    refused — the chip silently dropping a level the router really is sending, and a tooltip saying
    the turn runs at the model default while it runs at High.

    That is the confident, specific, false sentence this control must never produce, arrived at from
    an absence of evidence rather than from evidence. `undefined` means nobody answered; `[]` means
    the alias answered "none".
    """
    (row,) = _drawn([{"mode": "plan", "listing": False, "resourceAliases": "legacy",
                      "seedPick": {"model": "deepseek/deepseek-v3", "effort": "high"}}])

    # The level stands, because nothing has said this alias refuses it.
    assert row["label"] == "deepseek/deepseek-v3 · High"
    # And no row anywhere claims a refusal it has no evidence for.
    assert not any("not accepted" in (r.get("label") or "") for r in _every_row(row))
    assert "doesn't accept" not in (row["why"] or "")


def test_the_listing_mapper_passes_an_absent_narrow_list_through_untouched():
    """The membership leg's contract, asserted on the OTHER leg and through the real mapper.

    Both legs feed `model_llm`, and the composer prefers `gatewayAliases` whenever it is non-empty,
    so a gateway listing whose rows lack the narrow field is never rescued by the membership rows
    behind it. A `|| []` here alone would give one absence two opposite answers decided by which
    listing happened to reply: unanswered on one leg, "refuses every level" on the other.

    Driven through `SW.api.resourceListing` rather than by seeding the store, because the mapper is
    the thing under test — seeding steps over the only line that can get this wrong, and a test that
    steps over its subject stays green when the subject breaks.

    `undefined` means nobody said; `[]` means the alias said none. The key is therefore ABSENT here,
    not empty — JSON drops an undefined value, which is exactly the distinction being kept.
    """
    (row,) = _drawn([{"listingRoute": True}])
    mapped = row["row"]

    assert mapped["reasoning_efforts"] == ["low", "high"]
    assert "reasoning_efforts_with_tools" not in mapped


def test_the_gateway_leg_reads_an_absent_narrow_list_as_no_evidence():
    """And what the menu does with such a row: nothing. No refusal, no dropped level.

    The composer's half of the same contract, on the leg it prefers.
    """
    (row,) = _drawn([{"mode": "plan", "listing": "legacy",
                      "seedPick": {"model": "deepseek/deepseek-v3", "effort": "high"}}])

    assert row["label"] == "deepseek/deepseek-v3 · High"
    assert not any("not accepted" in (r.get("label") or "") for r in _every_row(row))


def test_a_missing_alias_listing_is_not_read_as_a_refusal():
    """The listing absent and the alias advertising nothing read identically off
    `reasoning_efforts` — both are `[]` — and they are opposite facts. `gatewayAliases` starts empty
    and a gateway leg that 40x's at boot leaves it that way, so this is the state a Workbench opens
    in rather than an exotic one.

    Read as a refusal, a level the model takes perfectly well gets marked `— not accepted` and the
    chip drops it: the confident, specific, false sentence the chip's own rule forbids, arrived at
    through an absence of evidence. `store.js`'s `kept()` draws the same line for the drawer's half
    — an alias the listing did not carry is a prediction with no evidence, not evidence of a drop.
    """
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "listing": False}])

    # The chip still names the level, because nothing has said the model refuses it.
    assert row["label"] == "deepseek/deepseek-v3 · High"


def test_a_missing_listing_puts_no_refusal_in_the_menu_either():
    """The menu's half of the rule above, asserted apart from the chip's because they hang off two
    different reads and one plant only reds the first of them. With no listing there is nothing to
    offer, so the row is plain — what it must not be is a row stating a refusal it has no evidence
    for."""
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "listing": False}])

    assert _children(row, "deepseek/deepseek-v3") is None
    # Over every row AND every child. `— not accepted` is only ever produced as a CHILD label, so a
    # sweep of `items` alone passes whether or not the bug is present — it is the shape of assertion
    # this file has caught twice now, written a third time by the author who caught them.
    assert not any("not accepted" in (r.get("label") or "") for r in _every_row(row))


def test_the_listing_coming_back_restores_the_submenu():
    """The absent-listing row is a waiting state, not a dead end: once the aliases land the level is
    offered again, marked as the selection it already was."""
    _, _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                        {"mode": "plan", "listing": False},
                        {"mode": "plan", "listing": True}])

    assert [c["label"] for c in _children(row, "deepseek/deepseek-v3")] == [
        "Model default", "Low", "High"]
    assert row["label"] == "deepseek/deepseek-v3 · High"


def test_clearing_the_override_clears_its_level_with_it():
    """Criterion 5's browser half. The way back sends no level, so the slot's own ASSIGNMENT effort
    is what applies from the next turn — not the one that was picked for a model nobody is on any
    more, and not none."""
    _, row = _drawn([{"mode": "plan", "pick": "deepseek/deepseek-v3::high"},
                     {"mode": "plan", "pick": "__pinned__"}])
    assert row["wrote"] == [{"pick": None, "pick_effort": None}]
    assert row["serverEffort"] is None
    assert row["afterSelected"] == ["__pinned__"]


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


def test_a_pick_made_in_implement_does_not_leak_into_auto():
    """`ModelControl.set_mode` never clears `_picked_model` — a pick made while Implement is the
    standing mode survives the switch to Auto, because the router itself needs no help forgetting
    it: `_resolve_build` only reads `picked_model` in Plan and Implement (ADR-0017's own words,
    "Auto follows phase"). The picker's `override` must forget it too, or the chip goes on naming a
    model nobody assigned to this phase and the assignments drawer never mentions."""
    _, row = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::default"}, {"mode": "auto"}])
    assert row["offered"] is False
    assert row["label"] == f"{IMPLEMENT_MODEL} · building"


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


def test_an_override_naming_the_pinned_model_is_not_mis_marked_in_the_menu():
    """Pick Plan's model while in Implement, then switch to Plan: the override now names the model
    Plan is already pinned to. Read as an override it would mark no row selected at all, because the
    menu keys that model as the way-back row rather than under its own id.

    This also used to assert the LABEL read `(default)`, on the premise that such a pick is "running
    exactly the default". That premise died when per-slot efforts landed (#281/#282) and nobody
    noticed: the router answers `plan-override` with `effort=None` where the unpicked slot answers
    `plan-pinned` with `effort=catalog.plan_effort`. Same model, different level, one label. What
    the chip says now is two tests down.
    """
    _, row = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::default"}, {"mode": "plan"}])
    assert row["selectedKeys"] == ["__pinned__"]


def test_a_pick_that_collapses_still_names_the_level_it_runs_at():
    """The collapse above is right about the MODEL and wrong about the LEVEL.

    Pick plan's model at `High` while in Implement, then switch to Plan: `override` collapses,
    because the model really is the slot's — but the router still returns PLAN_OVERRIDE carrying
    `picked_effort`, so the turn runs at High while the assignments drawer says the slot's own
    level applies. `(default)` over that would have the control claim the slot's setting while the
    turn runs the person's, which is this ticket's defect arriving through the one door the menu
    cannot mark.

    The menu's mark is left on `__pinned__` deliberately and that row does clear the pick, so the
    selection is incomplete rather than false. The chip is the half that has to be true; what the
    way-back row should mean once it can also carry a level is #310.
    """
    _, row = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::high"}, {"mode": "plan"}])
    assert row["label"] == f"{PLAN_MODEL} · High"
    assert row["selectedKeys"] == ["__pinned__"]
    # And the tooltip accounts for it, because the menu cannot: the way-back row is marked and
    # carries no submenu (#310), so this is the only place the level can be explained or its exit
    # named. The stranded twin had a sentence from the start; this, the commoner case, had none.
    assert "at High, not at the assignment's level" in row["why"]
    assert "clears it" in row["why"]


def test_a_collapsed_pick_whose_level_is_stranded_claims_no_default():
    """The two special cases meeting: the pick names the mode's own pinned model AND its level has
    stopped being one that model takes.

    Neither arm of the chip fits. The level will not run, so naming it would be false — and
    `(default)` is a claim about the SLOT'S ASSIGNMENT, which is not what routes either: a live pick
    makes the decision an OVERRIDE, the shim drops the refused level, and the turn lands on the
    ALIAS's default, a third thing again. So the chip names the model and stops.

    The tooltip carries what the label cannot, because this is the one stranded level with nowhere
    to be drawn: the way-back row has no submenu (#310), so without the sentence the person is told
    nothing at all about a level they set and the turn is not running.
    """
    *_, row = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::high"},
                      {"mode": "plan"},
                      {"mode": "plan", "narrow": {"alias": PLAN_MODEL, "efforts": ["low"]}}])

    assert row["label"] == PLAN_MODEL
    assert "(default)" not in row["label"]
    assert "doesn't accept High" in row["why"]
    assert "runs at the model default" in row["why"]


def test_only_an_unpicked_slot_is_allowed_to_call_itself_the_default():
    """One rule behind the shapes above: `(default)` is a claim about the SLOT'S ASSIGNMENT — its
    model and its effort together — so it may only be made where that assignment is what routes.

    Any live pick makes the decision an OVERRIDE, and an override carries the PICK's effort, never
    `catalog.<slot>_effort`. Measured rather than argued: with `plan_effort="low"`, a pick naming
    plan's own model resolves `plan-override … effort=None` while the unpicked slot resolves
    `plan-pinned … effort=low`. Same model, same label until now, a different level.

    So a collapsed pick reads as a bare name — true, and silent about a level it does not have —
    and only a slot nobody has picked keeps the suffix.
    """
    implement, collapsed = _drawn([{"mode": "implement", "pick": f"{PLAN_MODEL}::default"},
                                   {"mode": "plan"}])
    # Implement's own row is untouched by a pick that has not happened yet.
    assert implement["label"] == f"{IMPLEMENT_MODEL} (default)"
    # Plan's row carries a live pick naming its own model: a bare name, no claim about the slot.
    assert collapsed["label"] == PLAN_MODEL

    # And with no pick anywhere, the suffix is back — this is the one shape that earns it.
    (clean,) = _drawn([{"mode": "plan"}])
    assert clean["label"] == f"{PLAN_MODEL} (default)"


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
    assert "required for this session" in row["why"]
    assert "every Build turn uses it" in row["why"]


def test_a_pinned_session_still_offers_the_override_that_beats_the_pin():
    """Precedence is in-session act > pin, so taking the menu away would be a lie in the other
    direction — the pick really does win."""
    (row,) = _drawn([{"mode": "plan", "signing": "implement",
                      "pick": "deepseek/deepseek-v3::default"}])
    assert row["offered"] is True
    assert row["wrote"] == [{"pick": "deepseek/deepseek-v3", "pick_effort": None}]
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
    assert "required for this session" in row["why"]


def test_ask_under_the_pin_names_the_pinned_model_too():
    # Ask shares the harness session with Build, and read tools survive the read-only strip, so an
    # Ask turn signs history like any other.
    (row,) = _drawn([{"mode": "ask", "signing": "implement"}])
    assert row["label"] == SIGNING_MODEL
    assert "required for this session" in row["why"]


def test_a_running_turn_still_accounts_for_the_pin():
    """Both sentences are true at once, and the running one used to win `title` outright — so the
    hover that happens mid-build, which is the moment somebody looks at this chip, gave no account
    of the pin at all (#276). The closed control still explains itself; it now explains both."""
    (row,) = _drawn([{"mode": "plan", "signing": "implement", "running": True}])
    assert row["disabled"] is True
    assert "required for this session" in row["why"]
    assert "This turn is running on" in row["why"]


def test_an_override_that_beat_the_pin_takes_the_pin_s_sentence_with_it():
    """Precedence is in-session act > pin — `_pin_signing` hands a PLAN_OVERRIDE decision straight
    back — so once a pick is in, the chip names the pick and the pin's sentence is false. It used to
    be shown anyway, which had the control contradict itself in two consecutive sentences (#276)."""
    _, after = _drawn([{"mode": "plan", "signing": "implement",
                        "pick": "deepseek/deepseek-v3::default"},
                       {"mode": "plan", "running": True}])
    assert after["label"] == "deepseek/deepseek-v3"
    assert "required for this session" not in (after["why"] or "")
    assert "This turn is running on deepseek/deepseek-v3" in after["why"]


def test_a_barred_pick_mid_turn_leaves_the_pin_unnamed():
    """The lock outranks the pin here exactly as it does on the open control: under the lock the pin
    is not what moved this model, and a confident sentence naming the wrong cause is worse than one
    fewer sentence."""
    (row,) = _drawn([{
        "mode": "plan", "signing": "implement", "running": True,
        "sensitivity": {"enabled": True, "locked": True, "group": "approved-for-sensitive",
                        "approved": ["sovereign/plan"], "datasets": ["claims"],
                        "refusal": None, "model": "sovereign/plan", "chat_model": None,
                        "slot_models": {}, "reason": ""},
        "app": "Claims app", "declaredIn": "binding",
    }])
    assert "required for this session" not in row["why"]
    assert "This turn is running on" in row["why"]


def test_no_signing_slot_leaves_every_word_of_the_picker_alone():
    plain, _ = _drawn([{"mode": "auto"}, {"mode": "auto", "signing": None}])
    assert plain["label"] == f"{PLAN_MODEL} · planning"
    assert "to plan and" in plain["why"]


# --- The sensitivity lock, in the picker (ADR-0043) -----------------------------------------------
#
# A declared Dataset in scope narrows the models the router will use, and the picker's job is to say
# so BEFORE somebody picks. Two things could go wrong quietly and both are asserted here: a row that
# is closed without a reason (the house rule a disabled control must explain itself), and a session
# moved onto another model with nothing said about it.

# `sovereign/plan` is the approved model in this fixture, because `_lock_preferences` prefers the
# sovereign slots and the harness's catalog already carries them.
APPROVED = "sovereign/plan"


def _locked(**over):
    lock = {"enabled": True, "locked": True, "group": "FDE_models",
            "approved": [APPROVED], "datasets": ["claims"], "refusal": None}
    lock.update(over)
    return lock


def _flat(items):
    """Every row the menu offers, groups opened out and the divider dropped — it carries no key and
    no label, so JSON leaves it an empty object."""
    rows = [c for i in items for c in (i["children"] if "group" in i else [i])]
    return [r for r in rows if r.get("key")]


def test_a_model_outside_the_approved_group_is_closed_and_says_why():
    """The reason has to travel with the row. A greyed model with no sentence over it is the defect
    this exists to prevent — the person is left to guess whether it is broken, gone, or refused."""
    (row,) = _drawn([{"mode": "plan", "sensitivity": _locked()}])
    barred = next(i for i in _flat(row["items"]) if i["key"] == IMPLEMENT_MODEL)

    assert barred["disabled"] is True
    assert "not allowed" in barred["label"]
    assert IMPLEMENT_MODEL in barred["title"]
    # Names the Dataset that closed it, so the explanation points at something to go and look at.
    assert "claims" in barred["title"]


def test_the_way_back_to_the_default_is_never_closed():
    """The pinned row carries no model id — picking it CLEARS the override — so closing it would
    strand somebody on the very override they are trying to leave."""
    (row,) = _drawn([{"mode": "plan", "pick": IMPLEMENT_MODEL},
                     {"mode": "plan", "sensitivity": _locked()}])[1:]
    pinned = next(i for i in _flat(row["items"]) if i["key"] == "__pinned__")

    assert not pinned.get("disabled")


def test_an_unlocked_project_closes_nothing_and_says_nothing():
    """The overwhelmingly common case, and the one a governance feature must not tax. Nothing about
    the picker changes for a Project with no declared Dataset in scope."""
    (row,) = _drawn([{"mode": "plan"}])

    assert [i for i in _flat(row["items"]) if i.get("disabled")] == []
    assert row["lockNotice"] is None


def test_the_session_moving_onto_an_approved_model_is_said_once():
    """Switch and tell them. Switching in silence fails the confidence the promise is meant to
    build, and refusing the turn would stop somebody mid-task to teach what a sentence can teach."""
    (row,) = _drawn([{"mode": "plan", "sensitivity": _locked()}])

    assert row["lockNotice"], "the lock moved the session and drew no notice"
    assert PLAN_MODEL in row["lockNotice"]      # what it moved off
    assert APPROVED in row["lockNotice"]        # what it moved to
    assert "Allowed models" in row["lockNotice"]


def _pointed(**over):
    """A locked step with the app on screen holding the declared Dataset as a Binding."""
    step = {"mode": "plan", "sensitivity": _locked(), "app": "Claims Explorer",
            "declaredIn": "binding"}
    step.update(over)
    return step


def test_the_notice_names_the_dataset_in_the_picker_s_own_words():
    """One promise, two surfaces (#264). The picker row has named the Dataset since it was
    written and the notice named the KIND, so a creator who read both was told a declared Dataset
    was the reason twice and which one once. Both now read `declaredPhrase`, which is the only way
    they cannot drift: an anonymous notice beside a named row is the same defect as the chip that
    named a model it would not run."""
    (row,) = _drawn([_pointed()])
    barred = next(i for i in _flat(row["items"]) if i["key"] == IMPLEMENT_MODEL)

    assert "the Dataset claims" in row["lockNotice"]
    assert "the Dataset claims" in barred["title"]


def test_the_notice_points_at_the_list_that_owns_the_dataset():
    """A pointer names its destination in the words the reader will see on arrival (ADR-0011), and
    names the app because a Project holds many (ADR-0008). The lock's scope IS the selected app's,
    so the list that can lift it is that app's own — not the Project rail beside it.

    Drawn for either record that list holds and offers a removal for: a Binding and an Attachment
    both put the rows in front of the model (ADR-0043) and both can be taken back out here."""
    binding, attached = _drawn([_pointed(), _pointed(declaredIn="attachment")])

    for row in (binding, attached):
        assert "App dependencies" in row["lockNotice"]
        assert "Remove it from Claims Explorer" in row["lockNotice"]

    # Two declared Datasets arm the same lock, and the pointer is pronouning the pair the sentence
    # above it just named. "Remove it" there is the one word in this notice a reader stops at.
    (both,) = _drawn([_pointed(sensitivity=_locked(datasets=["claims", "members"]))])
    assert "the Datasets claims and members" in both["lockNotice"]
    assert "Remove them from Claims Explorer" in both["lockNotice"]


def test_chat_gets_the_pointer_with_the_surface_named():
    """Chat is where the failure this feature exists to fix was found — "the lock stayed on, in Chat,
    naming no Dataset, with its only removal control in Build" (ADR-0048). Naming the Dataset and
    then withholding the route fixes the middle clause and reproduces the last one, so Chat carries
    the pointer and names the surface it is sending the reader to.

    Not the same sentence as Build's: naming the mode already on screen reads as a correction, so
    only the composer that has to cross says which way."""
    chat, build = _drawn([_pointed(chat=True), _pointed()])

    assert "In Build, remove it from Claims Explorer under App dependencies" in chat["lockNotice"]
    assert "In Build" not in build["lockNotice"]
    # The condition is the records, not the surface — so Chat withholds it on the same terms Build
    # does rather than on easier ones.
    (chip,) = _drawn([_pointed(chat=True, declaredIn="chip")])
    assert "App dependencies" not in chip["lockNotice"]


def test_a_lock_the_app_list_cannot_fully_release_is_pointed_nowhere():
    """A pointer at a list that cannot release the lock is the dead end ADR-0011 closes, not a
    weaker way of closing it. Four ways that happens, and the rule is one rule — EVERY declared
    Dataset holding the lock has to be on the app's own list:

      - a `dsfile:` chip, which arms the lock from the Conversation and writes neither a Binding nor
        an Attachment (`confirm_thread_dataset_file`). The mode is no guide to this: the
        Conversation travels into Build through the handoff.
      - MIXED doors — the app binds `claims` while the Conversation pins `members`. "Any" would
        answer yes here and the notice would say "Remove them" of a pair half of which is absent;
        the creator removes one, comes back, and the lock has not moved.
      - a pre-manifest Attachment carrying no `dataset_id`, which `_rehydrate_attached` still will
        not write (ADR-0048), so nothing here can join it to the declaration.
      - no app selected at all: no app, no list.

    The switch is announced in every one of them. Only the destination is withheld."""
    chip, mixed, legacy, none = _drawn([
        _pointed(declaredIn="chip"),
        _pointed(sensitivity=_locked(datasets=["claims", "members"]), appDatasets=["claims"]),
        _pointed(declaredIn="attachment-legacy"),
        _pointed(app=None),
    ])

    for row in (chip, mixed, legacy, none):
        assert row["lockNotice"], "the switch is still announced"
        assert "claims" in row["lockNotice"]
        assert "App dependencies" not in row["lockNotice"]


def test_the_sticky_lock_names_no_dataset_and_points_nowhere_near_the_data():
    """The case ADR-0043 argued anonymity for, and it survives #264 unchanged. The rows are in the
    transcript, the Dataset has already gone, and naming it would send somebody to remove something
    that is not there — after which the lock has not moved. The only way out is a new chat, so that
    is the only way out offered.

    `datasets` is empty because that is the only shape the server sends with `reason: "session"` —
    a live declaration wins the sentence while there is one. So the claim under test is the pair
    that IS reachable: no pointer at the data, and the new chat in its place."""
    (row,) = _drawn([_pointed(sensitivity=_locked(datasets=[], reason="session"))])

    assert "App dependencies" not in row["lockNotice"]
    assert "Start a new chat" in row["lockNotice"]


def test_a_pick_that_was_already_approved_is_not_announced_as_a_switch():
    """Nothing moved, so there is nothing to say. A notice on every locked turn would be the
    "don't show me this again" the house rules warn about, arriving by a different route."""
    (row,) = _drawn([{"mode": "plan", "sensitivity": _locked(approved=[PLAN_MODEL])}])

    assert row["lockNotice"] is None


def test_an_approved_set_that_resolves_to_nothing_refuses_rather_than_announcing_a_move():
    """The dead end. Every model is closed and the server's own sentence — which names WHICH of the
    four ways the set came back empty, and who to ask — is what each closed row carries. No switch
    notice, because nothing was switched to."""
    refusal = "The LLM Alias group FDE_models has no models in it."
    (row,) = _drawn([{"mode": "plan", "sensitivity": _locked(approved=[], refusal=refusal)}])
    offered = [i for i in _flat(row["items"]) if i["key"] not in ("__pinned__", "__assignments__")]

    assert offered and all(i["disabled"] for i in offered)
    assert all(i["title"] == refusal for i in offered)
    assert row["lockNotice"] is None
