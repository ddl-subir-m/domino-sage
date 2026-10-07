"""The crossing bar's "Add them" finishes the request a live refusal card is holding (#656).

A Build turn that drops an @mention it cannot use leaves two offers for one fix: the card under the
reply (#213), whose button binds and then sends the refused request again, and the bar above the
composer (#275), whose button bound the same chips and stopped. Somebody who pressed the bar saw the
chips land and then had to resend by hand.

So the bar re-sends exactly when the card would have: a live card, with a `prompt`, for this app,
whose every entry that builds is held once the crossing lands. And it says so in its label, the
same way `mentionFixes` does, so "and build" appears exactly when a build follows.
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
PROMPT = "Compare @haiku and @sonnet on the desk notes"

HAIKU = {"id": "ctx_h", "resourceId": "llm_alias:haiku", "resourceName": "haiku",
         "resourceKind": "llm_alias", "bindingKey": ["llm_alias", "haiku"]}
SONNET = {"id": "ctx_s", "resourceId": "llm_alias:sonnet", "resourceName": "sonnet",
          "resourceKind": "llm_alias", "bindingKey": ["llm_alias", "sonnet"]}
UPLOAD = {"id": "ctx_u", "resourceId": "file:.sage/scratch/uploads/sales.csv",
          "resourceName": "sales.csv", "resourceKind": "file",
          "path": ".sage/scratch/uploads/sales.csv"}

BOTH_BOUND = [{"kind": "llm_alias", "id": "haiku"}, {"kind": "llm_alias", "id": "sonnet"}]


def _entry(kind: str, rid: str, app: dict = APP) -> dict:
    return {"kind": kind, "id": rid, "name": rid.split("/")[-1], "app": app["name"],
            "appId": app["id"]}


def _card(entries: list[dict], *, live: bool = True, prompt: str = PROMPT) -> list[dict]:
    """The Build transcript as the store derives it: the refused turn, its card under the reply."""
    return [
        {"id": "u1", "role": "user", "blocks": [{"type": "text", "value": prompt}]},
        {"id": "a1", "role": "assistant", "blocks": [
            {"type": "text", "value": "I can't use those here."},
            {"type": "mentions_unresolved", "message": "2 mentions were dropped.",
             "entries": entries, "prompt": prompt, "live": live},
        ]},
    ]


ALIASES = [_entry("llm_alias", "haiku"), _entry("llm_alias", "sonnet")]


def _run(cases: list[dict]) -> list[dict]:
    out = subprocess.run(["node", str(_HARNESS)], input=json.dumps({"cases": cases}),
                         check=False, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _press(**case) -> dict:
    case.setdefault("app", APP)
    case.setdefault("press", True)
    return _run([case])[0]


@needs_node
def test_a_bar_click_that_binds_what_the_live_card_needs_sends_the_refused_request_once():
    got = _press(chips=[HAIKU, SONNET], buildMessages=_card(ALIASES), boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["offer"]["label"] == "Add them and build"
    assert got["sent"] == [PROMPT]
    # And nothing is left on the bar to send it a second time.
    assert got["offerAfter"] is None


@needs_node
def test_one_chip_offered_reads_add_it_and_build():
    got = _press(chips=[HAIKU], buildMessages=_card([_entry("llm_alias", "haiku")]),
                 boundAfter=[{"kind": "llm_alias", "id": "haiku"}],
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku"], "refused": []})

    assert got["offer"]["label"] == "Add it and build"
    assert got["sent"] == [PROMPT]


@needs_node
def test_a_chat_upload_the_card_names_is_held_once_the_crossing_attaches_it():
    """A file entry names the Chat path and the app holds it under `public/data/`: the two meet by
    basename, the way `unusableMentions` asks it."""
    got = _press(chips=[UPLOAD],
                 buildMessages=_card([_entry("file", ".sage/scratch/uploads/sales.csv")]),
                 attachedAfter=[{"path": "public/data/sales_2026/uploads/sales.csv"}],
                 answer={"ok": True, "appId": "app_a", "crossed": ["sales.csv"], "refused": []})

    assert got["offer"]["label"] == "Add it and build"
    assert got["sent"] == [PROMPT]


@needs_node
def test_a_refused_chip_sends_nothing():
    got = _press(chips=[HAIKU, SONNET], buildMessages=_card(ALIASES),
                 boundAfter=[{"kind": "llm_alias", "id": "haiku"}],
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku"],
                         "refused": [{"name": "sonnet", "reason": "sonnet stayed in Chat."}]})

    assert got["sent"] == []
    assert got["refusedState"]["byName"] == {"sonnet": "sonnet stayed in Chat."}


@needs_node
def test_any_refusal_sends_nothing_even_when_both_models_landed():
    """The refusal toast is the receipt, and a turn starting under it would bury it. Both models
    are held here, so only the refusal itself can be what holds the send back."""
    got = _press(chips=[HAIKU, SONNET, UPLOAD], buildMessages=_card(ALIASES),
                 boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": [{"name": "sales.csv", "reason": "sales.csv stayed in Chat."}]})

    assert got["offer"]["label"] == "Add them and build"
    assert got["sent"] == []


@needs_node
def test_chips_no_turn_refused_are_added_and_nothing_is_sent():
    got = _press(chips=[HAIKU, SONNET], boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["offer"]["label"] == "Add them"
    assert got["sent"] == []


@needs_node
def test_a_replayed_card_is_a_record_and_sends_nothing():
    got = _press(chips=[HAIKU, SONNET], buildMessages=_card(ALIASES, live=False),
                 boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["offer"]["label"] == "Add them"
    assert got["sent"] == []


@needs_node
def test_a_card_written_before_the_prompt_field_sends_nothing():
    got = _press(chips=[HAIKU, SONNET], buildMessages=_card(ALIASES, prompt=""),
                 boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["offer"]["label"] == "Add them"
    assert got["sent"] == []


@needs_node
def test_a_card_for_another_app_sends_nothing():
    other = {"id": "app_b", "name": "Gong sentiment"}
    got = _press(chips=[HAIKU, SONNET],
                 buildMessages=_card([_entry("llm_alias", "haiku", other),
                                      _entry("llm_alias", "sonnet", other)]),
                 boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["offer"]["label"] == "Add them"
    assert got["sent"] == []


@needs_node
def test_a_crossing_the_server_landed_in_another_app_sends_nothing():
    """The server resolves the target from the live selection, so a switch that lands first crosses
    somewhere else, and the request the card holds is still missing its models here."""
    got = _press(chips=[HAIKU, SONNET], buildMessages=_card(ALIASES), boundAfter=BOTH_BOUND,
                 answer={"ok": True, "appId": "app_b", "crossed": ["haiku", "sonnet"],
                         "refused": []})

    assert got["sent"] == []


@needs_node
def test_a_card_entry_the_crossing_cannot_move_keeps_the_plain_label_and_sends_nothing():
    """The card names @sonnet but no chip carries it, so the click cannot close the gap and a build
    behind it would meet the same refusal."""
    got = _press(chips=[HAIKU], buildMessages=_card(ALIASES),
                 boundAfter=[{"kind": "llm_alias", "id": "haiku"}],
                 answer={"ok": True, "appId": "app_a", "crossed": ["haiku"], "refused": []})

    assert got["offer"]["label"] == "Add it"
    assert got["sent"] == []


@needs_node
def test_once_the_card_has_bound_them_the_bar_has_nothing_to_offer():
    """The other order: the card's own button bound both, so the chips are held and there is no bar
    to press a second time."""
    got = _run([{"app": APP, "chips": [HAIKU, SONNET], "buildMessages": _card(ALIASES),
                 "bindings": BOTH_BOUND}])[0]

    assert got["offer"] is None
