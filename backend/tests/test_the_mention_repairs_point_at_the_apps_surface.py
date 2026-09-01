"""The mention repairs point at the app's surface (#143, ADR-0021).

WHAT MOVED. A refused @mention arrives with the fix attached (#135), and two of those fixes routed
into the Resource Browser — the one offered when a mention names a Data Source the app holds no
Binding for, and the one offered when a Model API has no stored credential. Both pointed at the
panel because that is where the act used to be. The act is on the Built App's own surface now
(#141), so the signposts follow it.

WHAT THE DATA SOURCE ONE STOPPED NEEDING. A cascade. Its repair had to walk the creator into the
panel and stand a row open, because a Data Source Binding derived its Scope from the cascade
position the creator was standing on (#129) and a card has no position to pass. #142 split those
into two acts, so the repair records the dependency in ONE click — the rule #135 set for every
button on that card — and the Scope is offered afterwards, beside the record's own name in the
Build header.

WHAT THE MODEL API ONE COULD NOT BECOME. The same thing. Sage refuses to record a Model API it
holds no access token for, so the credential is the first half of the fix and the bind is the
second; offering a bind here would offer an act the server is designed to turn down. That one stays
a signpost, and what it points at is the header's own door — beside the sentence saying what that
door will ask for first. The sentence names the Resource and not the app, because a token is stored
per model and outlives any one Binding.

WHAT IS DELIBERATELY UNTOUCHED. `Attach to {app}`. It acts in place rather than navigating
anywhere, so it has no destination to re-point.

Nothing is mounted — see `js/build_header_harness.mjs` for why.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "build_header_harness.mjs"
_JS = Path(__file__).resolve().parents[1] / "sage" / "workbench" / "js"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH (it is in the Sage image)"
)

# `app_c` binds one LLM Alias and nothing else, so every kind below is a Resource this app really
# cannot reach — which is the only state a refusal card is ever drawn in.
ON = {"thread": "thr_many", "select": "app_c"}
APP = "Rate curve viewer"

# The Data Source the app holds no Binding for, and the Model API it holds no credential for.
SOURCE = {"kind": "data_source", "id": "ds_9", "name": "Risk warehouse"}
MODEL_API = {"kind": "model_api", "id": "ma_1", "name": "Churn risk"}


def _run(steps: list[dict]) -> list[dict]:
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


def _fix(entry: dict) -> dict:
    return _run([{"fixMention": entry, **ON}])[-1]


# ---- the Data Source ------------------------------------------------------------------------


@needs_node
def test_the_data_source_repair_records_the_binding_on_the_app_itself():
    """One click, and the dependency is on the app. That is the rule #135 set for every button this
    card draws — only a drop somebody can close in ONE act gets one — and the Data Source could not
    keep it while its Scope was a position in a panel."""
    step = _fix(SOURCE)
    assert step["labels"] == [f"Use in {APP}"]
    # The bare id beside the bare kind, which is what the route resolves. A repair that posted the
    # prefixed `data_source:ds_9` would answer 404 and leave the header redrawing unchanged — a
    # failure shaped exactly like success (#127).
    assert step["posted"] == [{"kind": "data_source", "id": "ds_9"}]
    assert step["bindings"] == ["llm_alias:al_2", "data_source:ds_9"]


@needs_node
def test_the_bind_carries_no_scope_and_the_second_act_is_waiting_on_the_apps_surface():
    """The half #142 made possible. Nothing is asked before the bind, and the part of the source the
    app reads is chosen afterwards — beside the record's own name in the Build header, which is
    where the receipt says to look."""
    step = _fix(SOURCE)
    # No position travelled with the bind, and no walk was opened to collect one.
    assert step["posted"][0].keys() == {"kind", "id"}
    assert step["scoped"] == []
    assert step["walkOpen"] is False
    # And the Binding arrives on the app's own surface in the state the product has a word for.
    assert step["doors"] == [{"after": "Risk warehouse", "label": "not scoped yet"}]


@needs_node
def test_the_receipt_names_the_resource_and_sends_the_reader_to_the_second_act():
    """The sentence is `bindToApp`'s, said in one place because three doors now reach that act
    (ADR-0021). It is the whole of what the repair leaves on screen, so it has to name the thing
    that just moved."""
    said = _fix(SOURCE)["said"]
    assert len(said) == 1
    assert "Risk warehouse" in said[0]
    assert "Choose a Scope beside its name" in said[0]


# ---- the Model API --------------------------------------------------------------------------


@needs_node
def test_the_credential_repair_stands_the_apps_own_door_open():
    """It cannot finish, so it points — and #143 moved what it points at. The door is the header's
    add control, which is where a Model API is added to this app now, and it holds the row this
    mention named."""
    step = _fix(MODEL_API)
    assert step["labels"] == ["Add its access token"]
    assert step["pickerOpen"] is True
    assert any(i["key"] == "model_api:ma_1" for i in step["pickerItems"])


@needs_node
def test_the_credential_repair_offers_no_bind_the_server_would_refuse():
    """The first half of this fix is the token and the second is the bind. A repair that posted one
    would spend the person's click on an act Sage is designed to turn down."""
    step = _fix(MODEL_API)
    assert step["posted"] == []
    assert step["calls"] == []
    assert step["bindings"] == ["llm_alias:al_2"]


@needs_node
def test_the_credential_sentence_names_the_resource_rather_than_the_app():
    """A token is stored per model and outlives any one Binding, so it is not a thing an app owns.
    Every other sentence this card produces names the app; this one must not."""
    said = _fix(MODEL_API)["said"]
    assert len(said) == 1
    assert "Churn risk" in said[0]
    assert APP not in said[0]


# ---- and what neither of them does any more ---------------------------------------------------


@needs_node
@pytest.mark.parametrize("entry", [SOURCE, MODEL_API], ids=["data_source", "model_api"])
def test_neither_repair_opens_the_resource_browser(entry: dict):
    """The panel owns no app scope, so it is not where either act lives. The dock is shut when each
    click happens and it is shut afterwards — a repair that opened it would be pointing at the
    surface the act left."""
    step = _fix(entry)
    assert step["dockTab"] is None
    assert step["panelFilter"] is None
    assert step["walkOpen"] is False


@needs_node
def test_the_file_repair_is_untouched_because_it_navigates_nowhere():
    """`Attach to {app}` puts the bytes on a Dataset, which attaches them to the selected app in the
    same act. It acts in place, so it has no destination to re-point — and it must not grow one."""
    ui = (_JS / "store.js").read_text()
    assert (
        "file: (e) => ({ label: `Attach to ${e.app}`, act: () => store.attachFileForMention(e) })"
        in ui
    )


# ---- the door the two of them now share -------------------------------------------------------


@needs_node
def test_the_header_door_shuts_itself_once_it_has_been_picked_from():
    """What being controlled costs. antd shuts an uncontrolled menu on a click; this one is held
    open from the store so the credential repair can point at it, which means the pick has to put it
    away or the menu stands open over its own receipt."""
    step = _run([{"addIn": True, **ON, "pick": "dataset:as_ticks"}])[-1]
    assert step["posted"] == [{"kind": "dataset", "id": "as_ticks"}]
    assert step["openAfter"] is False
